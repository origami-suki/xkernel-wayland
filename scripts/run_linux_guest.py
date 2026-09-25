#!/usr/bin/env python3
"""Run the same Wayland work disk under its bundled Linux kernel in snapshot mode.

Only the copied initramfs entry point changes: switch_root starts a diagnostic
shell instead of the rootfs init/login service. No filesystem mounts on the host
or modifications to the base disk are needed.
"""

import argparse
import gzip
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
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

from frame_capture import capture_frames

ROOT = Path(__file__).resolve().parents[1]
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


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def git_state(path):
    return {"commit": command(["git", "rev-parse", "HEAD"], path),
            "status": command(["git", "status", "--porcelain=v1"], path)}


def prepare_boot(disk, output):
    boot = output / "linux"
    boot.mkdir()
    extraction = []
    for name in ("vmlinuz-lts", "initramfs-lts"):
        destination = boot / name
        argv = ["debugfs", "-R", f"dump /boot/{name} {destination}", str(disk)]
        extraction.append(shlex.join(argv) + "\n" + command(argv))
        require(destination.is_file() and destination.stat().st_size > 0,
                f"missing /boot/{name} in work disk")
    (output / "boot-extraction.log").write_text("\n".join(extraction) + "\n")
    source = (boot / "vmlinuz-lts").read_bytes()
    image = None
    offset = 0
    while True:
        offset = source.find(b"\x1f\x8b\x08", offset)
        require(offset >= 0, "no gzip-compressed ARM64 Image in vmlinuz-lts")
        try:
            candidate = zlib.decompress(source[offset:], wbits=31)
            if candidate[0x38:0x3c] == b"ARM\x64":
                image = candidate
                break
        except zlib.error:
            pass
        offset += 1
    (boot / "Image").write_bytes(image)

    # Rewrite only the init entry in the newc archive. Preserve every other
    # entry byte-for-byte, including modes, owners, symlinks and compressed ko's.
    original = gzip.decompress((boot / "initramfs-lts").read_bytes())
    pos = 0
    modified = bytearray()
    replacements = 0
    while pos + 110 <= len(original):
        header = bytearray(original[pos:pos + 110])
        require(header[:6] == b"070701", "expected newc initramfs")
        size = int(header[54:62], 16)
        namesize = int(header[94:102], 16)
        name_end = pos + 110 + namesize
        name = original[pos + 110:name_end - 1]
        data_start = (name_end + 3) & ~3
        end = (data_start + size + 3) & ~3
        if name in (b"init", b"./init"):
            data = original[data_start:data_start + size]
            old = b"switch_root /sysroot /sbin/init"
            require(data.count(old) == 1, "unexpected original initramfs init entry point")
            (boot / "init.original").write_bytes(data)
            data = data.replace(old, b"switch_root /sysroot /bin/sh")
            (boot / "init.diagnostic").write_bytes(data)
            header[54:62] = f"{len(data):08x}".encode()
            modified.extend(header + original[pos + 110:data_start] + data)
            modified.extend(b"\0" * ((-len(modified)) % 4))
            replacements += 1
        else:
            modified.extend(original[pos:end])
        pos = end
        if name == b"TRAILER!!!":
            break
    require(replacements == 1, "expected exactly one initramfs init entry")
    modified.extend(b"\0" * ((-len(modified)) % 512))
    (boot / "initramfs-diagnostic.gz").write_bytes(gzip.compress(modified, mtime=0))
    return {"gzip_payload_offset": offset,
            "provenance": "Linux kernel and initramfs extracted from the same work disk /boot",
            "init_change": "switch_root target /sbin/init -> /bin/sh; no rootfs file changed",
            "debug_elf": "not supplied by rootfs; original vmlinuz and full derived Image preserved",
            "sha256": {p.name: sha256(p) for p in sorted(boot.iterdir())}}


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
    raise TimeoutError(f"socket connection timeout: {path}")


def monitor_command(client, log, text=None):
    if text is not None:
        log.write(("\nHOST COMMAND: " + text + "\n").encode())
        client.sendall((text + "\n").encode())
    received = bytearray()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            data = client.recv(65536)
        except socket.timeout:
            continue
        if not data:
            break
        log.write(data)
        log.flush()
        received.extend(data)
        if b"(qemu) " in received:
            break
    return bytes(received)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="linux-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "-" + uuid.uuid4().hex[:6])
    parser.add_argument("--disk", type=Path, default=ROOT / "work/images/wayland-m0.img")
    parser.add_argument("--guest-commands", type=Path,
                        help="POSIX shell commands in a subshell; nonzero final status fails the runner")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.run_id), "invalid run ID")
    require(0 < args.timeout <= 3600, "timeout must be in (0, 3600]")
    disk = args.disk.resolve(strict=True)
    require(disk.is_relative_to((ROOT / "work/images").resolve()) and disk.is_file()
            and disk.stat().st_nlink == 1 and "audit" not in disk.name.lower()
            and "," not in str(disk), "expected independent work/images disk, never Assets/audit")
    extra = args.guest_commands.read_text() if args.guest_commands else "true\n"
    require("\x00" not in extra and len(extra.encode()) <= 8192,
            "guest commands must be text no larger than 8 KiB")
    output = ROOT / "artifacts/runs" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__), output / "run_linux_guest.py")
    shutil.copy2(Path(__file__).with_name("frame_capture.py"), output / "frame_capture.py")
    state = {"run_id": args.run_id, "started_utc": datetime.now(timezone.utc).isoformat(),
             "integration": git_state(ROOT), "xkernel_reference": git_state(ROOT / "sources/x-kernel"),
             "host": {"uname": list(platform.uname()), "python": sys.version,
                      "cpuinfo": Path("/proc/cpuinfo").read_text(),
                      "meminfo": Path("/proc/meminfo").read_text()},
             "runner_sha256": sha256(output / "run_linux_guest.py"),
             "frame_capture_sha256": sha256(output / "frame_capture.py"),
             "disk": {"path": str(disk), "snapshot": True}, "timeout_seconds": args.timeout,
             "policy": "AArch64 TCG thread=multi, 2 GiB, 4 vCPU; same PCI devices as x-kernel runner; offline"}
    print(f"Evidence: {output}", flush=True)
    process = serial = monitor = sockets = None
    result = 1
    try:
        state["qemu_version"] = command([QEMU, "--version"])
        require(re.search(r"version 11\.1\.1(?:\s|$)", state["qemu_version"]),
                "expected QEMU 11.1.1 at /usr/bin/qemu-system-aarch64")
        state["disk"]["sha256_before"] = sha256(disk)
        state["linux"] = prepare_boot(disk, output)
        sockets = tempfile.TemporaryDirectory(prefix="xklinux-", dir="/tmp")
        socket_dir = Path(sockets.name)
        argv = [QEMU, "-machine", "virt,gic-version=3", "-cpu", "cortex-a76",
                "-accel", "tcg,thread=multi", "-m", "2g", "-smp", "4",
                "-kernel", str(output / "linux/Image"),
                "-initrd", str(output / "linux/initramfs-diagnostic.gz"),
                "-append", "console=ttyAMA0 root=/dev/vda rw", "-snapshot",
                "-drive", f"id=disk0,if=none,format=raw,file={disk}",
                "-device", "virtio-blk-pci,drive=disk0", "-object", "rng-random,id=host_rng0",
                "-device", "virtio-rng-pci,rng=host_rng0", "-device", "virtio-gpu-pci",
                "-device", "virtio-keyboard-pci", "-device", "virtio-mouse-pci",
                "-nic", "none", "-vga", "none", "-display", "none",
                "-chardev", f"socket,id=serial0,path={socket_dir}/serial,server=on,wait=off,"
                f"logfile={output}/serial.log", "-serial", "chardev:serial0",
                "-monitor", f"unix:{socket_dir}/monitor,server=on,wait=off", "-no-reboot"]
        state["argv"] = argv
        (output / "command.sh").write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n")
        token = uuid.uuid4().hex
        ready = f"__LINUX_READY_{token}__"
        done = f"__LINUX_DONE_{token}__"
        script = ("(\nuname -a\ntest \"$(getconf _NPROCESSORS_ONLN)\" = 4 || exit 98\n"
                  + extra + "\n)\nlinux_rc=$?\n"
                  + f"printf '\\n%s%s%d\\n' '__LINUX_DONE_' '{token}__' \"$linux_rc\"\n")
        (output / "guest-commands.sh").write_text(script)
        state["guest_commands_sha256"] = sha256(output / "guest-commands.sh")
        write_json(output / "metadata.json", state)
        deadline = time.monotonic() + args.timeout
        with (output / "qemu.log").open("wb") as qemu_log, (output / "monitor.log").open("wb") as mon_log:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=qemu_log, stderr=subprocess.STDOUT)
            state["qemu_pid"] = process.pid
            serial = connect(socket_dir / "serial", process, min(deadline, time.monotonic() + 15))
            monitor = connect(socket_dir / "monitor", process, min(deadline, time.monotonic() + 15))
            monitor_command(monitor, mon_log)
            actual = Path(f"/proc/{process.pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            state["actual_argv"] = [os.fsdecode(part) for part in actual]
            require(state["actual_argv"] == argv, "live QEMU argv differs from plan")
            for query in ("info status", "info version", "info qtree"):
                require(b"(qemu) " in monitor_command(monitor, mon_log, query), "monitor query incomplete")
            phase = "boot"
            serial_path = output / "serial.log"
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    serial.recv(65536)
                except socket.timeout:
                    pass
                raw = serial_path.read_text(errors="replace") if serial_path.exists() else ""
                clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw).replace("\r", "")
                if phase == "boot" and re.search(r"(?:^|\n)[^\n]*[#$] $", clean):
                    serial.sendall((f"printf '\\n%s%s\\n' '__LINUX_READY_' '{token}__'\n").encode())
                    phase = "handshake"
                if phase == "handshake" and ready in clean.splitlines():
                    require("Linux version 6.12.110-0-lts" in clean, "unexpected Linux kernel release")
                    state["shell_ready"] = True
                    serial.sendall(script.encode())
                    phase = "commands"
                if phase == "commands":
                    capture_frames(clean, output, lambda command: monitor_command(monitor, mon_log, command), state)
                completed = re.search(r"(?m)^" + re.escape(done) + r"(\d+)$", clean)
                if phase == "commands" and completed:
                    state["guest_exit_code"] = int(completed[1])
                    serial.sendall(b"sync; poweroff -f\n")
                    state["stop_requested"] = "guest-poweroff-f"
                    phase = "shutdown"
                time.sleep(0.02)
            if process.poll() is None:
                state["exit_reason"] = "timeout-" + phase
                monitor_command(monitor, mon_log, "quit")
            else:
                state["exit_reason"] = state.get("stop_requested", "unexpected-qemu-exit")
            process.wait(timeout=5)
            state["qemu_exit_code"] = process.returncode
            result = int(not (phase == "shutdown" and state.get("guest_exit_code") == 0
                             and process.returncode == 0 and "timeout" not in state["exit_reason"]))
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
        for client in (serial, monitor):
            if client is not None:
                client.close()
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
