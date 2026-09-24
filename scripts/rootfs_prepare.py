#!/usr/bin/env python3
"""Rebuild the official disk plus a locked, signed Wayland package increment.

Uses explicit qemu-user calls only. Never touches host binfmt configuration.
Run as the normal project user; sudo is used only for mount/guest disk writes.
"""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]
QEMU = '/usr/bin/qemu-aarch64-static'


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def require_hash(path, expected):
    got = digest(path)
    if got != expected:
        raise RuntimeError(f'SHA256 mismatch: {path}: {got} != {expected}')
    return got


def run(cmd, evidence=None, check=True, timeout=120):
    try:
        result = subprocess.run([str(c) for c in cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if evidence:
            Path(evidence).write_bytes(('command=' + json.dumps([str(c) for c in cmd]) + '\n').encode()
                + (error.stdout or b'') + f'\ntimeout=true timeout_seconds={timeout}\n'.encode())
        raise
    if evidence:
        Path(evidence).write_bytes(('command=' + json.dumps([str(c) for c in cmd]) + '\n').encode()
                                  + result.stdout + f'\nexit={result.returncode}\n'.encode())
    if check and result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {cmd}\n{result.stdout.decode(errors="replace")}')
    return result


def packages(path):
    result = {}
    for block in path.read_text().split('\n\n'):
        record = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
        if 'P' in record:
            result[record['P']] = record['V']
    return result


def sudo_write(path, data, mode='0644'):
    with tempfile.NamedTemporaryFile() as f:
        f.write(data.encode() if isinstance(data, str) else data)
        f.flush()
        run(['sudo', '-n', 'install', '-D', '-m', mode, f.name, path])


def create_base(output, asset, lock):
    # Exclusive creation makes accidental overwrites (including another working disk) impossible.
    if output.exists() or output.is_symlink():
        raise RuntimeError(f'Refusing to overwrite existing image: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with lzma.open(asset, 'rb') as src, output.open('xb') as dst:
        for chunk in iter(lambda: src.read(4 * 1024 * 1024), b''):
            dst.write(chunk)
            h.update(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    if h.hexdigest() != lock['source_uncompressed_sha256']:
        raise RuntimeError('Uncompressed base hash mismatch; failed output retained for diagnosis')
    return h.hexdigest()


def fetch_locked(lock, cache):
    result = []
    for package in lock['packages']:
        path = cache / package['repository'] / 'aarch64' / package['filename']
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix('.download')
            with urllib.request.urlopen(package['url'], timeout=60) as src, temporary.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            require_hash(temporary, package['sha256'])
            temporary.rename(path)
        require_hash(path, package['sha256'])
        result.append(path)
    return result


def install_overlay(root, pages, evidence):
    # Preserve the source boot profile and welcome page; the new init profile never launches X11.
    source = root / 'etc/ict-wayland/source'
    run(['sudo', '-n', 'mkdir', '-p', source])
    for original in ['etc/inittab', 'etc/init.d/kiosk-boot', 'usr/local/bin/x11-session']:
        run(['sudo', '-n', 'cp', '-a', root / original, source / Path(original).name])
    groups = (root / 'etc/group').read_text().splitlines()
    used = {int(x.split(':')[2]) for x in groups}
    for name, gid, members in [('seat', 101, 'kiosk'), ('weston-launch', 102, '')]:
        if any(x.startswith(name + ':') for x in groups) or gid in used:
            raise RuntimeError(f'Unexpected original group collision: {name}:{gid}')
        groups.append(f'{name}:x:{gid}:{members}')
    # These are the only functional package pre-install steps (reviewed and retained in the lock).
    sudo_write(root / 'etc/group', '\n'.join(groups) + '\n')
    for path in sorted((REPO / 'guest').rglob('*')):
        if path.is_file():
            sudo_write(root / path.relative_to(REPO / 'guest'), path.read_bytes(),
                       '0755' if os.access(path, os.X_OK) else '0644')
    with tarfile.open(pages, 'r:xz') as archive:
        expected = {'index.html', 'interaction.html', 'layout.html'}
        members = archive.getmembers()
        if {m.name for m in members} != expected or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected official page archive members')
        page_hashes = {}
        for member in members:
            data = archive.extractfile(member).read()
            destination = root / 'opt/ict-testpages' / member.name
            sudo_write(destination, data)
            page_hashes[member.name] = hashlib.sha256(data).hexdigest()
            require_hash(destination, page_hashes[member.name])
    (evidence / 'pages.json').write_text(json.dumps(page_hashes, indent=2) + '\n')
    require_hash(root / 'usr/share/kiosk/index.html', 'd34337bee3341cfd1feb775d68dc5c360e862b63d6fe126d2434a01754734f67')


def user_probes(root, evidence):
    # chroot isolates guest library resolution; only this copied host ELF is executed natively.
    helper = root / 'tmp/xkernel-qemu-user-probe'
    if helper.exists():
        raise RuntimeError(f'Unexpected probe helper exists: {helper}')
    run(['sudo', '-n', 'cp', QEMU, helper])
    probes = {
        'busybox-help': ['/bin/busybox', '--help'],
        'musl-loader': ['/lib/ld-musl-aarch64.so.1'],
        'weston-version': ['/usr/bin/weston', '--version'],
        'weston-help': ['/usr/bin/weston', '--help'],
        'seatd-version': ['/usr/bin/seatd', '-v'],
        'seatd-help': ['/usr/bin/seatd', '-h'],
        'chromium-version': ['/usr/lib/chromium/chromium', '--version'],
    }
    results = {}
    try:
        for name, args in probes.items():
            result = run(['sudo', '-n', 'chroot', root, '/tmp/xkernel-qemu-user-probe', *args],
                         evidence / (name + '.txt'), check=False, timeout=30)
            expected = 1 if name == 'musl-loader' else 0
            results[name] = {'exit': result.returncode, 'expected': expected}
            if result.returncode != expected:
                raise RuntimeError(f'Unexpected user probe exit: {name}={result.returncode}')
    finally:
        run(['sudo', '-n', 'rm', helper])
    (evidence / 'user-probe-results.json').write_text(json.dumps(results, indent=2) + '\n')


def prepare(root, lock, apk_paths, pages, evidence):
    for key in lock['trusted_signing_keys']:
        require_hash(root / 'etc/apk/keys' / key['name'], key['sha256'])
    before = packages(root / 'lib/apk/db/installed')
    shutil.copyfile(root / 'lib/apk/db/installed', evidence / 'installed.before')
    shutil.copyfile(root / 'etc/apk/world', evidence / 'world.before')
    apk = ['sudo', '-n', QEMU, '-L', root, '-E', f'LD_LIBRARY_PATH={root}/usr/lib:{root}/lib',
           root / 'sbin/apk', '--root', root, '--keys-dir', root / 'etc/apk/keys',
           '--repositories-file', '/dev/null', '--no-network']
    run(apk + ['verify', *apk_paths], evidence / 'apk-verify.txt')
    add = apk + ['--no-scripts', '--no-commit-hooks', 'add']
    simulation = run(add + ['--simulate', *apk_paths], evidence / 'solver.txt').stdout.decode()
    if re.search(r'\) (Upgrading|Downgrading|Purging|Reinstalling) ', simulation):
        raise RuntimeError('Solver would modify an existing package; refusing install')
    run(add + apk_paths, evidence / 'apk-install.txt')
    after = packages(root / 'lib/apk/db/installed')
    if any(after.get(name) != version for name, version in before.items()):
        raise RuntimeError('Existing package versions changed')
    added = {name: version for name, version in after.items() if name not in before}
    if added != {p['name']: p['version'] for p in lock['packages']}:
        raise RuntimeError(f'Unexpected package increment: {added}')
    shutil.copyfile(root / 'lib/apk/db/installed', evidence / 'installed.after')
    (evidence / 'package-changes.json').write_text(json.dumps({'before_count': len(before),
        'after_count': len(after), 'added': added, 'changed': [], 'removed': []}, indent=2) + '\n')
    install_overlay(root, pages, evidence)
    user_probes(root, evidence)
    run(['python3', REPO / 'scripts/rootfs_audit.py', root, evidence], evidence / 'audit-run.txt')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-only', action='store_true', help='Verify and decompress the official disk only')
    parser.add_argument('--output', type=Path, default=REPO / 'work/images/wayland-prepared.img')
    parser.add_argument('--cache', type=Path, default=REPO / 'work/wayland-cache')
    parser.add_argument('--evidence', type=Path, default=REPO / 'artifacts/rootfs-rebuild')
    args = parser.parse_args()
    output, cache, evidence = args.output.absolute(), args.cache.resolve(), args.evidence.absolute()
    baseline = json.loads((REPO / 'config/baseline.json').read_text())
    lock = json.loads((REPO / 'config/rootfs-wayland.lock.json').read_text())
    assets = {entry['role']: entry for entry in baseline['assets']}
    asset = (REPO / assets['rootfs']['path']).resolve()
    pages = (REPO / assets['testpages']['path']).resolve()
    require_hash(asset, assets['rootfs']['sha256'])
    require_hash(pages, assets['testpages']['sha256'])
    if output.exists() or output.is_symlink():
        parser.error(f'Refusing to overwrite existing image: {output}')
    if evidence.exists() and any(evidence.iterdir()):
        parser.error(f'Refusing to overwrite nonempty evidence directory: {evidence}')
    evidence.mkdir(parents=True, exist_ok=True)
    apk_paths = [] if args.base_only else fetch_locked(lock, cache)
    base_hash = create_base(output, asset, lock)
    if not args.base_only:
        run(['sudo', '-n', 'true'])
        mount = Path(tempfile.mkdtemp(prefix='wayland-rootfs-', dir=REPO / 'work'))
        mounted = False
        try:
            run(['sudo', '-n', 'mount', '-o', 'loop', output, mount]); mounted = True
            prepare(mount, lock, apk_paths, pages, evidence)
        finally:
            if mounted:
                # Do not remove a mountpoint unless the actual unmount succeeded.
                run(['sudo', '-n', 'umount', mount])
            mount.rmdir()
    result = {'image': str(output), 'base_sha256': base_hash, 'sha256': digest(output),
              'bytes': output.stat().st_size, 'base_only': args.base_only,
              'qemu_user_version': None if args.base_only else run([QEMU, '--version']).stdout.decode().strip()}
    (evidence / 'image.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
