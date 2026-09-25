import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from frame_capture import capture_frames


class CaptureTests(unittest.TestCase):
    def capture(self, payload, response=b'(qemu) '):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            state = {}
            calls = []

            def monitor(command):
                calls.append(command)
                path = Path(json.loads(command.removeprefix('screendump ')))
                if payload is not None:
                    path.write_bytes(payload)
                return response

            capture_frames('__ICT_FRAME_red__\n', output, monitor, state)
            capture_frames('__ICT_FRAME_red__\n', output, monitor, state)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(state['frames']), 1)
            return state['frames'][0]

    def test_complete_rgb_frame_and_repeated_log_scan(self):
        frame = self.capture(b'P6\n2 1\n255\n\xff\0\0\0\xff\0')
        self.assertEqual((frame['width'], frame['height']), (2, 1))

    def test_missing_file_fails(self):
        with self.assertRaises(ValueError):
            self.capture(None)

    def test_magic_without_header_fails(self):
        with self.assertRaises(ValueError):
            self.capture(b'P6\n')

    def test_truncated_raster_fails(self):
        with self.assertRaises(ValueError):
            self.capture(b'P6\n2 1\n255\n\xff\0\0')

    def test_extra_raster_bytes_fail(self):
        with self.assertRaises(ValueError):
            self.capture(b'P6\n1 1\n255\n\xff\0\0extra')

    def test_reported_monitor_error_fails_even_with_a_file(self):
        with self.assertRaises(ValueError):
            self.capture(b'P6\n1 1\n255\n\xff\0\0', b'Error: capture failed\n(qemu) ')

    def test_echo_and_path_escape_are_not_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            state = {}
            def monitor(command):
                self.fail('must not call monitor for an echoed/invalid marker')
            capture_frames("printf '__ICT_FRAME_red__'\n__ICT_FRAME_../bad__\n",
                           Path(directory), monitor, state)
            self.assertEqual(state['frames'], [])


if __name__ == '__main__':
    unittest.main()
