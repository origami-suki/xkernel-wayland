#!/usr/bin/env python3
"""Run the existing guest harness with a monitor-confirmed profiler handshake.

The adapter supplies a sampler subclass and captures the harness's serial
connection. It leaves run_guest.py (including pre-existing local edits) intact;
all boot, shutdown, disk, bundle and serial-drain checks remain in that harness.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

import run_guest
from frame_capture import OneSecondSampler

BASELINE_FRAME_SHA256 = '5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287'


class ProfileSampler(OneSecondSampler):
    def __init__(self, send_serial, expected_hash, mode, **kwargs):
        super().__init__(**kwargs)
        self.send_serial = send_serial
        self.expected_hash = expected_hash
        self.mode = mode
        self.sent = False
        self.host_sample_slot = None

    def update(self, clean, output, send_monitor, state):
        if 'syscall_profile' not in state:
            shutil.copy2(__file__, output / 'run_syscall_profile.py')
            state['syscall_profile'] = {
                'mode': self.mode, 'expected_frame_sha256': self.expected_hash,
                'adapter_sha256': run_guest.sha256(Path(__file__)),
                'clock_boundary': 'host frame observation -> serial input -> guest stop; clocks unaligned',
            }
        super().update(clean, output, send_monitor, state)
        profile = state['syscall_profile']
        for marker in ('STARTUP_STOPPED', 'STEADY_START'):
            if re.search(r'(?m)^__ICT_PROFILE_' + marker + r'__$', clean):
                profile.setdefault(marker.lower() + '_host_received_ns', self.clock())
        if (not self.sent and re.search(r'(?m)^__ICT_PROFILE_WAIT_FRAME__$', clean)):
            frames = state.get('frames', [])
            match = next((i for i, f in enumerate(frames) if f['sha256'] == self.expected_hash), None)
            if match is not None:
                first = frames[match]
                previous = frames[match - 1] if match else None
                profile['first_frame'] = first['file']
                profile['first_frame_interval_host_ns'] = [
                    previous['host_monotonic_ns_before'] if previous else self.origin,
                    first['host_monotonic_ns_after'],
                ]
                profile['first_frame_signal_host_ns'] = self.clock()
                self.send_serial(b'first-frame\n')
                self.sent = True
        if self.origin is not None:
            slot = (self.clock() - self.origin) // 1_000_000_000
            if slot != self.host_sample_slot:
                self.host_sample_slot = slot
                pid = state.get('qemu_pid')
                if pid:
                    try:
                        status = Path(f'/proc/{pid}/status').read_text()
                        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                        sample = {'host_monotonic_ns': self.clock(), 'qemu_pid': pid,
                                  'cpu_ticks': int(stat[11]) + int(stat[12]),
                                  'ticks_per_second': os.sysconf('SC_CLK_TCK'),
                                  'loadavg': Path('/proc/loadavg').read_text().strip(),
                                  'host_meminfo': Path('/proc/meminfo').read_text(),
                                  'qemu_memory_kb': {key: int(value) for key, value in
                                      re.findall(r'^(VmRSS|VmSwap):\s+(\d+) kB$', status, re.M)}}
                        with (output / 'profile-host-samples.jsonl').open('a') as stream:
                            stream.write(json.dumps(sample) + '\n')
                    except FileNotFoundError:
                        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--profile-mode', choices=('on', 'off'), default='on')
    parser.add_argument('--expected-frame-sha256', default=BASELINE_FRAME_SHA256)
    options, remaining = parser.parse_known_args()
    if not re.fullmatch('[0-9a-f]{64}', options.expected_frame_sha256):
        raise ValueError('expected frame must be a SHA-256 digest')
    if any(arg.split('=')[0] in ('--guest-commands', '--gdb', '--monitor-stop') for arg in remaining):
        raise ValueError('profile adapter owns the session and requires normal shutdown without GDB')
    original_connect, original_sampler, original_argv = run_guest.connect, run_guest.OneSecondSampler, sys.argv
    serial_connection = []

    def connect(path, process, deadline):
        connection = original_connect(path, process, deadline)
        if path.name == 'serial':
            serial_connection.append(connection)
        return connection

    def sampler(**kwargs):
        return ProfileSampler(lambda data: serial_connection[0].sendall(data),
                              options.expected_frame_sha256, options.profile_mode, **kwargs)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh') as script:
        script.write('exec /opt/ict-tests/chromium/profile-startup.sh ' + options.profile_mode + '\n')
        script.flush()
        sys.argv = [original_argv[0], *remaining, '--sample-every-second', '--guest-commands', script.name]
        run_guest.connect, run_guest.OneSecondSampler = connect, sampler
        try:
            run_guest.main()
        finally:
            run_guest.connect, run_guest.OneSecondSampler, sys.argv = original_connect, original_sampler, original_argv


if __name__ == '__main__':
    main()
