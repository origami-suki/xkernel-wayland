"""Capture guest-marked display phases through the QEMU monitor.

Markers name observations, not presentation timestamps. A guest probe must hold
each phase long enough for capture; pixel checks determine whether it succeeded.
"""
import hashlib
import json
import re
import time


def capture_frames(clean, output, send_monitor, state, *, max_frames=16):
    frames = state.setdefault('frames', [])
    seen = {frame['label'] for frame in frames}
    for match in re.finditer(r'(?m)^__ICT_FRAME_([a-zA-Z0-9_-]{1,40})__$', clean):
        label = match[1]
        if label in seen:
            continue
        if len(frames) >= max_frames:
            raise ValueError('guest frame limit exceeded')
        path = output / ('frame-' + label + '.ppm')
        started = time.monotonic_ns()
        if path.exists():
            raise ValueError('refusing to overwrite a frame: ' + label)
        response = send_monitor('screendump ' + json.dumps(str(path)))
        ended = time.monotonic_ns()
        clean_response = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', response)
        if re.search(rb'(?im)^Error:', clean_response):
            raise ValueError('monitor reported a capture error: ' + label)
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
        frames.append({'label': label, 'file': path.name, 'sha256': digest,
                       'width': width, 'height': height,
                       'host_monotonic_ns_before': started, 'host_monotonic_ns_after': ended,
                       'monitor_response_sha256': hashlib.sha256(response).hexdigest(),
                       'monitor_log': 'monitor.log',
                       'meaning': 'host observation interval, not guest presentation time'})
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

    def update(self, clean, output, send_monitor, state):
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
        if re.search(r'(?m)^__ICT_CAPTURE_STOP__$', clean):
            self.stopped = True
            state['periodic_capture']['stop_reason'] = 'guest-stop-marker'
            return
        now = self.clock()
        if now < self.next_due:
            return
        if len(state.get('frames', [])) >= self.max_frames:
            self.stopped = True
            state['periodic_capture']['stop_reason'] = 'frame-limit'
            return
        slot = (now - self.origin) // 1_000_000_000
        capture_frames(f'__ICT_FRAME_sample-{slot:03d}__\n', output, send_monitor,
                       state, max_frames=self.max_frames)
        self.next_due = self.origin + (slot + 1) * 1_000_000_000
