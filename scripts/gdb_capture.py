#!/usr/bin/env python3
"""Request a bounded GDB snapshot from a running run_guest.py --gdb session."""

import argparse
import hashlib
import json
import math
import re
import shlex
import struct
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def elf_identity(path):
    """Hash allocated sections and source symbols, excluding generated build notes.

    The boot ELF's note payloads are finalized after the debug ELF is copied.
    Comparing code alone would miss stale data layouts or source line tables.
    """
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:6] != b"\x7fELF\x02\x01":
            raise ValueError(f"not a little-endian ELF64: {path}")
        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        if fields[2] != 183 or fields[11] != 64 or not 0 < fields[12] < 65536:
            raise ValueError(f"unsupported AArch64 ELF section table: {path}")
        stream.seek(fields[6])
        table = stream.read(fields[11] * fields[12])
        sections = list(struct.iter_unpack("<IIQQQQIIQQ", table))
        names = sections[fields[13]]
        stream.seek(names[4])
        strings = stream.read(names[5])
        identity = {}
        for section in sections:
            name = strings[section[0]:].split(b"\0", 1)[0].decode()
            if name in (".note.xkernel.build-info", ".note.gnu.build-id"):
                continue
            if not (section[2] & 2 or name.startswith(".debug_")
                    or name in (".symtab", ".strtab")):
                continue
            digest = hashlib.sha256()
            if section[1] != 8:  # SHT_NOBITS has an address and size, no file bytes.
                stream.seek(section[4])
                remaining = section[5]
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError(f"truncated ELF section {name}: {path}")
                    digest.update(chunk)
                    remaining -= len(chunk)
            identity[name] = {"type": section[1], "flags": section[2],
                              "address": section[3], "size": section[5],
                              "sha256": digest.hexdigest()}
        for name in (".text", ".debug_info", ".debug_line", ".symtab"):
            if not identity.get(name, {}).get("size"):
                raise ValueError(f"missing nonempty {name}: {path}")
        return {"entry": fields[4], "sections": identity}


def verify_debug_elf(boot, debug):
    identity = elf_identity(boot)
    if identity != elf_identity(debug):
        raise ValueError("debug ELF does not match boot ELF code/data/addresses/source symbols")
    return identity


def validate_request(request):
    timeout = request.get("timeout", 45)
    expressions = request.get("expressions", [])
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("GDB timeout must be in (0, 60] seconds")
    if not isinstance(expressions, list) or len(expressions) > 16:
        raise ValueError("at most 16 expressions are supported")
    for expression in expressions:
        if (not isinstance(expression, str) or not expression.strip() or len(expression) > 512
                or any(ord(char) < 32 for char in expression)):
            raise ValueError("expressions must be nonempty single lines of at most 512 characters")
    return timeout, expressions


def gdb_script(socket_path, result_path, expressions):
    commands = ["info threads", "thread apply all bt 24",
                "thread apply all info registers " + " ".join(f"x{i}" for i in range(31))
                + " sp pc cpsr", "thread apply all x/12i $pc"]
    commands += ["print " + expression for expression in expressions]
    # Python repr keeps user expressions inside string literals, not GDB commands.
    return "\n".join([
        "set pagination off", "set confirm off", "set print elements 64",
        "set print repeats 8", "set remotetimeout 5", "set tcp connect-timeout 5",
        "set may-write-memory off", "set may-write-registers off",
        "set may-call-functions off", "python", "import gdb, json",
        f"gdb.execute('target remote ' + {str(socket_path)!r})",
        "results = []", f"for command in {commands!r}:",
        "    try:", "        output = gdb.execute(command, to_string=True)",
        "        results.append({'command': command, 'output': output, 'ok': True})",
        "        gdb.write('\\n>>> ' + command + '\\n' + output)",
        "    except gdb.error as error:",
        "        results.append({'command': command, 'error': str(error), 'ok': False})",
        "        gdb.write('\\nERROR: ' + command + ': ' + str(error) + '\\n')",
        f"with open({str(result_path)!r}, 'w') as stream:",
        "    json.dump(results, stream, indent=2)",
        "gdb.execute('disconnect')", "end", "quit", ""])


class DebugSession:
    def __init__(self, output, socket_path, gdb, state):
        self.output = output / "debug"
        self.output.mkdir()
        self.requests = self.output / "requests"
        self.requests.mkdir()
        self.socket = socket_path
        self.elf = output / "bundle/kernel.debug.elf"
        self.gdb = gdb
        self.state = state
        state["debug"] = {"enabled": True, "socket": str(socket_path),
                          "gdb": gdb, "snapshots": [], "excluded_seconds": 0.0,
                          "performance_sample_valid": False,
                          "thread_meaning": "QEMU vCPUs, not guest OS threads"}

    def qemu_args(self):
        return ["-chardev", f"socket,id=gdb0,path={self.socket},server=on,wait=off",
                "-gdb", "chardev:gdb0"]

    def capture(self, name, request, monitor):
        directory = self.output / name
        directory.mkdir()
        result = {"name": name, "request": request, "status": "failed",
                  "started_utc": datetime.now(timezone.utc).isoformat()}
        started = time.monotonic()
        was_running = None
        restored = False
        try:
            timeout, expressions = validate_request(request)
            before = monitor.send("info status").decode(errors="replace")
            if "VM status: running" in before:
                was_running = True
            elif "VM status: paused" in before:
                was_running = False
            else:
                raise RuntimeError(f"unexpected QEMU status: {before}")
            result["was_running"] = was_running
            monitor.send("stop")
            if b"VM status: paused" not in monitor.send("info status"):
                raise RuntimeError("QEMU did not pause")
            result["paused_monotonic"] = time.monotonic()
            script = directory / "capture.gdb"
            script.write_text(gdb_script(self.socket, directory / "commands.json", expressions))
            argv = [self.gdb, "-q", "-nx", "-nh", "-batch", "-iex", "set auto-load off",
                    str(self.elf), "-x", str(script)]
            result["argv"] = argv
            (directory / "command.sh").write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n")
            with (directory / "gdb.log").open("wb") as log:
                process = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            result["gdb_exit_code"] = process.returncode
            result["paused_after_gdb"] = b"VM status: paused" in monitor.send("info status")
            if not result["paused_after_gdb"]:
                raise RuntimeError("GDB unexpectedly resumed the guest before monitor restoration")
            commands = json.loads((directory / "commands.json").read_text())
            result["status"] = ("passed" if process.returncode == 0
                                and all(item["ok"] for item in commands) else "partial")
        except (Exception, KeyboardInterrupt) as error:
            result["error"] = f"{type(error).__name__}: {error}"
            if isinstance(error, KeyboardInterrupt):
                raise
        finally:
            # A failed GDB, including a killed process, must not strand the VM.
            try:
                if was_running is not None:
                    monitor.send("cont" if was_running else "stop")
                    expected = b"VM status: running" if was_running else b"VM status: paused"
                    restored = expected in monitor.send("info status")
            except Exception as error:
                result["restore_error"] = str(error)
            result["restored"] = restored
            result["finished_monotonic"] = time.monotonic()
            result["excluded_seconds"] = result["finished_monotonic"] - started
            result["finished_utc"] = datetime.now(timezone.utc).isoformat()
            if not restored:
                result["status"] = "failed"
            write_json(directory / "result.json", result)
            self.state["debug"]["snapshots"].append(result)
            self.state["debug"]["excluded_seconds"] += result["excluded_seconds"]
        if was_running is not None and not restored:
            raise RuntimeError("could not restore QEMU after GDB capture; see debug result")
        return result["excluded_seconds"]

    def service(self, monitor):
        # Process at most one request per guest-loop iteration to preserve fairness.
        for path in sorted(self.requests.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
                continue
            if (self.output / path.stem).exists():
                continue
            try:
                request = json.loads(path.read_text())
            except (ValueError, OSError) as error:
                request = {"expressions": None, "parse_error": str(error)}
            return self.capture(path.stem, request, monitor)
        return 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="live run's evidence directory")
    parser.add_argument("--expression", action="append", default=[], help="GDB print expression; repeatable")
    parser.add_argument("--timeout", type=float, default=45, help="GDB subprocess limit, maximum 60 seconds")
    args = parser.parse_args()
    request = {"expressions": args.expression, "timeout": args.timeout}
    validate_request(request)
    output = args.run.resolve(strict=True)
    state = json.loads((output / "metadata.json").read_text())
    if not state.get("debug", {}).get("enabled") or "finished_utc" in state:
        raise ValueError("run must be live and started with --gdb")
    name = uuid.uuid4().hex
    write_json(output / "debug/requests" / (name + ".json"), request)
    result_path = output / "debug" / name / "result.json"
    print(f"Snapshot: {result_path.parent}", flush=True)
    deadline = time.monotonic() + args.timeout + 30
    while time.monotonic() < deadline:
        if result_path.exists():
            result = json.loads(result_path.read_text())
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else 1
        state = json.loads((output / "metadata.json").read_text())
        if "finished_utc" in state:
            raise RuntimeError("run ended before snapshot completed")
        time.sleep(0.1)
    raise TimeoutError(f"snapshot still queued or running; inspect {result_path}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, TimeoutError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
