#!/usr/bin/env python3
"""Extract one size/hash-verified Chromium protobuf trace from a serial log."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import zlib

LIMIT = 64 * 1024 * 1024
HEADER = re.compile(rb'__ICT_TRACE_BEGIN__ ([0-9]+) ([0-9a-f]{64}) ([0-9]+) ([0-9a-f]{64})')
PREFIX = b'__ICT_TRACE_DATA__ '


def extract(serial, output):
    if output.exists():
        raise ValueError('refusing to overwrite trace')
    header = None
    finished = False
    compressed = bytearray()
    with serial.open('rb') as stream:
        for raw in stream:
            line = raw.rstrip(b'\r\n')
            match = HEADER.fullmatch(line)
            if match:
                if header is not None:
                    raise ValueError('multiple trace payloads')
                header = (int(match[1]), match[2].decode(), int(match[3]), match[4].decode())
                if not (0 < header[0] <= LIMIT and 0 < header[2] <= LIMIT + 1024 * 1024):
                    raise ValueError('trace exceeds transfer limit')
            elif line.startswith(PREFIX):
                if header is None or finished:
                    raise ValueError('trace data outside payload')
                encoded = line[len(PREFIX):]
                if len(encoded) > 128:
                    raise ValueError('oversized transfer line')
                compressed.extend(base64.b64decode(encoded, validate=True))
                if len(compressed) > header[2]:
                    raise ValueError('compressed payload exceeds declared size')
            elif line == b'__ICT_TRACE_END__':
                if header is None or finished:
                    raise ValueError('unexpected transfer end')
                finished = True
    if header is None or not finished:
        raise ValueError('missing or incomplete trace transfer')
    size, digest, zsize, zdigest = header
    if len(compressed) != zsize or hashlib.sha256(compressed).hexdigest() != zdigest:
        raise ValueError('compressed size or SHA-256 mismatch')
    decoder = zlib.decompressobj(wbits=31)
    data = decoder.decompress(compressed, size + 1)
    if (len(data) != size or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or hashlib.sha256(data).hexdigest() != digest):
        raise ValueError('trace size, gzip integrity or SHA-256 mismatch')
    with output.open('xb') as stream:
        stream.write(data)
    return {'file': str(output), 'bytes': size, 'sha256': digest,
            'compressed_bytes': zsize, 'compressed_sha256': zdigest,
            'transfer_verified': True, 'performance_sample_valid': False,
            'limitation': 'byte integrity only; parseability, data loss and process coverage need Perfetto validation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    result_path = args.run / 'trace-export.json'
    if result_path.exists():
        raise ValueError('refusing to overwrite an earlier export result')
    try:
        result = extract(args.run / 'serial.log', args.run / 'chromium-startup.pftrace')
    except (ValueError, OSError, zlib.error) as error:
        if not result_path.exists():
            result_path.write_text(json.dumps({'transfer_verified': False, 'error': str(error)}, indent=2) + '\n')
        raise
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
