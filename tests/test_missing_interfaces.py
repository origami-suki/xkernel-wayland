"""Log evidence must not be promoted to syscall causality or implementation work."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from missing_interfaces import collect, write_inventory


class MissingInterfaceTests(unittest.TestCase):
    def test_kernel_and_application_evidence_keep_attribution_and_line_references(self):
        kernel = "\x1b[33mUnimplemented syscall: landlock_create_ruleset (tid=29)\x1b[m"
        app = "[29:57:0925/074242:ERROR:base/watcher.cc:3] inotify_init() failed: Function not implemented (38)"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "serial.log"
            path.write_text("noise\x00\n" + kernel + "\n" + app + "\n" + kernel + "\n")
            report = collect(path)
        self.assertEqual(len(report["entries"]), 2)
        syscall, operation = report["entries"]
        self.assertEqual(syscall["interface_or_message"], "landlock_create_ruleset")
        self.assertEqual(syscall["line_numbers"], [2, 4])
        self.assertEqual(syscall["observed_tids"], [29])
        self.assertEqual(len(syscall["examples"]), 1)
        self.assertIsNone(syscall["implementation_required"])
        self.assertEqual(operation["source"], "unattributed_operation")
        self.assertEqual(operation["observed_pids"], [29])
        self.assertEqual(operation["observed_tids"], [57])
        self.assertNotIn("call_count", syscall)

    def test_subcommands_stay_distinct_and_debug_only_syscalls_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "serial.log"
            path.write_text("unsupported fcntl parameters: cmd: 700\n"
                            "unsupported fcntl parameters: cmd: 701\n"
                            "sys_prctl: unsupported option 999\n"
                            "Unsupported syscall requested dummy fd: inotify_init1\n")
            entries = collect(path)["entries"]
        self.assertEqual([e["subcommand"] for e in entries], [700, 701, 999, None])
        self.assertEqual(entries[-1]["interface_or_message"], "inotify_init1")

    def test_missing_log_is_not_a_clean_compatibility_result(self):
        with tempfile.TemporaryDirectory() as directory:
            report = collect(Path(directory) / "serial.log")
        self.assertFalse(report["serial_exists"])
        self.assertIsNone(report["serial_sha256"])
        self.assertEqual(report["entries"], [])
        self.assertIn("not a syscall trace", report["coverage"])

    def test_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "serial.log").write_text("Unimplemented syscall: ptrace (tid=5)\n")
            summary = write_inventory(output)
            original = (output / summary["file"]).read_bytes()
            with self.assertRaises(FileExistsError):
                write_inventory(output)
            self.assertEqual((output / summary["file"]).read_bytes(), original)
            self.assertEqual(json.loads(original)["entries"][0]["impact"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
