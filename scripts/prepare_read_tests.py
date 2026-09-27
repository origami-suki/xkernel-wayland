#!/usr/bin/env python3
"""Compile the project read probe and install it in a new reflink work disk."""
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
    parser.add_argument('--threadscale', action='store_true', help='only resources.pak batch/clock-only with 0,8,32 blocking helpers')
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

    source = ROOT / 'tests/fs/read-path-cost.c'
    binary = evidence / 'read-path-cost'
    compiler = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    (evidence / source.name).write_bytes(source.read_bytes())
    (evidence / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    run([compiler, '--version'])
    run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread', '-static', source, '-o', binary])
    before = sha256(base)
    resources = {}
    for name in ['resources.pak', 'chrome_100_percent.pak', 'chrome_200_percent.pak']:
        local = evidence / name
        run(['debugfs', '-R', f'dump /usr/lib/chromium/{name} {local}', base])
        data = local.read_bytes()
        h = 14695981039346656037
        for byte in data:
            h = ((h ^ byte) * 1099511628211) & ((1 << 64) - 1)
        resources[name] = {'bytes': len(data), 'sha256': sha256(local), 'fnv64': f'{h:016x}',
                           'expected_4096_calls_including_eof': (len(data) + 4095) // 4096 + 1}
    write_json(evidence / 'resources.json', resources)
    lines = ['#!/bin/sh', 'set -eu', 'echo READ_PROBE_BEGIN', 'id',
             'sha256sum /usr/lib/chromium/resources.pak /usr/lib/chromium/chrome_100_percent.pak /usr/lib/chromium/chrome_200_percent.pak']
    for name, info in resources.items():
        if args.threadscale and name != 'resources.pak':
            continue
        lines.append(f'echo READ_FILE {name}')
        cases = [('batch', 4096, 0), ('per-call', 4096, 0),
                 ('clock-only', 4096, 0), ('batch', 65536, 0), ('batch', 4096, 1)]
        if args.threadscale:
            cases = [('batch', 4096, 0), ('clock-only', 4096, 0)]
        helper_counts = [0, 8, 32] if args.threadscale else [0]
        for helpers in helper_counts:
            for mode, chunk, prefault in cases:
                suffix = f' {helpers}' if args.threadscale else ''
                lines.append(f'/opt/ict-tests/read-path-cost /usr/lib/chromium/{name} {mode} {chunk} {prefault} {info["fnv64"]}{suffix}')
    lines.extend(['sha256sum /usr/lib/chromium/resources.pak /usr/lib/chromium/chrome_100_percent.pak /usr/lib/chromium/chrome_200_percent.pak', 'echo READ_PROBE_END'])
    runner = evidence / 'read-path-cost-run.sh'
    runner.write_text('\n'.join(lines) + '\n')
    run(['cp', '--reflink=always', base, output])
    target = '/opt/ict-tests/read-path-cost'
    run(['debugfs', '-w', '-R', 'rm ' + target, output])
    run(['debugfs', '-w', '-R', f'write {binary} {target}', output])
    run(['debugfs', '-w', '-R', f'set_inode_field {target} mode 0100755', output])
    readback = evidence / 'read-path-cost.readback'
    run(['debugfs', '-R', f'dump {target} {readback}', output])
    if sha256(binary) != sha256(readback):
        raise ValueError('deployed probe differs from built binary')
    target_script = '/opt/ict-tests/read-path-cost-run.sh'
    run(['debugfs', '-w', '-R', f'write {runner} {target_script}', output])
    script_readback = evidence / 'runner.readback'
    run(['debugfs', '-R', f'dump {target_script} {script_readback}', output])
    if sha256(runner) != sha256(script_readback):
        raise ValueError('deployed shell script differs')
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'source_sha256': sha256(source), 'binary_sha256': sha256(binary),
        'compiler': str(compiler), 'compiler_sha256': sha256(compiler),
        'threadscale': args.threadscale, 'resources': resources, 'runner_sha256': sha256(runner),
        'changes': 'project C probe and project shell script only; resources unchanged',
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
