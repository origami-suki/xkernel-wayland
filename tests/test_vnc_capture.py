"""Host RFB protocol tests, never launch QEMU or a guest."""
import hashlib
import io
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import vnc_capture as vnc
from frame_capture import capture_frames, OneSecondSampler


def init(width=2, height=1, security=b'\x01\x01', result=b'\0' * 4):
    return (b'RFB 003.008\n' + security + result + struct.pack('!HH', width, height)
            + bytes.fromhex('2018010100ff00ff00ff100800000000') + struct.pack('!I', 4) + b'test')


def rect(x=0, y=0, width=2, height=1, encoding=0, raw=b'\0\0\xff\0\0\xff\0\0'):
    return struct.pack('!HHHHi', x, y, width, height, encoding) + raw


def update(*rectangles):
    return b'\0\0' + struct.pack('!H', len(rectangles)) + b''.join(rectangles)


class FragmentedSocket:
    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.sent = bytearray()

    def recv(self, size):
        return self.data.read(min(size, 1))

    def sendall(self, data):
        self.sent.extend(data)

    def settimeout(self, timeout):
        pass


class ProtocolTests(unittest.TestCase):
    def read(self, payload):
        client = FragmentedSocket(payload)
        rx, tx = io.BytesIO(), io.BytesIO()
        frame = vnc.RFBReader(client, rx, tx, 2).frame()
        self.assertEqual(rx.getvalue(), payload)
        self.assertEqual(tx.getvalue(), bytes(client.sent))
        self.assertEqual(tx.getvalue()[14:34], b'\0' * 4 + vnc.PIXEL_FORMAT)
        self.assertEqual(tx.getvalue()[46:56], struct.pack('!BBHHHH', 3, 0, 0, 0, 2, 1))
        return frame

    def test_fragmented_little_endian_raw_and_multiple_rectangles(self):
        width, height, rgb, info = self.read(init() + update(
            rect(width=1, raw=b'\x03\x02\x01\xff'),
            rect(x=1, width=1, raw=b'\x06\x05\x04\x80')))
        self.assertEqual((width, height, rgb), (2, 1, b'\x01\x02\x03\x04\x05\x06'))
        self.assertFalse(info['incremental'])

    def test_resize_discards_old_raster_and_requests_full_new_extent(self):
        payload = init() + update(rect(width=1, height=2, encoding=-223, raw=b''))
        payload += update(rect(width=1, height=2))
        width, height, rgb, info = self.read(payload)
        self.assertEqual((width, height, rgb), (1, 2, b'\xff\0\0\0\xff\0'))
        self.assertEqual(info['resizes'], [[1, 2]])

    def test_unrelated_bell_and_bounded_clipboard(self):
        self.read(init() + b'\x02\x03\0\0\0' + struct.pack('!I', 3) + b'abc' + update(rect()))

    def test_reject_security_version_extent_malformed_and_incomplete(self):
        cases = [init().replace(b'003.008', b'003.007') + update(rect()),
                 init(security=b'\x01\x02'), init(security=b'\0'),
                 init(result=b'\0\0\0\x01'), init(width=0), init(width=65535, height=65535),
                 init() + update(rect(x=1)), init() + update(rect(encoding=1)),
                 init() + update(rect(width=0, raw=b'')), init() + update(),
                 init() + update(rect(width=1, raw=b'\0' * 4)),
                 init() + update(rect(width=1, encoding=-223, raw=b''), rect()),
                 init() + update(rect())[:-1], init() + b'\x01',
                 init() + b'\0\0\xff\xff',
                 init() + b'\x03\0\0\0' + struct.pack('!I', 65537)]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                vnc.RFBReader(FragmentedSocket(payload), io.BytesIO(), io.BytesIO(), 2).frame()

    def test_read_wire_and_deadline_limits(self):
        reader = vnc.RFBReader(FragmentedSocket(b'abc'), io.BytesIO(), io.BytesIO(), 2)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            reader.read(vnc.MAX_WIRE_BYTES + 1)
        reader.deadline = 0
        with self.assertRaises(TimeoutError):
            reader.read(1)

    def test_real_unix_server_known_pattern_with_raw_transcript_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            path = output / 'vnc'
            payload = init() + update(rect())
            errors = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                def serve():
                    try:
                        with server.accept()[0] as client:
                            client.settimeout(2)
                            # Deliberate byte fragmentation; writes remain full-duplex.
                            for byte in payload:
                                client.sendall(bytes([byte]))
                            expected = 56
                            received = bytearray()
                            while len(received) < expected:
                                chunk = client.recv(expected - len(received))
                                if not chunk:
                                    raise ValueError('client truncated requests')
                                received.extend(chunk)
                            self.assertEqual(bytes(received), b'RFB 003.008\n\x01\x01'
                                + b'\0' * 4 + vnc.PIXEL_FORMAT
                                + struct.pack('!BBHii', 2, 0, 2, 0, -223)
                                + struct.pack('!BBHHHH', 3, 0, 0, 0, 2, 1))
                    except Exception as error:
                        errors.append(error)
                thread = threading.Thread(target=serve)
                thread.start()
                result = vnc.capture_vnc(path, output, 'known')
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
            ppm = (output / result['file']).read_bytes()
            self.assertEqual(ppm, b'P6\n2 1\n255\n\xff\0\0\0\xff\0')
            self.assertEqual(result['sha256'], hashlib.sha256(ppm).hexdigest())
            self.assertEqual((output / result['files']['rx']['file']).read_bytes(), payload)
            provenance = json.loads((output / result['provenance_file']).read_text())
            self.assertTrue(provenance['supplemental_only'])
            self.assertFalse(provenance['guest_acceleration_verified'])
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                vnc.capture_vnc(path, output, 'known')

    def test_failed_security_keeps_wire_and_failure_provenance_without_image(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            path = output / 'vnc'
            payload = b'RFB 003.008\n\x01\x02'
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                def serve():
                    with server.accept()[0] as client:
                        client.settimeout(2)
                        client.sendall(payload)
                        received = bytearray()
                        while len(received) < 12:
                            received.extend(client.recv(12 - len(received)))
                thread = threading.Thread(target=serve)
                thread.start()
                with self.assertRaisesRegex(ValueError, 'security unavailable'):
                    vnc.capture_vnc(path, output, 'failed')
                thread.join(3)
                self.assertFalse(thread.is_alive())
            provenance = json.loads((output / 'supplemental-vnc-failed.json').read_text())
            self.assertEqual(provenance['status'], 'failed')
            self.assertEqual((output / 'supplemental-vnc-failed.server.rfb').read_bytes(), payload)
            self.assertFalse((output / 'supplemental-vnc-failed.ppm').exists())


class SupplementBoundaryTests(unittest.TestCase):
    def test_no_surface_records_separate_evidence_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            state = {'graphics': {'guest_acceleration_verified': False}}
            calls = []
            def supplemental(label):
                calls.append(label)
                return {'label': label, 'supplemental_only': True}
            def monitor(command):
                return b'\x1b[0mError: no surface\r\n(qemu) '
            for _ in range(2):
                capture_frames('__ICT_FRAME_page__\n', output, monitor, state, vnc_capture=supplemental)
            self.assertEqual(calls, ['page'])
            self.assertEqual(state['frames'], [])
            self.assertEqual(state['monitor_capture_attempts'][0]['status'], 'BLOCKED')
            self.assertFalse(state['graphics']['guest_acceleration_verified'])
            self.assertIn('BLOCKED', state['formal_monitor_capture'])
            self.assertEqual((output / 'monitor-frame-page.response').read_bytes(), monitor(''))
            self.assertEqual(json.loads((output / 'metadata.json').read_text())['frames'], [])

    def test_unexpected_monitor_error_stays_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            for response in (b'Error: no surface extra\n', b'Error: no surface\nError: other\n',
                             b'Error: other\n'):
                with self.subTest(response=response), self.assertRaises(ValueError):
                    capture_frames('__ICT_FRAME_page__\n', Path(directory), lambda _: response,
                                   {}, vnc_capture=lambda _: self.fail('must not fall back'))

    def test_sampler_counts_supplements_toward_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [1_000_000_000]
            sampler = OneSecondSampler(clock=lambda: now[0], max_frames=1)
            state = {}
            for _ in range(2):
                sampler.update('__ICT_CAPTURE_START__\n', Path(directory),
                               lambda _: b'Error: no surface\n', state,
                               vnc_capture=lambda label: {'label': label})
                now[0] += 1_000_000_000
            self.assertEqual(len(state['supplemental_frames']), 1)
            self.assertEqual(state['periodic_capture']['stop_reason'], 'frame-limit')


if __name__ == '__main__':
    unittest.main()
