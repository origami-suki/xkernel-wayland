#!/usr/bin/env python3
"""Build Chromium prerequisite probes and deploy a recorded observation fixture."""
import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile

from run_guest import ROOT, check_disk, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'work/images/wayland-m2-final.img')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
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

    source = ROOT / 'tests/chromium/session.sh'
    (evidence / 'session.sh').write_bytes(source.read_bytes())
    run(['sh', '-n', source])
    gcc = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    run([gcc, '--version'])
    inputs = {source.name: sha256(source)}
    binaries = {}
    for name, source_name, linkage in (
            ('userns-probe', 'userns-probe', ['-static']),
            ('proc-task', 'proc-task', []),
            ('proc-task-static', 'proc-task', ['-static']),
            ('unix-credentials', 'unix-credentials', ['-static']),
            ('no-new-privs', 'no-new-privs', ['-static']),
            ('scheduler-query', 'scheduler-query', []),
            ('scheduler-query-static', 'scheduler-query', ['-static'])):
        c_source = ROOT / 'tests/chromium' / (source_name + '.c')
        (evidence / c_source.name).write_bytes(c_source.read_bytes())
        inputs[c_source.name] = sha256(c_source)
        binary = evidence / name
        run([gcc, '-Wall', '-Wextra', '-Werror', '-O2', '-pthread',
             *linkage, c_source, '-o', binary])
        (evidence / (name + '.readelf.txt')).write_bytes(run(['readelf', '-lW', binary]))
        binaries[name] = binary
    before = sha256(base)
    run(['cp', '--reflink=always', base, output])
    with tempfile.TemporaryDirectory(prefix='xk-chromium-mount-') as directory:
        run(['sudo', '-n', 'mount', '-o', 'loop', output, directory])
        try:
            target = Path(directory) / 'opt/ict-tests/chromium'
            run(['sudo', '-n', 'mkdir', '-p', target])
            run(['sudo', '-n', 'install', '-m', '0755', source, target / 'session.sh'])
            if sha256(source) != sha256(target / 'session.sh'):
                raise ValueError('guest script differs from source')
            for name, binary in binaries.items():
                run(['sudo', '-n', 'install', '-m', '0755', binary, target / name])
                if sha256(binary) != sha256(target / name):
                    raise ValueError('guest probe differs from built binary')
            fixture = target / 'no-new-privs-setid'
            run(['sudo', '-n', 'install', '-o', '0', '-g', '0', '-m', '6755',
                 binaries['no-new-privs'], fixture])
            stat = fixture.stat()
            if (stat.st_uid, stat.st_gid, stat.st_mode & 0o7777) != (0, 0, 0o6755):
                raise ValueError('set-ID control fixture metadata differs')
            if sha256(fixture) != sha256(binaries['no-new-privs']):
                raise ValueError('set-ID control fixture binary differs')
        finally:
            run(['sudo', '-n', 'umount', directory])
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'sources_sha256': inputs,
        'binaries_sha256': {name: sha256(binary) for name, binary in binaries.items()},
        'compiler': str(gcc), 'compiler_sha256': sha256(gcc),
        'setid_fixture': {'owner': 0, 'group': 0, 'mode': '6755',
                          'source_binary': 'no-new-privs'},
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
