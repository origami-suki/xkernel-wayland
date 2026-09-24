#!/usr/bin/env python3
"""Run the pinned AArch64 guest, check its shell, and preserve bounded evidence.

Python 3.11+ and the build's rust-objcopy are required. Images must be independent
files under work/images. Snapshot mode is always used: guest writes are discarded.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "sources/x-kernel"
QEMU = "/usr/bin/qemu-system-aarch64"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(argv, cwd=ROOT):
    return subprocess.check_output(argv, cwd=cwd, stderr=subprocess.STDOUT,
                                   text=True, timeout=120).strip()


def git_state(directory):
    return {"commit": command(["git", "rev-parse", "HEAD"], directory),
            "status": command(["git", "status", "--porcelain=v1"], directory)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def check_disk(path):
    path = path.resolve(strict=True)
    require(path.is_relative_to((ROOT / "work/images").resolve()),
            "disk must resolve inside work/images; Assets and audit images are forbidden")
    require(path.is_file() and path.stat().st_nlink == 1,
            "disk must be an independent regular file, not a hard link")
    require("audit" not in path.name.lower(), "audit images must remain read-only")
    require("," not in str(path), "disk path may not contain a QEMU option separator (,)")
    return path


def check_bundle(bundle, output, state):
    manifest_path = bundle / "bundle.toml"
    manifest = tomllib.loads(manifest_path.read_text())
    info = manifest["build-info"]
    require(manifest["format-version"] == 6 and not manifest["unittest"],
            "expected a v0.2.0 format-6 normal kernel bundle")
    require(info["arch"] == "aarch64" and info["platform"] == "kplat-aarch64",
            "expected an AArch64 QEMU bundle")
    require(info["nr-cpus-max"] == 4 and not info["vmm-enabled"],
            "bundle must configure 4 CPUs without KFEAT_VMM")
    require(info["git-commit"] == state["kernel"]["commit"],
            "bundle commit differs from current kernel HEAD; build first")
    build_id = manifest["build-id"]
    require(re.fullmatch(r"[0-9a-f]{64}", build_id), "invalid SHA-256 Build ID")
    saved = output / "bundle"
    saved.mkdir()
    shutil.copy2(manifest_path, saved / "bundle.toml")
    shutil.copy2(KERNEL / ".config", output / "kernel.config")
    hashes = {}
    for key, name in (("kernel-elf", "kernel.elf"), ("kernel-image", "kernel.bin")):
        require(manifest[key]["path"] == name, f"unexpected {key} path")
        source = bundle / name
        require(source.stat().st_size == manifest[key]["size"], f"{name} size mismatch")
        shutil.copy2(source, saved / name)
        hashes[name] = sha256(saved / name)
    notes = command(["readelf", "-n", str(saved / "kernel.elf")])
    (output / "elf-notes.txt").write_text(notes + "\n")
    ids = re.findall(r"Build ID:\s*([0-9a-f]+)", notes)
    require(ids == [build_id], "ELF GNU Build ID differs from bundle manifest")
    # Use the exact raw-image operation from xkmake/src/build.rs. The local
    # existing_bundle implementation only checks ELF existence, despite its docs.
    rebuilt = output / "kernel.rebuilt.bin"
    objcopy_argv = ["rust-objcopy", "--binary-architecture=aarch64",
                    str(saved / "kernel.elf"), "--strip-all", "-O", "binary", str(rebuilt)]
    (output / "objcopy-validation.txt").write_text(
        shlex.join(objcopy_argv) + "\n" + command(objcopy_argv, KERNEL) + "\n")
    require(sha256(rebuilt) == hashes["kernel.bin"], "kernel.bin does not match bundle ELF")
    rebuilt.unlink()
    config = (output / "kernel.config").read_text()
    for line in ('ARCH="aarch64"', 'MACHINE="qemu"', "NR_CPUS=4",
                 "KFEAT_VIRTIO_BUS_PCI=y", "KFEAT_DRIVER_VIRTIO_BLK=y",
                 "KFEAT_DRIVER_VIRTIO_GPU=y", "KFEAT_DRIVER_VIRTIO_INPUT=y",
                 "KFEAT_DRIVER_VIRTIO_RNG=y"):
        require(line in config.splitlines(), f"configuration missing {line}")
    state["bundle"] = {"source": str(bundle), "build_info": info, "build_id": build_id,
                       "sha256": hashes, "config_file_sha256": sha256(output / "kernel.config"),
                       "validation": "manifest sizes, ELF GNU note, objcopy ELF/bin equality; "
                       "config copied with required platform/device symbols; no source-freshness claim"}
    return saved / "kernel.bin", build_id


def connect(path, process, deadline):
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(path))
            client.settimeout(0.2)
            return client
        except (FileNotFoundError, ConnectionRefusedError):
            client.close()
            require(process.poll() is None, "QEMU exited before socket connection")
            time.sleep(0.05)
    raise TimeoutError(f"timed out connecting to {path.name}")


class Monitor:
    def __init__(self, client, logfile):
        self.client = client
        self.logfile = logfile
        self.read_prompt()

    def read_prompt(self):
        received = bytearray()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                data = self.client.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            self.logfile.write(data)
            self.logfile.flush()
            received.extend(data)
            if b"(qemu) " in received:
                break
        return bytes(received)

    def send(self, text):
        self.logfile.write(("\nHOST COMMAND: " + text + "\n").encode())
        self.logfile.flush()
        self.client.sendall((text + "\n").encode())
        return self.read_prompt()


def marker_command(kind, token, argument=""):
    # The complete marker is absent from the sent command, so terminal echo
    # cannot satisfy the exact-line match. printf expands the adjacent pieces.
    return f"printf '\\n%s%s%s\\n' '__XK_{kind}_' '{token}__' {shlex.quote(argument)}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "-" + uuid.uuid4().hex[:6])
    parser.add_argument("--disk", type=Path, default=ROOT / "work/images/xkernel-run.img")
    parser.add_argument("--bundle", type=Path,
                        default=KERNEL / "target/xkmake/kplat-aarch64/release")
    parser.add_argument("--timeout", type=float, default=240,
                        help="maximum guest runtime in seconds, excluding validation/hashing")
    parser.add_argument("--guest-commands", type=Path,
                        help="POSIX shell script run after smoke, inside a subshell; its status is checked")
    parser.add_argument("--monitor-stop", action="store_true",
                        help="after successful smoke/probe, stop with monitor quit instead of PID1 exit")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.run_id), "invalid run ID")
    require(0 < args.timeout <= 3600, "timeout must be in (0, 3600] seconds")
    disk = check_disk(args.disk)
    extra = args.guest_commands.read_text() if args.guest_commands else ""
    require("\x00" not in extra and len(extra.encode()) <= 1024,
            "guest script must be text no larger than 1 KiB; invoke longer scripts already on disk")
    output = ROOT / "artifacts/runs" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__), output / "run_guest.py")
    print(f"Evidence: {output}", flush=True)
    state = {"run_id": args.run_id, "started_utc": datetime.now(timezone.utc).isoformat(),
             "integration": git_state(ROOT), "kernel": git_state(KERNEL),
             "host": {"uname": list(platform.uname()), "python": sys.version,
                      "cpuinfo": Path("/proc/cpuinfo").read_text(),
                      "meminfo": Path("/proc/meminfo").read_text()},
             "runner_sha256": sha256(output / "run_guest.py"),
             "disk": {"path": str(disk), "snapshot": True},
             "timeout_seconds": args.timeout,
             "policy": "TCG thread=multi, 2 GiB, 4 vCPU; NET and VSOCK omitted for offline "
                       "serial/monitor checks; no DHCP, host forwarding or vhost dependency",
             "mode": "monitor-stop" if args.monitor_stop else "pid1-exit"}
    process = serial = monitor = None
    result = 1
    sockets = None
    try:
        state["qemu_version"] = command([QEMU, "--version"])
        require(re.search(r"version 11\.1\.1(?:\s|$)", state["qemu_version"]),
                "QEMU must be /usr/bin/qemu-system-aarch64 version 11.1.1")
        kernel_image, build_id = check_bundle(args.bundle.resolve(), output, state)
        state["disk"]["sha256_before"] = sha256(disk)
        sockets = tempfile.TemporaryDirectory(prefix="xkm0-", dir="/tmp")
        socket_dir = Path(sockets.name)
        argv = [QEMU, "-machine", "virt,gic-version=3", "-cpu", "cortex-a76",
                "-accel", "tcg,thread=multi", "-m", "2g", "-smp", "4",
                "-kernel", str(kernel_image), "-snapshot",
                "-drive", f"id=disk0,if=none,format=raw,file={disk}",
                "-device", "virtio-blk-pci,drive=disk0", "-object", "rng-random,id=host_rng0",
                "-device", "virtio-rng-pci,rng=host_rng0", "-device", "virtio-gpu-pci",
                "-device", "virtio-keyboard-pci", "-device", "virtio-mouse-pci",
                "-nic", "none", "-vga", "none", "-display", "none",
                "-chardev", f"socket,id=serial0,path={socket_dir}/serial,server=on,wait=off,"
                f"logfile={output}/serial.log", "-serial", "chardev:serial0",
                "-monitor", f"unix:{socket_dir}/monitor,server=on,wait=off", "-no-reboot"]
        state["argv"] = argv
        write_json(output / "metadata.json", state)
        (output / "command.sh").write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n")
        token = uuid.uuid4().hex
        ready = f"__XK_READY_{token}__"
        done = f"__XK_DONE_{token}__"
        smoke = (f"uname -a && ls -la / && p=/tmp/xkernel-m0-{token} && "
                 "printf '%s\\n' 'xkernel-m0-read-write' > \"$p\" && "
                 "cat \"$p\" && test \"$(cat \"$p\")\" = xkernel-m0-read-write && "
                 "rm \"$p\" && test ! -e \"$p\"")
        script = "(\n" + smoke + "\nsmoke_rc=$?\n"
        script += "if [ \"$smoke_rc\" -ne 0 ]; then exit \"$smoke_rc\"; fi\n"
        script += extra + "\n)\nxk_rc=$?\n"
        script += f"printf '\\n%s%s%d\\n' '__XK_DONE_' '{token}__' \"$xk_rc\"\n"
        (output / "guest-commands.sh").write_text(script)
        deadline = time.monotonic() + args.timeout
        with (output / "qemu.log").open("wb") as qemu_log, \
                (output / "monitor.log").open("wb") as monitor_log:
            process = subprocess.Popen(argv, cwd=KERNEL, stdout=qemu_log, stderr=subprocess.STDOUT)
            state["qemu_pid"] = process.pid
            serial = connect(socket_dir / "serial", process, min(deadline, time.monotonic() + 15))
            monitor = Monitor(connect(socket_dir / "monitor", process,
                                      min(deadline, time.monotonic() + 15)), monitor_log)
            # /proc/cmdline can briefly be empty during exec. A monitor greeting
            # proves QEMU has finished startup before inspecting its live argv.
            actual_argv = Path(f"/proc/{process.pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            state["actual_argv"] = [os.fsdecode(part) for part in actual_argv]
            require(state["actual_argv"] == argv, "live QEMU argv differs from planned argv")
            write_json(output / "metadata.json", state)
            for query in ("info status", "info version", "info qtree"):
                response = monitor.send(query)
                require(b"(qemu) " in response, f"monitor did not finish {query}")
            phase = "boot"
            serial_path = output / "serial.log"
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    serial.recv(65536)  # Drain the channel; the chardev logfile is authoritative.
                except socket.timeout:
                    pass
                data = serial_path.read_text(errors="replace") if serial_path.exists() else ""
                clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", data).replace("\r", "")
                if phase == "boot" and re.search(r"(?:^|\n)[^\n]*[#$] $", clean):
                    serial.sendall((marker_command("READY", token) + "\n").encode())
                    phase = "handshake"
                if phase == "handshake" and ready in clean.splitlines():
                    require(f"build_id = {build_id}" in clean,
                            "booted guest does not print the verified Build ID")
                    require(re.search(r"(?m)^smp = 4$", clean), "guest did not report 4 CPUs")
                    state["shell_ready"] = True
                    serial.sendall(script.encode())
                    phase = "commands"
                completed = re.search(r"(?m)^" + re.escape(done) + r"(\d+)$", clean)
                if phase == "commands" and completed:
                    state["guest_exit_code"] = int(completed[1])
                    dump = output / "screendump.ppm"
                    response = monitor.send("screendump " + json.dumps(str(dump)))
                    state["screendump"] = {"exists": dump.is_file(),
                        "bytes": dump.stat().st_size if dump.is_file() else 0,
                        "interpretation": "monitor capture only; existence is not guest rendering evidence",
                        "monitor_response": response.decode(errors="replace")}
                    if args.monitor_stop:
                        monitor.send("quit")
                        state["stop_requested"] = "monitor-quit"
                    else:
                        serial.sendall(b"exit \"$xk_rc\"\n")
                        state["stop_requested"] = "pid1-exit-for-kernel-sync-poweroff"
                    phase = "shutdown"
                time.sleep(0.02)
            if process.poll() is None:
                state["exit_reason"] = "timeout-" + phase
                monitor.send("quit")
            else:
                state["exit_reason"] = (state.get("stop_requested", "unexpected-qemu-exit"))
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                state["cleanup"] = "terminate-after-monitor-quit"
                process.wait(timeout=5)
            state["qemu_exit_code"] = process.returncode
            result = 0 if (phase == "shutdown" and state.get("guest_exit_code") == 0
                           and process.returncode == 0 and "timeout" not in state["exit_reason"]) else 1
    except (Exception, KeyboardInterrupt) as error:
        state["exit_reason"] = f"{type(error).__name__}: {error}"
        print(state["exit_reason"], file=sys.stderr)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            state["qemu_exit_code"] = process.returncode
        if serial is not None:
            serial.close()
        if monitor is not None:
            monitor.client.close()
        if sockets is not None:
            sockets.cleanup()
        if "sha256_before" in state["disk"]:
            state["disk"]["sha256_after"] = sha256(disk)
            state["disk"]["unchanged"] = state["disk"]["sha256_before"] == state["disk"]["sha256_after"]
            if not state["disk"]["unchanged"]:
                result = 1
        state["finished_utc"] = datetime.now(timezone.utc).isoformat()
        state["runner_exit_code"] = result
        write_json(output / "metadata.json", state)
    print(f"{'PASS' if result == 0 else 'FAIL'} {args.run_id}: {state['exit_reason']}", flush=True)
    return result


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
