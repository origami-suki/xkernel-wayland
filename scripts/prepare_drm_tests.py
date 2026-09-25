#!/usr/bin/env python3
"""Build M2 display/session probes with recorded inputs in a new rootfs working copy."""
import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile

from run_guest import ROOT, check_disk, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'work/images/rights-m1-final.img')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--headers', type=Path, default=Path('/usr/include/libdrm'))
    args = parser.parse_args()
    base = check_disk(args.base)
    output = args.output.absolute()
    if (output.exists() or output.is_symlink()
            or not output.parent.resolve().is_relative_to(ROOT / 'work/images')):
        raise ValueError('output must be a new file under work/images')
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)

    def run(argv):
        argv = list(map(str, argv))
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with (evidence / 'commands.log').open('ab') as log:
            log.write((shlex.join(argv) + '\n').encode() + result.stdout
                      + f'\nexit={result.returncode}\n'.encode())
        result.check_returncode()
        return result.stdout

    gcc = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    source = ROOT / 'tests/drm/probe.c'
    binary = evidence / 'drm-probe'
    (evidence / 'probe.c').write_bytes(source.read_bytes())
    headers = evidence / 'headers'
    headers.mkdir()
    for name in ['drm.h', 'drm_mode.h', 'drm_fourcc.h']:
        (headers / name).write_bytes((args.headers / name).read_bytes())
    run([gcc, '--version'])
    run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-static', '-I' + str(headers), source, '-o', binary])
    (evidence / 'drm-probe.readelf.txt').write_bytes(run(['readelf', '-lW', binary]))
    signal_source = ROOT / 'tests/signalfd/wakeup.c'
    signal_binary = evidence / 'signalfd-probe'
    (evidence / 'signalfd-probe.c').write_bytes(signal_source.read_bytes())
    run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-static', signal_source, '-o', signal_binary])
    before = sha256(base)
    run(['cp', '--reflink=always', base, output])
    with tempfile.TemporaryDirectory(prefix='xk-drm-mount-') as directory:
        run(['sudo', '-n', 'mount', '-o', 'loop', output, directory])
        try:
            target = Path(directory) / 'opt/ict-tests/drm'
            run(['sudo', '-n', 'mkdir', '-p', target])
            run(['sudo', '-n', 'install', '-m', '0755', binary, target / 'drm-probe'])
            run(['sudo', '-n', 'install', '-m', '0755', signal_binary, target / 'signalfd-probe'])
            if sha256(signal_binary) != sha256(target / 'signalfd-probe'):
                raise ValueError('guest signal probe differs from built binary')
            if sha256(binary) != sha256(target / 'drm-probe'):
                raise ValueError('guest probe differs from built binary')
        finally:
            run(['sudo', '-n', 'umount', directory])
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'compiler': str(gcc), 'compiler_sha256': sha256(gcc),
        'source': str(source.relative_to(ROOT)), 'source_sha256': sha256(source),
        'binary_sha256': sha256(binary),
        'signalfd_source_sha256': sha256(signal_source),
        'signalfd_binary_sha256': sha256(signal_binary),
        'headers': {p.name: sha256(p) for p in headers.iterdir()},
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
