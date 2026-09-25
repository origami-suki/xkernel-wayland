#!/usr/bin/env python3
"""Compile project C clock probes and install them in a new reflink work disk."""
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
    output = args.output.absolute()
    if (output.exists() or output.is_symlink()
            or not output.parent.resolve().is_relative_to((ROOT / 'work/images').resolve())):
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
    run([gcc, '--version'])
    sources, binaries = {}, {}
    for name in ('thread-clock', 'cpu-accounting'):
        source = ROOT / 'tests/observe' / (name + '.c')
        (evidence / source.name).write_bytes(source.read_bytes())
        sources[name] = sha256(source)
        binary = evidence / name
        run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-pthread', '-static', source, '-o', binary])
        (evidence / (name + '.readelf.txt')).write_bytes(run(['readelf', '-lW', binary]))
        binaries[name] = binary
    if any(c.isspace() or c in '\"' for c in str(evidence)):
        raise ValueError('evidence path must have no whitespace or quotes for debugfs')
    before = sha256(base)
    run(['cp', '--reflink=always', base, output])
    # Offline debugfs writes avoid desktop automount races around loop devices.
    # The base must already contain the project's /opt/ict-tests directory.
    for name, binary in binaries.items():
        target = '/opt/ict-tests/' + name
        run(['debugfs', '-w', '-R', 'rm ' + target, output])
        run(['debugfs', '-w', '-R', f'write {binary} {target}', output])
        run(['debugfs', '-w', '-R', f'set_inode_field {target} mode 0100755', output])
        restored = evidence / (name + '.readback')
        run(['debugfs', '-R', f'dump {target} {restored}', output])
        if not restored.exists() or sha256(binary) != sha256(restored):
            raise ValueError('deployed probe differs from built binary')
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before,
        'output': str(output), 'output_sha256': sha256(output), 'storage': 'reflink',
        'sources_sha256': sources,
        'binaries_sha256': {name: sha256(binary) for name, binary in binaries.items()},
        'compiler': str(gcc), 'compiler_sha256': sha256(gcc),
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
