#!/usr/bin/env python3
"""Build small AArch64 ELF layout fixtures and inject into a new reflink disk."""
import argparse
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile

from run_guest import ROOT, check_disk, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'work/images/wayland-m0.img')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    base = check_disk(args.base)
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.resolve().is_relative_to(ROOT / 'work/images'):
        raise ValueError('output must be a new file under work/images')
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)
    commands = []

    def run(argv):
        commands.append(shlex.join(map(str, argv)))
        result = subprocess.run(list(map(str, argv)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with (evidence / 'commands.log').open('ab') as log:
            log.write((commands[-1] + '\n').encode() + result.stdout + f'\nexit={result.returncode}\n'.encode())
        result.check_returncode()
        return result.stdout

    gcc = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    run([gcc, '--version'])
    source = ROOT / 'tests/elf-layout'
    fixtures = evidence / 'fixtures'
    fixtures.mkdir()
    for name, flags in [('large-pie', ['-fPIE', '-pie']), ('small-pie', ['-fPIE', '-pie', '-DBSS_BYTES=8192']),
                        ('static-exec', ['-static', '-DBSS_BYTES=8192'])]:
        run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', *flags, source / 'large-pie.c', '-o', fixtures / name])
    run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-static', source / 'exec-check.c', '-o', fixtures / 'exec-check'])
    original = bytearray((fixtures / 'small-pie').read_bytes())
    phoff = struct.unpack_from('<Q', original, 32)[0]
    phsize, phnum = struct.unpack_from('<HH', original, 54)
    headers = [phoff + i * phsize for i in range(phnum)]
    load = next(p for p in headers if struct.unpack_from('<I', original, p)[0] == 1)
    interp = next(p for p in headers if struct.unpack_from('<I', original, p)[0] == 3)
    cases = {
        'bad-filesz': (load + 40, '<Q', 1),
        'bad-overflow': (load + 40, '<Q', 0xffffffffffffffff),
        'bad-offset': (load + 8, '<Q', 1),
        'bad-interp-short': (interp + 8, '<Q', len(original) - 1),
        'bad-phentsize': (54, '<H', 0),
        'bad-phoverflow': (32, '<Q', 0xfffffffffffffff0),
    }
    for name, (offset, kind, value) in cases.items():
        data = original.copy()
        struct.pack_into(kind, data, offset, value)
        (fixtures / name).write_bytes(data)
        (fixtures / name).chmod(0o755)
    script = '#!/bin/sh\nset -eu\ncd /opt/ict-tests/elf-layout\n'
    for name in ['small-pie', 'static-exec', 'large-pie']:
        script += f'./exec-check success ./{name}\n'
    script += '/usr/lib/chromium/chromium --version\nweston --version\necho ELF_SUITE_OK\n'
    (fixtures / 'run.sh').write_text(script)
    (fixtures / 'run.sh').chmod(0o755)
    # Linux reports an execve errno only for these three malformed images. The
    # remaining three are accepted by the kernel and fail later at runtime, so
    # they are observed separately instead of asserted as rejected.
    malformed = '#!/bin/sh\nset -eu\ncd /opt/ict-tests/elf-layout\n'
    for name in ['bad-interp-short', 'bad-phentsize', 'bad-phoverflow']:
        malformed += f'./exec-check reject ./{name}\n'
    malformed += 'echo ELF_MALFORMED_OK\n'
    (fixtures / 'run-malformed.sh').write_text(malformed)
    (fixtures / 'run-malformed.sh').chmod(0o755)
    observed = '#!/bin/sh\ncd /opt/ict-tests/elf-layout || exit 99\n'
    for name in ['bad-filesz', 'bad-overflow', 'bad-offset']:
        observed += (f'printf \'XK_INVALID_BEGIN=%s\\n\' "{name}"\n'
                     f'./exec-check reject "./{name}"\n'
                     f'printf \'XK_INVALID_RESULT=%s rc=%s\\n\' "{name}" "$?"\n')
    observed += 'printf \'XK_INVALID_OBSERVATIONS_COMPLETE\\n\'\n'
    (fixtures / 'run-observed.sh').write_text(observed)
    (fixtures / 'run-observed.sh').chmod(0o755)
    for name in ['large-pie', 'small-pie', 'static-exec']:
        (evidence / (name + '.readelf.txt')).write_bytes(run(['readelf', '-lW', fixtures / name]))
    before = sha256(base)
    run(['cp', '--reflink=always', base, output])
    with tempfile.TemporaryDirectory(prefix='xk-elf-mount-') as directory:
        run(['sudo', '-n', 'mount', '-o', 'loop', output, directory])
        try:
            target = Path(directory) / 'opt/ict-tests/elf-layout'
            run(['sudo', '-n', 'mkdir', '-p', target])
            for path in sorted(fixtures.iterdir()):
                run(['sudo', '-n', 'install', '-m', '0755', path, target / path.name])
                if sha256(path) != sha256(target / path.name):
                    raise ValueError('guest fixture hash mismatch')
        finally:
            run(['sudo', '-n', 'umount', directory])
    run(['e2fsck', '-fn', output])
    assert sha256(base) == before, 'base disk changed'
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output), 'output_sha256': sha256(output),
        'storage': 'reflink', 'compiler': str(gcc), 'compiler_sha256': sha256(gcc),
        'sources': {str(p.relative_to(ROOT)): sha256(p) for p in sorted(source.iterdir())},
        'fixtures': {p.name: sha256(p) for p in sorted(fixtures.iterdir())},
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
