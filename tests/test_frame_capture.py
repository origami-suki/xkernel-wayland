import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from frame_capture import OneSecondSampler, capture_frames


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

    def test_metadata_readers_see_complete_old_state_until_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            metadata = output / 'metadata.json'
            metadata.write_text('{"old": true}\n')
            original = Path.write_text
            observed = []

            def writing(path, contents, *args, **kwargs):
                if path.name.startswith('metadata.'):
                    # Emulate a reader running after truncate, before full write.
                    path.write_bytes(b'')
                    observed.append(json.loads(metadata.read_text()))
                return original(path, contents, *args, **kwargs)

            def monitor(command):
                Path(json.loads(command.removeprefix('screendump '))).write_bytes(
                    b'P6\n1 1\n255\n\xff\0\0')
                return b'(qemu) '

            with patch.object(Path, 'write_text', writing):
                capture_frames('__ICT_FRAME_red__\n', output, monitor, {})
            self.assertEqual(observed, [{'old': True}])
            self.assertEqual(json.loads(metadata.read_text())['frames'][0]['label'], 'red')

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

    def test_periodic_capture_ignores_echo_and_records_late_ticks_without_burst(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            state = {}
            now = [10_000_000_000]
            sampler = OneSecondSampler(clock=lambda: now[0])

            def monitor(command):
                path = Path(json.loads(command.removeprefix('screendump ')))
                path.write_bytes(b'P6\n1 1\n255\n\xff\0\0')
                return b'(qemu) '

            sampler.update("printf '__ICT_CAPTURE_START__'\n", output, monitor, state)
            self.assertNotIn('frames', state)
            marker = '__ICT_CAPTURE_START__\n'
            sampler.update(marker, output, monitor, state)
            now[0] += 500_000_000
            sampler.update(marker, output, monitor, state)
            self.assertEqual(len(state['frames']), 1)
            now[0] += 3_000_000_000
            sampler.update(marker, output, monitor, state)
            self.assertEqual([x['label'] for x in state['frames']], ['sample-000', 'sample-003'])
            sampler.update(marker + '__ICT_CAPTURE_STOP__\n', output, monitor, state)
            now[0] += 2_000_000_000
            sampler.update(marker, output, monitor, state)
            self.assertEqual(len(state['frames']), 2)

    def test_default_marker_capture_limit_remains_sixteen(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'limit'):
                capture_frames('__ICT_FRAME_new__\n', Path(directory), None,
                               {'frames': [{'label': str(i)} for i in range(16)]})

    def test_restart_events_keep_first_receipt_and_original_sampling_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [10_000_000_000]
            sampler = OneSecondSampler(clock=lambda: now[0], max_frames=601)
            state = {}

            def monitor(command):
                Path(json.loads(command.removeprefix('screendump '))).write_bytes(
                    b'P6\n1 1\n255\n\xff\0\0')
                return b'(qemu) '

            text = '__ICT_CAPTURE_START__\n'
            sampler.update(text, Path(directory), monitor, state)
            now[0] += 200_500_000_000
            text += '__ICT_CAPTURE_FIRST_END__\n__ICT_CAPTURE_RELAUNCH__\n'
            sampler.update(text, Path(directory), monitor, state)
            received = now[0]
            now[0] += 1_000_000_000
            sampler.update(text, Path(directory), monitor, state)
            timing = state['periodic_capture']
            self.assertEqual(timing['origin_host_monotonic_ns'], 10_000_000_000)
            self.assertEqual(timing['events_host_monotonic_ns']['RELAUNCH'], received)
            self.assertEqual([f['label'] for f in state['frames']],
                             ['sample-000', 'sample-200', 'sample-201'])
            text += '__ICT_CAPTURE_SECOND_END__\n__ICT_CAPTURE_STOP__\n'
            sampler.update(text, Path(directory), monitor, state)
            self.assertEqual(timing['events_host_monotonic_ns']['SECOND_END'], now[0])


if __name__ == '__main__':
    unittest.main()
