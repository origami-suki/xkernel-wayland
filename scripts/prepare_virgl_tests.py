#!/usr/bin/env python3
"""Build project virgl probes and deploy to a new rootfs work copy."""
import argparse
from pathlib import Path
import re
import shlex
import subprocess

from run_guest import ROOT, check_disk, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--headers', type=Path, default=Path('/usr/include/libdrm'))
    args = parser.parse_args()
    base = check_disk(args.base)
    output = args.output.absolute()
    evidence = args.evidence.absolute()
    if (output.exists() or output.is_symlink()
            or not output.parent.resolve().is_relative_to((ROOT / 'work/images').resolve())):
        raise ValueError('output must be a new file under work/images')
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
        return result.stdout.decode(errors='replace')

    compiler = ROOT / 'work/toolchains/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc'
    (evidence / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    headers = evidence / 'headers'
    headers.mkdir()
    for name in ('drm.h', 'drm_mode.h', 'virtgpu_drm.h'):
        (headers / name).write_bytes((args.headers / name).read_bytes())
    for folder, names in {'EGL': ('egl.h', 'eglext.h', 'eglplatform.h'),
                          'GLES2': ('gl2.h', 'gl2platform.h'),
                          'KHR': ('khrplatform.h',)}.items():
        (headers / folder).mkdir()
        for name in names:
            (headers / folder / name).write_bytes((Path('/usr/include') / folder / name).read_bytes())
    (headers / 'gbm.h').write_bytes(Path('/usr/include/gbm.h').read_bytes())
    run([compiler, '--version'])
    probes = {}
    for source_name, binary_name in (('virgl-query.c', 'virgl-query'),
                                    ('lifetime.c', 'drm-lifetime'),
                                    ('virgl-clear.c', 'virgl-clear'),
                                    ('prime-sharing.c', 'prime-sharing'),
                                    ('guest-egl.c', 'guest-egl')):
        source = ROOT / 'tests/drm' / source_name
        binary = evidence / binary_name
        (evidence / source_name).write_bytes(source.read_bytes())
        flags = ['-ldl'] if binary_name == 'guest-egl' else ['-static']
        run([compiler, '-Wall', '-Wextra', '-Werror', '-O2',
             '-I' + str(headers), source, '-o', binary, *flags])
        probes[binary_name] = {'source': str(source.relative_to(ROOT)),
                               'source_sha256': sha256(source), 'binary_sha256': sha256(binary)}
    before = sha256(base)
    run(['e2fsck', '-fn', base])
    run(['cp', '--reflink=always', base, output])
    # check_disk also excludes symlink escapes and hardlinks for the new copy.
    check_disk(output)
    # debugfs can return 0 after a failed mkdir and leave an orphan inode.
    # Only create a missing directory, then verify its actual type.
    directory = '/opt/ict-tests/drm'
    info = run(['debugfs', '-R', 'stat ' + directory, output])
    if not re.search(r'Inode:\s*\d+\s+Type:\s*directory\b', info):
        if 'File not found by ext2_lookup' not in info:
            raise ValueError('probe path is not a directory: ' + info)
        run(['debugfs', '-w', '-R', 'mkdir ' + directory, output])
        info = run(['debugfs', '-R', 'stat ' + directory, output])
        if not re.search(r'Inode:\s*\d+\s+Type:\s*directory\b', info):
            raise ValueError('probe directory creation failed: ' + info)
    for binary_name in probes:
        target = '/opt/ict-tests/drm/' + binary_name
        run(['debugfs', '-w', '-R', 'rm ' + target, output])
        run(['debugfs', '-w', '-R', f'write {evidence / binary_name} {target}', output])
        run(['debugfs', '-w', '-R', f'set_inode_field {target} mode 0100755', output])
        readback = evidence / (binary_name + '.readback')
        run(['debugfs', '-R', f'dump {target} {readback}', output])
        if sha256(readback) != probes[binary_name]['binary_sha256']:
            raise ValueError('deployed probe differs from built binary: ' + binary_name)
    run(['e2fsck', '-fn', output])
    if sha256(base) != before:
        raise ValueError('base image changed')
    write_json(evidence / 'manifest.json', {
        'base': str(base), 'base_sha256': before, 'output': str(output),
        'output_sha256': sha256(output), 'storage': 'reflink',
        'compiler': str(compiler), 'compiler_sha256': sha256(compiler),
        'script_sha256': sha256(Path(__file__)), 'probes': probes,
        'headers': {str(p.relative_to(headers)): sha256(p)
                    for p in headers.rglob('*') if p.is_file()},
        'scope': 'project UAPI/lifetime probes only; query is not a rendering test',
    })
    print(evidence / 'manifest.json')


if __name__ == '__main__':
    main()
