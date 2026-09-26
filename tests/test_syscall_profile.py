import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_syscall_profile import FIELDS, aggregate, analyze, parse_snapshot
from run_syscall_profile import ProfileSampler
from frame_capture import OneSecondSampler


def snapshot(rows='0,7,63,3,2,1,10,100,8,70,0\n'):
    return ('SYSCALL_PROFILE version=1 epoch=1 start_ns=10 stop_ns=110 freeze_end_ns=120 shards=16 capacity=1024 max_probes=32\n'
            + ','.join(FIELDS) + '\n' + rows + 'SYSCALL_PROFILE_END dropped=0\n')


class ProfileTests(unittest.TestCase):
    def test_parser_retains_incomplete_calls_and_merges_shards(self):
        head, rows = parse_snapshot(snapshot('0,7,63,3,2,1,10,100,8,70,0\n1,7,63,4,4,0,20,200,9,80,0\n'))
        result = aggregate(rows, 'sysno')[0]
        self.assertEqual((result['cpu_ns'], result['completed'], result['unfinished'], result['max_elapsed_ns']), (30, 6, 1, 80))
        self.assertEqual(head['dropped'], 0)

    def test_parser_rejects_corruption_and_duplicates(self):
        valid = snapshot()
        for broken in (valid.rsplit('\n', 2)[0], valid.replace('version=1', 'version=2'),
                       valid.replace('3,2,1', '1,2,1'), valid.replace('3,2,1', '3,2,3'),
                       valid.replace('0,7,63', 'x,7,63'),
                       snapshot('0,7,63,3,2,1,10,100,8,70,0\n' * 2)):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                parse_snapshot(broken)

    def test_transfer_hash_and_old_evidence_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run = root / 'run'; run.mkdir()
            (run / 'metadata.json').write_text('{}')
            names = root / 'names.rs'; names.write_text('    read = 63,\n')
            data = snapshot(); digest = hashlib.sha256(data.encode()).hexdigest()
            serial = ''.join(f'PROFILE_DATA_BEGIN_{s}\n{data}PROFILE_DATA_END_{s}\n{digest}  /tmp/profile-{s}.csv\n' for s in ('startup', 'steady'))
            (run / 'serial.log').write_text(serial.replace('\n', '\r\n'))
            result = analyze(run, root / 'ok', names)
            self.assertEqual(result['stages']['startup']['top_cpu'][0]['name'], 'read')
            with self.assertRaises(ValueError): analyze(run, root / 'ok', names)
            (run / 'serial.log').write_text(serial.replace(digest, '0' * 64))
            with self.assertRaises(ValueError): analyze(run, root / 'bad', names)
            self.assertFalse((root / 'bad').exists())

    def test_handshake_requires_guest_ready_and_matching_pixels_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(OneSecondSampler, 'update'):
            output = Path(directory); sent = []
            sampler = ProfileSampler(sent.append, 'a' * 64, 'on', clock=lambda: 123)
            state = {'frames': [{'sha256': 'b' * 64, 'file': 'old.ppm', 'host_monotonic_ns_before': 10, 'host_monotonic_ns_after': 20}]}
            sampler.update('__ICT_PROFILE_WAIT_FRAME__\n', output, None, state)
            self.assertEqual(sent, [])
            state['frames'].append({'sha256': 'a' * 64, 'file': 'first.ppm', 'host_monotonic_ns_before': 30, 'host_monotonic_ns_after': 40})
            sampler.update('', output, None, state); self.assertEqual(sent, [])
            sampler.update('__ICT_PROFILE_WAIT_FRAME__\n', output, None, state)
            sampler.update('__ICT_PROFILE_WAIT_FRAME__\n', output, None, state)
            self.assertEqual(sent, [b'first-frame\n'])
            self.assertEqual(state['syscall_profile']['first_frame_interval_host_ns'], [10, 40])


if __name__ == '__main__':
    unittest.main()
