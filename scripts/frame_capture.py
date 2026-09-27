"""Capture guest-marked display phases through the QEMU monitor.

Markers name observations, not presentation timestamps. A guest probe must hold
each phase long enough for capture; pixel checks determine whether it succeeded.
"""
import hashlib
import json
import re
import time


def capture_frames(clean, output, send_monitor, state, *, max_frames=16, vnc_capture=None):
    frames = state.setdefault('frames', [])
    seen = {frame['label'] for frame in frames + state.get('supplemental_frames', [])}
    for match in re.finditer(r'(?m)^__ICT_FRAME_([a-zA-Z0-9_-]{1,40})__$', clean):
        label = match[1]
        if label in seen:
            continue
        if len(seen) >= max_frames:
            raise ValueError('guest frame limit exceeded')
        path = output / ('frame-' + label + '.ppm')
        started = time.monotonic_ns()
        if path.exists():
            raise ValueError('refusing to overwrite a frame: ' + label)
        response = send_monitor('screendump ' + json.dumps(str(path)))
        ended = time.monotonic_ns()
        clean_response = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', response)
        errors = re.findall(rb'(?im)^Error:[^\r\n]*', clean_response)
        no_surface = errors == [b'Error: no surface']
        if errors and not (vnc_capture is not None and no_surface):
            raise ValueError('monitor reported a capture error: ' + label)
        if vnc_capture is not None:
            response_path = output / ('monitor-frame-' + label + '.response')
            response_path.write_bytes(response)
            attempt = {'label': label, 'status': 'BLOCKED' if no_surface else 'pending-validation',
                       'response_file': response_path.name,
                       'response_sha256': hashlib.sha256(response).hexdigest()}
            state.setdefault('monitor_capture_attempts', []).append(attempt)
        if no_surface:
            if path.exists():
                raise ValueError('monitor no-surface error unexpectedly produced a file')
            state['formal_monitor_capture'] = 'BLOCKED: QEMU monitor reports no surface'
            state.setdefault('supplemental_frames', []).append(vnc_capture(label))
            seen.add(label)
            temporary = output / 'metadata.tmp'
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(output / 'metadata.json')
            continue
        if not path.is_file():
            raise ValueError('monitor did not produce a PPM frame: ' + label)
        # QEMU emits this P6 header and packed 8-bit RGB raster. Validate the
        # exact payload length, not just the magic, before recording success.
        with path.open('rb') as stream:
            header = re.match(rb'P6\n([0-9]+) ([0-9]+)\n255\n', stream.read(80))
        if header is None:
            raise ValueError('invalid QEMU PPM header: ' + label)
        width, height = int(header[1]), int(header[2])
        if (width == 0 or height == 0 or width * height > 16_777_216
                or path.stat().st_size != header.end() + width * height * 3):
            raise ValueError('incomplete or oversized QEMU PPM raster: ' + label)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if vnc_capture is not None:
            attempt['status'] = 'captured'
        frames.append({'label': label, 'file': path.name, 'sha256': digest,
                       'width': width, 'height': height,
                       'host_monotonic_ns_before': started, 'host_monotonic_ns_after': ended,
                       'monitor_response_sha256': hashlib.sha256(response).hexdigest(),
                       'monitor_log': 'monitor.log',
                       'meaning': 'host observation interval, not guest presentation time'})
        if vnc_capture is not None:
            state.setdefault('supplemental_frames', []).append(vnc_capture(label))
        seen.add(label)
        # A live GDB client may read this while a display marker is captured.
        temporary = output / 'metadata.tmp'
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(output / 'metadata.json')


class OneSecondSampler:
    """Sample on the host after a guest launch marker, without guest polling.

    The origin is receipt of the marker immediately before browser launch, not
    exec completion. Late ticks are recorded as gaps rather than burst captures.
    """

    def __init__(self, clock=time.monotonic_ns, max_frames=181):
        self.clock = clock
        self.max_frames = max_frames
        self.origin = None
        self.next_due = None
        self.stopped = False

    def update(self, clean, output, send_monitor, state, *, vnc_capture=None):
        if self.stopped:
            return
        if self.origin is None:
            if not re.search(r'(?m)^__ICT_CAPTURE_START__$', clean):
                return
            self.origin = self.clock()
            self.next_due = self.origin
            state['periodic_capture'] = {
                'origin_host_monotonic_ns': self.origin,
                'period_ns': 1_000_000_000,
                'max_frames': self.max_frames,
                'origin_meaning': 'host receipt of guest marker immediately before browser launch; '
                                  'includes launch overhead, excludes Weston setup; serial delay uncalibrated',
            }
        # Restart observations share the original sampling grid, but keep
        # separate host receipt times for each browser launch/window boundary.
        for name in ('FIRST_END', 'RELAUNCH', 'SECOND_END'):
            if re.search(r'(?m)^__ICT_CAPTURE_' + name + r'__$', clean):
                events = state['periodic_capture'].setdefault('events_host_monotonic_ns', {})
                if name not in events:
                    events[name] = self.clock()
        if re.search(r'(?m)^__ICT_CAPTURE_STOP__$', clean):
            self.stopped = True
            state['periodic_capture']['stop_reason'] = 'guest-stop-marker'
            return
        now = self.clock()
        if now < self.next_due:
            return
        if len({f['label'] for f in state.get('frames', []) + state.get('supplemental_frames', [])}) >= self.max_frames:
            self.stopped = True
            state['periodic_capture']['stop_reason'] = 'frame-limit'
            return
        slot = (now - self.origin) // 1_000_000_000
        capture_frames(f'__ICT_FRAME_sample-{slot:03d}__\n', output, send_monitor,
                       state, max_frames=self.max_frames, vnc_capture=vnc_capture)
        self.next_due = self.origin + (slot + 1) * 1_000_000_000
