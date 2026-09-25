#!/usr/bin/env python3
"""Build small AArch64 ELF layout fixtures and inject into a new reflink disk."""
import argparse
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile

from run_guest import ROOT, check_disk, sha256, write_json


def build_elf(elf_type, entry, segments, interp):
    """Write a minimal little-endian AArch64 ELF64 with hand-built PT_LOAD entries.

    `segments` holds `(file_offset, virtual_address, mem_size, align, flags)`
    tuples. A compiler will not emit the exact page layout the placement
    regression needs, so the file is assembled here field by field. The first
    segment must cover the file and program headers, because `ELFParser::phdr`
    looks the program header table up inside a segment.
    """
    ehsize, phentsize = 64, 56
    phnum = len(segments) + (1 if interp else 0)
    phoff = ehsize
    cursor = ehsize + phnum * phentsize
    interp_offset = None
    if interp:
        interp_offset = cursor
        cursor += len(interp) + 1

    # ELF64 program header: p_type, p_flags, p_offset, p_vaddr, p_paddr,
    # p_filesz, p_memsz, p_align.
    phdrs = []
    if interp:
        phdrs.append(struct.pack('<IIQQQQQQ', 3, 4, interp_offset, interp_offset,
                                 interp_offset, len(interp) + 1, len(interp) + 1, 1))
    for offset, vaddr, mem_size, align, flags in segments:
        assert vaddr % 0x1000 == offset % 0x1000, 'p_vaddr and p_offset page offsets must match'
        file_size = max(1, min(mem_size, 0x1000))
        phdrs.append(struct.pack('<IIQQQQQQ', 1, flags, offset, vaddr, vaddr,
                                 file_size, mem_size, align))

    # e_ident is 16 bytes: magic, class=64, data=LSB, version=1, osabi, abiversion
    # and seven padding bytes.
    ident = b'\x7fELF' + bytes([2, 1, 1, 0, 0]) + b'\x00' * 7
    header = ident + struct.pack(
        '<HHIQQQIHHHHHH',
        elf_type, 183, 1, entry, phoff, 0, 0,
        ehsize, phentsize, phnum, 64, 0, 0,
    )
    assert len(header) == ehsize, len(header)

    end = max(cursor, max(offset + max(1, min(mem, 0x1000))
                          for offset, _, mem, _, _ in segments))
    image = bytearray(end)
    # Segment payload first: the first segment starts at file offset 0 and would
    # otherwise overwrite the ELF header and the program header table.
    for offset, _, mem_size, _, _ in segments:
        file_size = max(1, min(mem_size, 0x1000))
        image[offset:offset + file_size] = b'\x00' * file_size
    if interp:
        image[interp_offset:interp_offset + len(interp)] = interp.encode()
    for index, phdr in enumerate(phdrs):
        start = phoff + index * phentsize
        image[start:start + phentsize] = phdr
    image[0:len(header)] = header
    return bytes(image)


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
    # A synthetic interpreter whose first mapped page sits above its load bias,
    # plus a main image that leaves one free page and then occupies the next.
    # A reservation that ignores the bias-to-first-page offset verifies the free
    # page, then maps the interpreter into the occupied one.
    interp_path = '/lib/ld-xk-placement.so.1'
    interp = build_elf(
        elf_type=3,
        entry=0x1000,
        segments=[(0x0, 0x1000, 0x1000, 0x1000, 0x5)],
        interp=None,
    )
    (fixtures / 'ld-xk-placement.so.1').write_bytes(interp)

    # The first LOAD must cover the file and program headers: the parser looks
    # the program header table up inside a segment and panics when none covers it.
    main = build_elf(
        elf_type=3,
        entry=0x1000,
        segments=[
            (0x0, 0x0, 0x1000, 0x1000, 0x5),
            (0x1000, 0x4001000, 0x1000, 0x1000, 0x5),
        ],
        interp=interp_path,
    )
    (fixtures / 'placement-main').write_bytes(main)
    probe = build_elf(
        elf_type=2,
        entry=0x1000,
        segments=[(0x0, 0x0, 0x1000, 0x1000, 0x5)],
        interp=interp_path,
    )
    (fixtures / 'placement-probe').write_bytes(probe)

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
    # The synthetic probe is expected to be refused: its interpreter cannot be
    # loaded without overlapping the main image, so a pass here means the
    # kernel returned an errno. A kernel panic produces no output at all.
    placement = ('#!/bin/sh\ncd /opt/ict-tests/elf-layout || exit 99\n'
                 'printf \'PLACEMENT_BEGIN\\n\'\n'
                 './exec-check reject ./placement-probe\n'
                 'printf \'PLACEMENT_RESULT=%s\\n\' "$?"\n'
                 'printf \'PLACEMENT_OBSERVATIONS_COMPLETE\\n\'\n')
    (fixtures / 'run-placement.sh').write_text(placement)
    (fixtures / 'run-placement.sh').chmod(0o755)
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
            libdir = Path(directory) / 'lib'
            run(['sudo', '-n', 'install', '-m', '0755',
                 fixtures / 'ld-xk-placement.so.1', libdir / 'ld-xk-placement.so.1'])
            if sha256(fixtures / 'ld-xk-placement.so.1') != sha256(libdir / 'ld-xk-placement.so.1'):
                raise ValueError('guest interpreter hash mismatch')
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
