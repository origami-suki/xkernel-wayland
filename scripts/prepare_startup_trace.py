#!/usr/bin/env python3
"""Install only project diagnostic scripts into a new rootfs work copy."""
import argparse
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

from run_guest import ROOT, check_disk, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path,
                        default=ROOT / 'work/images/chromium-m1-fcntl-unknown-v1.img')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    base = check_disk(args.base)
    output = args.output.absolute()
    if (output.exists() or output.is_symlink()
            or not output.parent.resolve().is_relative_to(ROOT / 'work/images')):
        raise ValueError('output must be a new work/images file')
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)

    def run(argv):
        argv = list(map(str, argv))
        result = subprocess.run(argv, capture_output=True)
        with (evidence / 'commands.log').open('ab') as log:
            log.write((shlex.join(argv) + '\n').encode() + result.stdout + result.stderr
                      + f'\nexit={result.returncode}\n'.encode())
        result.check_returncode()

    sources = [ROOT / 'tests/chromium' / name
               for name in ('trace-startup.sh', 'trace-export.sh')]
    for source in sources:
        run(['sh', '-n', source])
        shutil.copy2(source, evidence / source.name)
    before = sha256(base)
    run(['cp', '--reflink=always', '--sparse=auto', base, output])
    with tempfile.TemporaryDirectory(prefix='xk-trace-mount-') as directory:
        run(['sudo', '-n', 'mount', '-o', 'loop', output, directory])
        try:
            target = Path(directory) / 'opt/ict-tests/chromium'
            if sha256(target / 'session.sh') != sha256(ROOT / 'tests/chromium/session.sh'):
                raise ValueError('base session does not match the known project script')
            for source in sources:
                run(['sudo', '-n', 'install', '-m', '0755', source, target / source.name])
                if sha256(source) != sha256(target / source.name):
                    raise ValueError('deployed script hash mismatch')
        finally:
            run(['sudo', '-n', 'umount', directory])
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'sources_sha256': {source.name: sha256(source) for source in sources},
        'changes': 'two project shell scripts; third-party programs and test pages unchanged',
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
