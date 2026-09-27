#!/usr/bin/env python3
"""Verify host EGL and QEMU virgl initialization without booting a guest."""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from qemu_graphics import graphics_options
from run_guest import Monitor, QEMU, ROOT, connect, git_state
from frame_capture import capture_frames


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graphics = graphics_options("virgl", args.render_node)
    node = args.render_node.resolve(strict=True)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    state = {"scope": "host EGL and QEMU initialization; no guest kernel, rootfs or browser",
             "guest_acceleration_verified": False,
             "integration": git_state(ROOT),
             "render_node_requested": str(args.render_node), "render_node": str(node),
             "graphics_environment": {name: os.environ.get(name) for name in
                                      ("GALLIUM_THREAD", "mesa_glthread", "MESA_LOADER_DRIVER_OVERRIDE",
                                       "LIBGL_ALWAYS_SOFTWARE", "EGL_PLATFORM", "DRI_PRIME")},
             "commands": []}
    process = None

    def run(label, argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        (output / (label + ".log")).write_text(
            shlex.join([str(arg) for arg in argv]) + "\n" + result.stdout + result.stderr)
        state["commands"].append({"label": label, "argv": [str(a) for a in argv],
                                  "exit_code": result.returncode})
        result.check_returncode()
        return result.stdout

    try:
        for file in (Path(__file__), ROOT / "scripts/qemu_graphics.py",
                     ROOT / "tests/drm/host-egl.c"):
            shutil.copy2(file, output / file.name)
        state["qemu_version"] = run("qemu-version", [QEMU, "--version"])
        run("packages", ["pacman", "-Q", "qemu-system-aarch64", "qemu-common",
                         "qemu-hw-display-virtio-gpu-gl",
                         "qemu-hw-display-virtio-gpu-pci-gl", "qemu-ui-egl-headless",
                         "qemu-ui-opengl", "virglrenderer", "mesa"])
        flags = shlex.split(run("egl-build-flags", ["pkg-config", "--cflags", "--libs",
                                                    "egl", "gbm", "gl"]))
        run("egl-build", ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O2",
                          output / "host-egl.c", "-o", output / "host-egl", *flags])
        state["host_renderer_output"] = run("host-egl", [output / "host-egl", node])
        driver = Path("/sys/class/drm") / node.name / "device/driver"
        state["host_driver"] = str(driver.resolve(strict=True))
        with tempfile.TemporaryDirectory(prefix="xk-virgl-host-") as temporary:
            socket_dir = Path(temporary)
            argv = [QEMU, "-machine", "virt,gic-version=3", "-cpu", "cortex-a76",
                    "-accel", "tcg,thread=multi", "-m", "2g", "-smp", "4",
                    "-S", "-nodefaults", "-nic", "none", "-vga", "none",
                    *graphics, "-serial", "none",
                    "-monitor", f"unix:{socket_dir}/monitor,server=on,wait=off",
                    "-no-reboot"]
            state["argv"] = argv
            (output / "command.sh").write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n")
            with (output / "qemu.log").open("wb") as qemu_log, \
                    (output / "monitor.log").open("wb") as monitor_log:
                process = subprocess.Popen(argv, stdout=qemu_log, stderr=subprocess.STDOUT)
                state["qemu_pid"] = process.pid
                client = connect(socket_dir / "monitor", process, time.monotonic() + 15)
                try:
                    monitor = Monitor(client, monitor_log)
                    state["actual_argv"] = [os.fsdecode(a) for a in
                                            Path(f"/proc/{process.pid}/cmdline").read_bytes()
                                            .rstrip(b"\0").split(b"\0")]
                    if state["actual_argv"] != argv:
                        raise ValueError("live QEMU arguments differ")
                    monitor.send("info status")
                    qtree = monitor.send("info qtree")
                    if b"virtio-gpu-gl" not in qtree:
                        raise ValueError("virgl device absent from live QEMU tree")
                    (output / "qemu-maps.txt").write_bytes(
                        Path(f"/proc/{process.pid}/maps").read_bytes())
                    fds = []
                    for file in Path(f"/proc/{process.pid}/fd").iterdir():
                        try:
                            fds.append(os.readlink(file))
                        except FileNotFoundError:
                            continue
                    state["qemu_open_fds"] = sorted(fds)
                    if str(node) not in fds:
                        raise ValueError("QEMU did not open the selected host render node")
                    capture_frames("__ICT_FRAME_host-only__\n", output, monitor.send, state)
                    monitor.send("quit")
                    process.wait(timeout=5)
                    state["qemu_exit_code"] = process.returncode
                    if process.returncode != 0:
                        raise ValueError("QEMU failed on shutdown")
                finally:
                    client.close()
        state["status"] = "passed-host-only"
        result = 0
    except (Exception, KeyboardInterrupt) as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        result = 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            state["qemu_exit_code"] = process.returncode
        state["sha256"] = {file.name: digest(file) for file in sorted(output.iterdir())
                           if file.is_file() and file.name != "metadata.json"}
        (output / "metadata.json").write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps({"status": state["status"], "output": str(output),
                      "error": state.get("error")}))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
