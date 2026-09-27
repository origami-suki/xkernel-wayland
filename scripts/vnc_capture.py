"""Bounded RFB 3.8 capture from a private QEMU UNIX socket, supplemental only.

Wire layout: https://www.rfc-editor.org/rfc/rfc6143 (sections 7.1-7.8).
No input events, compressed encodings, public listeners or authentication fallback.
"""
import hashlib
import json
import os
import re
import socket
import stat
import struct
import time
from pathlib import Path

MAX_PIXELS = 16_777_216
MAX_WIRE_BYTES = 134_217_728
PIXEL_FORMAT = struct.pack('!BBBBHHHBBB3x', 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)


def vnc_options(enabled, socket_dir):
    return ['-vnc', 'unix:' + str(Path(socket_dir) / 'vnc')] if enabled else []


def _extent(width, height):
    if not width or not height or width * height > MAX_PIXELS:
        raise ValueError('invalid or oversized RFB framebuffer')
    return width * height


class RFBReader:
    def __init__(self, client, received, sent, timeout):
        self.client, self.received, self.sent = client, received, sent
        self.deadline = time.monotonic() + timeout
        self.wire_bytes = 0

    def read(self, size):
        if size < 0 or self.wire_bytes + size > MAX_WIRE_BYTES:
            raise ValueError('RFB transcript byte limit exceeded')
        data = bytearray()
        while len(data) < size:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('RFB capture deadline exceeded')
            self.client.settimeout(remaining)
            chunk = self.client.recv(min(size - len(data), 65536))
            if not chunk:
                raise ValueError('truncated RFB stream')
            self.received.write(chunk)
            self.wire_bytes += len(chunk)
            data.extend(chunk)
        return bytes(data)

    def send(self, data):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('RFB capture deadline exceeded')
        self.client.settimeout(remaining)
        self.client.sendall(data)
        self.sent.write(data)

    def frame(self):
        if self.read(12) != b'RFB 003.008\n':
            raise ValueError('requires RFB 3.8 server')
        self.send(b'RFB 003.008\n')
        count = self.read(1)[0]
        if not count or 1 not in self.read(count):
            raise ValueError('RFB None security unavailable')
        self.send(b'\x01')
        if self.read(4) != b'\0\0\0\0':
            raise ValueError('RFB security failed')
        self.send(b'\x01')  # shared view; do not disconnect other diagnostic clients
        width, height = struct.unpack('!HH', self.read(4))
        _extent(width, height)
        initial_format = self.read(16).hex()
        name_length = struct.unpack('!I', self.read(4))[0]
        if name_length > 65536:
            raise ValueError('RFB server name exceeds bound')
        name = self.read(name_length).decode('utf-8', errors='replace')
        self.send(b'\0\0\0\0' + PIXEL_FORMAT)
        self.send(struct.pack('!BBHii', 2, 0, 2, 0, -223))
        resizes = []
        for attempt in range(8):
            pixels = _extent(width, height)
            raster = bytearray(pixels * 3)
            covered = bytearray(pixels)
            self.send(struct.pack('!BBHHHH', 3, 0, 0, 0, width, height))
            resized = False
            # Require a complete framebuffer from one nonincremental response.
            for message in range(128):
                kind = self.read(1)[0]
                if kind == 2:  # Bell has no payload
                    continue
                if kind == 3:
                    self.read(3)
                    length = struct.unpack('!I', self.read(4))[0]
                    if length > 65536:
                        raise ValueError('RFB clipboard exceeds bound')
                    self.read(length)
                    continue
                if kind != 0:
                    raise ValueError('unexpected RFB server message')
                _, count = struct.unpack('!BH', self.read(3))
                if count > 4096:
                    raise ValueError('RFB rectangle limit exceeded')
                for index in range(count):
                    x, y, rw, rh, encoding = struct.unpack('!HHHHi', self.read(12))
                    if encoding == -223:
                        if x or y or index != count - 1:
                            raise ValueError('malformed DesktopSize rectangle')
                        _extent(rw, rh)
                        width, height = rw, rh
                        resizes.append([width, height])
                        resized = True
                        continue
                    if encoding != 0 or not rw or not rh or x + rw > width or y + rh > height:
                        raise ValueError('unsupported or out-of-bounds RFB rectangle')
                    raw = self.read(rw * rh * 4)
                    # Requested little-endian 0x00RRGGBB, thus wire B,G,R,pad.
                    rgb = bytearray(rw * rh * 3)
                    rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
                    for row in range(rh):
                        offset = (y + row) * width + x
                        raster[offset * 3:(offset + rw) * 3] = rgb[row * rw * 3:(row + 1) * rw * 3]
                        covered[offset:offset + rw] = b'\x01' * rw
                if resized:
                    break
                if not count or 0 in covered:
                    raise ValueError('incomplete nonincremental RFB framebuffer')
                return width, height, bytes(raster), {'server_name': name,
                    'initial_pixel_format_hex': initial_format, 'requested_pixel_format_hex': PIXEL_FORMAT.hex(),
                    'resizes': resizes, 'encodings': [0, -223], 'incremental': False}
            if not resized:
                raise ValueError('RFB message limit exceeded')
        raise ValueError('RFB resize limit exceeded')


def capture_vnc(socket_path, output, label, *, timeout=10, context=None):
    """Preserve raw wire directions and provenance even when decoding fails."""
    socket_path, output = Path(socket_path), Path(output)
    parent = socket_path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("VNC socket directory must be private and owned")
    info = socket_path.stat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('expected owned private UNIX VNC socket')
    prefix = 'supplemental-vnc-' + label
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', label):
        raise ValueError('invalid VNC frame label')
    files = {key: output / (prefix + suffix) for key, suffix in
             [('rx', '.server.rfb'), ('tx', '.client.rfb'), ('ppm', '.ppm'), ('provenance', '.json')]}
    if any(path.exists() for path in files.values()):
        raise ValueError('refusing to overwrite VNC evidence')
    provenance = {'runner_metadata_file': 'metadata.json', 'context': context or {},
        'label': label, 'source': 'qemu-vnc-rfb-unix', 'supplemental_only': True,
        'formal_monitor_requirement': 'BLOCKED if monitor screendump is unavailable',
        'guest_acceleration_verified': False, 'socket': str(socket_path),
        'socket_metadata': {'uid': info.st_uid, 'gid': info.st_gid, 'mode': oct(info.st_mode),
                            'inode': info.st_ino}, 'host_monotonic_ns_before': time.monotonic_ns()}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client, \
                files['rx'].open('xb') as rx, files['tx'].open('xb') as tx:
            client.settimeout(timeout)
            client.connect(str(socket_path))
            width, height, raster, protocol = RFBReader(client, rx, tx, timeout).frame()
        files['ppm'].write_bytes(f'P6\n{width} {height}\n255\n'.encode() + raster)
        provenance.update(width=width, height=height, protocol=protocol, status='captured')
    except Exception as error:
        provenance.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        provenance['host_monotonic_ns_after'] = time.monotonic_ns()
        provenance['files'] = {}
        for key, path in files.items():
            if key != 'provenance' and path.exists():
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                provenance['files'][key] = {'file': path.name, 'sha256': digest,
                                           'bytes': path.stat().st_size}
        files['provenance'].write_text(json.dumps(provenance, indent=2) + '\n')
    provenance.update(file=files['ppm'].name, sha256=provenance['files']['ppm']['sha256'],
                      provenance_file=files['provenance'].name)
    return provenance
