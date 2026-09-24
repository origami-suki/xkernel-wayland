#!/usr/bin/env python3
"""Read-only recursive ELF dependency inventory; resolve symlinks within guest root."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys

root, evidence = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)


def guest_resolve(name):
    parts = list(PurePosixPath(posixpath.normpath('/' + name.lstrip('/'))).parts[1:])
    done = []
    for _ in range(256):
        if not parts:
            return root.joinpath(*done)
        part = parts.pop(0)
        if part == '..':
            if done:
                done.pop()
            continue
        if part == '.':
            continue
        current = root.joinpath(*done, part)
        if current.is_symlink():
            target = os.readlink(current)
            if target.startswith('/'):
                done = []
            parts = list(PurePosixPath(target).parts)[int(target.startswith('/')):] + parts
        else:
            done.append(part)
    raise RuntimeError('Guest symlink loop: ' + name)


entries = ['/bin/busybox', '/lib/ld-musl-aarch64.so.1', '/usr/bin/weston',
           '/usr/bin/seatd', '/usr/bin/weston-simple-shm', '/usr/lib/chromium/chromium',
           '/usr/lib/libweston-14/drm-backend.so', '/usr/lib/weston/kiosk-shell.so']
visited = {}
missing = []


def visit(name):
    real = guest_resolve(name)
    canonical = '/' + str(real.relative_to(root))
    if canonical in visited:
        return canonical
    data = real.read_bytes()
    header = subprocess.run(['readelf', '-h', str(real)], capture_output=True, text=True, check=True).stdout
    if 'AArch64' not in header:
        raise RuntimeError('Non-AArch64 ELF in guest chain: ' + canonical)
    output = subprocess.run(['readelf', '-d', str(real)], capture_output=True, text=True, check=True).stdout
    needed = re.findall(r'\(NEEDED\).*?\[(.*?)\]', output)
    runpaths = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', output)
    search = []
    for path in runpaths:
        search += [x.replace('$ORIGIN', posixpath.dirname(canonical)) for x in path.split(':')]
    search += ['/lib', '/usr/lib', '/usr/lib/weston', '/usr/lib/chromium']
    info = {'sha256': hashlib.sha256(data).hexdigest(), 'needed': needed, 'rpath': runpaths, 'resolved': {}}
    visited[canonical] = info
    for library in needed:
        choices = [p + '/' + library for p in search]
        found = next((p for p in choices if guest_resolve(p).is_file()), None)
        if found:
            info['resolved'][library] = visit(found)
        else:
            missing.append({'requester': canonical, 'library': library, 'search': search})
    return canonical


for entry in entries:
    visit(entry)
result = {'entrypoints': entries, 'elfs': visited, 'missing': missing,
          'limitation': 'ELF DT_NEEDED only; dlopen modules, symbols, devices and runtime syscalls need dynamic tests.'}
(evidence / 'elf-dependencies.json').write_text(json.dumps(result, indent=2) + '\n')
modules = [str(p.relative_to(root)) for folder in ['usr/lib/libweston-14', 'usr/lib/weston']
           for p in (root / folder).iterdir()]
(evidence / 'weston-modules.json').write_text(json.dumps(sorted(modules), indent=2) + '\n')
chromium = (root / 'usr/lib/chromium/chromium').read_bytes()
patterns = [rb'[^\x00\n]{0,70}ui/ozone/platform/wayland[^\x00\n]{0,150}',
            rb'[^\x00\n]{0,70}OzonePlatformWayland[^\x00\n]{0,150}']
strings = sorted({m.group().decode(errors='replace') for pattern in patterns for m in re.finditer(pattern, chromium)})
(evidence / 'chromium-wayland-strings.txt').write_text('\n'.join(strings) + '\n')
libweston = guest_resolve('/usr/lib/libweston-14.so.0')
symbols = subprocess.run(['readelf', '-Ws', str(libweston)], capture_output=True, text=True, check=True).stdout
pixman = '\n'.join(line for line in symbols.splitlines() if 'pixman_renderer' in line)
(evidence / 'pixman-symbols.txt').write_text(pixman + '\n')
if missing or not strings or not pixman:
    raise SystemExit(f'Audit failed: missing={missing}, wayland strings={len(strings)}, pixman={bool(pixman)}')
print(f'{len(visited)} AArch64 ELF files, no missing DT_NEEDED dependencies; {len(strings)} Chromium Wayland strings; pixman renderer symbol present.')
