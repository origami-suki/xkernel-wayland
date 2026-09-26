#!/usr/bin/env python3
"""Install only project profiler probes/scripts into a new reflink work disk."""
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
    if (output.exists() or output.is_symlink() or not output.parent.resolve().is_relative_to((ROOT / 'work/images').resolve())):
        raise ValueError('output must be a new work/images file')
    evidence.mkdir(parents=True, exist_ok=False)
    if any(c.isspace() or c in '\"' for c in str(evidence)):
        raise ValueError('debugfs evidence path cannot contain whitespace/quotes')

    def run(argv):
        argv = list(map(str, argv))
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with (evidence / 'commands.log').open('ab') as log:
            log.write((shlex.join(argv) + '\n').encode() + result.stdout + f'\nexit={result.returncode}\n'.encode())
        result.check_returncode()
        return result.stdout

    gcc = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    run([gcc, '--version'])
    source = ROOT / 'tests/observe/syscall-profile.c'
    binary = evidence / 'syscall-profile'
    (evidence / source.name).write_bytes(source.read_bytes())
    run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-pthread', '-static', source, '-o', binary])
    script = ROOT / 'tests/chromium/profile-startup.sh'
    run(['sh', '-n', script])
    saved_script = evidence / script.name
    saved_script.write_bytes(script.read_bytes())
    before = sha256(base)
    run(['cp', '--reflink=always', base, output])
    files = [(binary, '/opt/ict-tests/syscall-profile'),
             (saved_script, '/opt/ict-tests/chromium/profile-startup.sh')]
    for name in ('thread-clock', 'cpu-accounting'):
        probe_source = ROOT / 'tests/observe' / (name + '.c')
        probe_binary = evidence / name
        (evidence / probe_source.name).write_bytes(probe_source.read_bytes())
        run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-pthread', '-static', probe_source, '-o', probe_binary])
        files.append((probe_binary, '/opt/ict-tests/' + name))
    for src, dest in files:
        run(['debugfs', '-w', '-R', 'rm ' + dest, output])
        run(['debugfs', '-w', '-R', f'write {src} {dest}', output])
        run(['debugfs', '-w', '-R', f'set_inode_field {dest} mode 0100755', output])
        back = evidence / (src.name + '.readback')
        run(['debugfs', '-R', f'dump {dest} {back}', output])
        if sha256(src) != sha256(back):
            raise ValueError('deployment differs from source')
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'source_sha256': sha256(source), 'compiler_sha256': sha256(gcc),
        'files': {dest: sha256(src) for src, dest in files},
        'changes': 'project C probe and session script only',
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
