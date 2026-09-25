"""Failure recovery and evidence binding for host-side GDB capture."""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gdb_capture import DebugSession, validate_request, verify_debug_elf


def elf_fixture(path, text=b"code", lines=b"line", note=b"note", address=0x1000):
    entries = [(".text", 1, 6, address, text), (".debug_info", 1, 0, 0, b"debug"),
               (".debug_line", 1, 0, 0, lines), (".symtab", 2, 0, 0, b"symbol"),
               (".note.gnu.build-id", 7, 2, 0x2000, note)]
    names = b"\0.shstrtab\0" + b"".join(name.encode() + b"\0" for name, *_ in entries)
    data = bytearray(64)
    sections = [(0,) * 10]
    for name, kind, flags, addr, contents in [(".shstrtab", 3, 0, 0, names)] + entries:
        sections.append((names.index(name.encode()), kind, flags, addr, len(data),
                         len(contents), 0, 0, 1, 0))
        data.extend(contents)
    offset = len(data)
    for section in sections:
        data.extend(struct.pack("<IIQQQQIIQQ", *section))
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF\x02\x01" + b"\0" * 10,
                            2, 183, 1, 0x1000, 0, offset, 0, 64, 0, 0, 64, len(sections), 1)
    path.write_bytes(data)


class FakeMonitor:
    def __init__(self, running=True, fail_restore=False):
        self.running = running
        self.fail_restore = fail_restore
        self.commands = []

    def send(self, command):
        self.commands.append(command)
        if command == "cont" and not self.fail_restore:
            self.running = True
        if command == "stop":
            self.running = False
        return b"VM status: " + (b"running" if self.running else b"paused") + b"\n(qemu) "


class GdbCaptureTests(unittest.TestCase):
    def test_debug_elf_rejects_same_size_wrong_code_lines_or_addresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            boot, debug = Path(tmp) / "boot", Path(tmp) / "debug"
            elf_fixture(boot)
            elf_fixture(debug, note=b"other build note")
            verify_debug_elf(boot, debug)
            for change in ({"text": b"oops"}, {"lines": b"oops"}, {"address": 0x2000}):
                elf_fixture(debug, **change)
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, "does not match"):
                    verify_debug_elf(boot, debug)

    def test_timeout_recovers_running_guest_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {}
            session = DebugSession(Path(tmp), Path(tmp) / "socket", "/usr/bin/gdb", state)
            monitor = FakeMonitor()
            with patch("gdb_capture.subprocess.run", side_effect=subprocess.TimeoutExpired("gdb", 0.1)):
                elapsed = session.capture("timeout", {"timeout": 0.1}, monitor)
            result = json.loads((Path(tmp) / "debug/timeout/result.json").read_text())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["restored"])
            self.assertTrue(monitor.running)
            self.assertIn("TimeoutExpired", result["error"])
            self.assertGreater(elapsed, 0)
            self.assertEqual(state["debug"]["excluded_seconds"], elapsed)

    def test_failed_gdb_does_not_resume_a_previously_paused_guest(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = DebugSession(Path(tmp), Path(tmp) / "socket", "/usr/bin/gdb", {})
            monitor = FakeMonitor(running=False)
            with patch("gdb_capture.subprocess.run", side_effect=OSError("missing gdb")):
                session.capture("failure", {}, monitor)
            self.assertFalse(monitor.running)
            self.assertNotIn("cont", monitor.commands)

    def test_successful_capture_preserves_a_previously_paused_guest(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = DebugSession(Path(tmp), Path(tmp) / "socket", "/usr/bin/gdb", {})
            monitor = FakeMonitor(running=False)

            def gdb(argv, **kwargs):
                script = Path(argv[-1])
                self.assertIn("gdb.execute('disconnect')", script.read_text())
                self.assertNotIn("gdb.execute('detach')", script.read_text())
                (script.parent / "commands.json").write_text('[{"ok": true}]')
                return SimpleNamespace(returncode=0)

            with patch("gdb_capture.subprocess.run", side_effect=gdb):
                session.capture("success", {}, monitor)
            result = json.loads((Path(tmp) / "debug/success/result.json").read_text())
            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["paused_after_gdb"])
            self.assertFalse(monitor.running)
            self.assertNotIn("cont", monitor.commands)

    def test_restore_failure_is_fatal_but_preserves_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = DebugSession(Path(tmp), Path(tmp) / "socket", "/usr/bin/gdb", {})
            with patch("gdb_capture.subprocess.run", side_effect=OSError("gdb failed")):
                with self.assertRaisesRegex(RuntimeError, "could not restore"):
                    session.capture("failure", {}, FakeMonitor(fail_restore=True))
            result = json.loads((Path(tmp) / "debug/failure/result.json").read_text())
            self.assertFalse(result["restored"])

    def test_rejects_unbounded_timeouts_and_multiline_expressions(self):
        for timeout in (0, -1, 61, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                validate_request({"timeout": timeout})
        with self.assertRaises(ValueError):
            validate_request({"expressions": ["$pc\ncontinue"]})


if __name__ == "__main__":
    unittest.main()
