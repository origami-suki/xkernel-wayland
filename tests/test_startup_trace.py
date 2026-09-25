"""Reject incomplete/corrupt serial transfers before claiming a trace exists."""
import base64
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from extract_startup_trace import extract


def transfer(data):
    compressed = gzip.compress(data, mtime=0)
    header = f'__ICT_TRACE_BEGIN__ {len(data)} {hashlib.sha256(data).hexdigest()} {len(compressed)} {hashlib.sha256(compressed).hexdigest()}\r\n'
    lines = [b'__ICT_TRACE_DATA__ ' + line + b'\r\n'
             for line in base64.encodebytes(compressed).splitlines()]
    return header.encode() + b''.join(lines) + b'__ICT_TRACE_END__\r\n'


class TraceTransferTests(unittest.TestCase):
    def run_extract(self, wire):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        serial, output = Path(folder.name) / 'serial.log', Path(folder.name) / 'trace'
        serial.write_bytes(wire)
        return serial, output

    def test_binary_roundtrip_ignores_unrelated_console_lines(self):
        data = bytes(range(256)) * 33
        serial, output = self.run_extract(b'boot log\r\n' + transfer(data).replace(
            b'__ICT_TRACE_DATA__ ', b'kernel log\r\n__ICT_TRACE_DATA__ ', 1) + b'shell prompt\r\n')
        self.assertTrue(extract(serial, output)['transfer_verified'])
        self.assertEqual(output.read_bytes(), data)

    def test_truncation_and_missing_chunks_do_not_create_trace(self):
        wire = transfer(bytes(range(256)) * 20)
        for broken in [wire.split(b'__ICT_TRACE_END__')[0],
                       b'\n'.join(wire.splitlines()[0:1] + wire.splitlines()[2:])]:
            serial, output = self.run_extract(broken)
            with self.assertRaises(ValueError):
                extract(serial, output)
            self.assertFalse(output.exists())

    def test_corrupt_hash_duplicate_payload_and_size_limit(self):
        wire = transfer(b'payload')
        for broken in [wire.replace(hashlib.sha256(b'payload').hexdigest().encode(), b'0' * 64),
                       wire + wire,
                       wire.replace(b'__ICT_TRACE_BEGIN__ 7 ', b'__ICT_TRACE_BEGIN__ 999999999 '),
                       wire.replace(b'__ICT_TRACE_DATA__ ', b'__ICT_TRACE_DATA__ !', 1)]:
            serial, output = self.run_extract(broken)
            with self.assertRaises(ValueError):
                extract(serial, output)
            self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        serial, output = self.run_extract(transfer(b'payload'))
        output.write_bytes(b'prior evidence')
        with self.assertRaises(ValueError):
            extract(serial, output)
        self.assertEqual(output.read_bytes(), b'prior evidence')


if __name__ == '__main__':
    unittest.main()
