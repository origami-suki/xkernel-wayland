#!/usr/bin/env python3
"""Compile the project munmap probe and install it in a new reflink work disk."""
import argparse
from pathlib import Path
import shlex
import subprocess

from run_guest import ROOT, check_disk, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    base = check_disk(args.base)
    output, evidence = args.output.absolute(), args.evidence.absolute()
    if (output.exists() or output.is_symlink()
            or not output.parent.resolve().is_relative_to((ROOT / 'work/images').resolve())):
        raise ValueError('output must be a new work/images file')
    if any(c.isspace() or c in '\\"' for c in str(evidence)):
        raise ValueError('debugfs evidence path cannot contain whitespace/quotes')
    evidence.mkdir(parents=True, exist_ok=False)

    def run(argv):
        argv = list(map(str, argv))
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with (evidence / 'commands.log').open('ab') as log:
            log.write((shlex.join(argv) + '\n').encode() + result.stdout
                      + f'\nexit={result.returncode}\n'.encode())
        result.check_returncode()

    source = ROOT / 'tests/mm/munmap-sparse.c'
    binary = evidence / 'munmap-sparse'
    compiler = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    (evidence / source.name).write_bytes(source.read_bytes())
    (evidence / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    run([compiler, '--version'])
    run([compiler, '-O2', '-Wall', '-Wextra', '-Werror', '-static', source, '-o', binary])
    before = sha256(base)
    run(['cp', '--reflink=always', base, output])
    target = '/opt/ict-tests/munmap-sparse'
    run(['debugfs', '-w', '-R', 'rm ' + target, output])
    run(['debugfs', '-w', '-R', f'write {binary} {target}', output])
    run(['debugfs', '-w', '-R', f'set_inode_field {target} mode 0100755', output])
    readback = evidence / 'munmap-sparse.readback'
    run(['debugfs', '-R', f'dump {target} {readback}', output])
    if sha256(binary) != sha256(readback):
        raise ValueError('deployed probe differs from built binary')
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'source_sha256': sha256(source), 'binary_sha256': sha256(binary),
        'compiler': str(compiler), 'compiler_sha256': sha256(compiler),
        'changes': 'project C probe only',
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
