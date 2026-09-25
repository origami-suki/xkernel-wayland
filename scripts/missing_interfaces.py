#!/usr/bin/env python3
"""Inventory visible unsupported-interface messages without inferring causality.

This is a log index, not a syscall tracer. Quiet ENOSYS paths and successful
no-ops cannot be discovered from these messages. Application operation names
are not necessarily the actual AArch64 syscall names.
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
KERNEL_SYSCALL = re.compile(
    r"(?:Unimplemented syscall:|Unsupported syscall requested dummy fd:)\s*(\w+)"
)
FCNTL = re.compile(r"unsupported fcntl parameters:\s*cmd:\s*(-?\d+)")
PRCTL = re.compile(r"sys_prctl: unsupported option\s+(\d+)")
OTHER_UNSUPPORTED = re.compile(
    r"Function not implemented|\bENOSYS\b|Operation not supported|Not supported \(95\)"
    r"|unsupported ioctl|Unsupported ioctl"
)


def collect(serial_path):
    """Keep every matching line reference; repeated log exports are not calls."""
    serial_path = Path(serial_path)
    exists = serial_path.is_file()
    raw = serial_path.read_bytes() if exists else b""
    entries = {}
    for number, raw_line in enumerate(raw.decode(errors="replace").splitlines(), 1):
        line = ANSI.sub("", raw_line).replace("\x00", "\\0")
        syscall = KERNEL_SYSCALL.search(line)
        fcntl = FCNTL.search(line)
        prctl = PRCTL.search(line)
        if syscall:
            key = ("kernel_syscall", syscall[1], None)
        elif fcntl:
            key = ("kernel_subcommand", "fcntl", int(fcntl[1]))
        elif prctl:
            key = ("kernel_subcommand", "prctl", int(prctl[1]))
        elif OTHER_UNSUPPORTED.search(line):
            # Preserve the operation and errno text without guessing a syscall
            # from a libc wrapper, Chromium source filename, or log prefix.
            message = re.sub(r"^.*?\b(?:ERROR|WARNING):[^\]]*\]\s*", "", line)
            key = ("unattributed_operation", message, None)
        else:
            continue
        entry = entries.setdefault(key, {
            "source": key[0], "interface_or_message": key[1], "subcommand": key[2],
            "implementation_required": None, "impact": "not_assessed",
            "line_numbers": [], "observed_pids": [], "observed_tids": [], "examples": [],
        })
        entry["line_numbers"].append(number)
        tid = re.search(r"\(tid=(\d+)\)", line)
        if tid and int(tid[1]) not in entry["observed_tids"]:
            entry["observed_tids"].append(int(tid[1]))
        process = re.search(r"\[(\d+):(\d+):", line)
        if process:
            for field, value in (("observed_pids", int(process[1])),
                                 ("observed_tids", int(process[2]))):
                if value not in entry[field]:
                    entry[field].append(value)
        if line not in entry["examples"]:
            entry["examples"].append(line)
    return {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "serial_log": serial_path.name,
        "serial_exists": exists,
        "serial_bytes": len(raw),
        "serial_sha256": hashlib.sha256(raw).hexdigest() if exists else None,
        "coverage": "visible log messages only; not a syscall trace or implementation audit",
        "limitations": [
            "Quiet ENOSYS returns and successful no-op implementations may be absent.",
            "Application log operations may not name the underlying syscall.",
            "Repeated observation tails and raw exports may repeat one event; line counts are not call counts.",
            "Absence of entries does not prove interface compatibility or a complete application log.",
        ],
        "entries": list(entries.values()),
    }


def write_inventory(output):
    """Write a new derived index; never replace an existing evidence file."""
    output = Path(output)
    report = collect(output / "serial.log")
    destination = output / "missing-interfaces.json"
    with destination.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"file": destination.name, "entry_count": len(report["entries"]),
            "serial_exists": report["serial_exists"],
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(write_inventory(args.run_directory), ensure_ascii=False))
