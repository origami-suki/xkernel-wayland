#!/usr/bin/env python3
"""Validate stopped syscall snapshots and rank CPU, elapsed time, and call count."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
FIELDS = 'shard,pid,sysno,started,completed,errors,cpu_ns,elapsed_ns,max_cpu_ns,max_elapsed_ns,cpu_over_elapsed'.split(',')
SUMS = ['started', 'completed', 'errors', 'cpu_ns', 'elapsed_ns', 'cpu_over_elapsed']


def parse_snapshot(text):
    lines = text.splitlines()
    if len(lines) < 3 or not re.fullmatch(r'SYSCALL_PROFILE (?:[a-z_]+=\d+ ?)+', lines[0]):
        raise ValueError('missing or malformed profile header')
    header = {k: int(v) for k, v in re.findall(r'([a-z_]+)=(\d+)', lines[0])}
    if header.get('version') != 1 or header.get('stop_ns', 0) < header.get('start_ns', 0):
        raise ValueError('unsupported version or invalid window')
    match = re.fullmatch(r'SYSCALL_PROFILE_END dropped=(\d+)', lines[-1])
    if not match or lines[1].split(',') != FIELDS:
        raise ValueError('truncated snapshot or unexpected columns')
    rows, keys = [], set()
    for raw in csv.DictReader(lines[1:-1]):
        if set(raw) != set(FIELDS) or any(not re.fullmatch(r'\d+', v or '') for v in raw.values()):
            raise ValueError('malformed numeric row')
        row = {k: int(v) for k, v in raw.items()}
        key = (row['shard'], row['pid'], row['sysno'])
        if key in keys or row['errors'] > row['completed'] or row['completed'] > row['started']:
            raise ValueError('duplicate row or inconsistent counts')
        if row['max_cpu_ns'] > row['cpu_ns'] or row['max_elapsed_ns'] > row['elapsed_ns']:
            raise ValueError('inconsistent maxima')
        keys.add(key)
        row['unfinished'] = row['started'] - row['completed']
        rows.append(row)
    header['dropped'] = int(match[1])
    return header, rows


def aggregate(rows, key):
    groups = defaultdict(lambda: {field: 0 for field in [*SUMS, 'unfinished', 'max_cpu_ns', 'max_elapsed_ns']})
    for row in rows:
        group = groups[row[key]]
        for field in [*SUMS, 'unfinished']:
            group[field] += row[field]
        for field in ('max_cpu_ns', 'max_elapsed_ns'):
            group[field] = max(group[field], row[field])
    return [{key: value, **stats} for value, stats in groups.items()]


def analyze(run, output, names_path):
    if output.exists():
        raise ValueError('refusing to overwrite analysis')
    metadata = json.loads((run / 'metadata.json').read_text())
    serial = (run / 'serial.log').read_text(errors='strict').replace('\r', '')
    serial = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', serial)
    names_source = names_path.read_text()
    names = {int(n): name for name, n in re.findall(r'^\s+(\w+) = (\d+),', names_source, re.M)}
    result = {'run': str(run), 'kernel': metadata.get('kernel'),
              'profile_boundary': metadata.get('syscall_profile'), 'stages': {},
              'run_exit': {k: metadata.get(k) for k in ('guest_exit_code', 'qemu_exit_code', 'exit_reason')},
              'serial_sha256': hashlib.sha256((run / 'serial.log').read_bytes()).hexdigest(),
              'names_sha256': hashlib.sha256(names_source.encode()).hexdigest(),
              'performance_sample_valid': False,
              'limits': ['completed-in-window dispatcher intervals only; boundary calls are excluded',
                         'summed parallel elapsed times are not startup wall time or critical-path delay',
                         'CPU follows guest thread kernel accounting including its IRQ attribution',
                         'PID reuse is not disambiguated; cmdline does not prove Chromium process role',
                         'guest and host clocks are not aligned; first-frame stop includes serial delay']}
    pending = {}
    for stage in ('startup', 'steady'):
        blocks = re.findall(r'^PROFILE_DATA_BEGIN_' + stage + r'\n(.*?)^PROFILE_DATA_END_' + stage + r'$', serial, re.M | re.S)
        if len(blocks) != 1:
            raise ValueError('missing or repeated snapshot: ' + stage)
        text = blocks[0]
        digest = hashlib.sha256(text.encode()).hexdigest()
        checksums = re.findall(r'^([0-9a-f]{64})  /tmp/profile-' + stage + r'\.csv$', serial, re.M)
        if checksums != [digest]:
            raise ValueError('snapshot transfer hash mismatch: ' + stage)
        header, rows = parse_snapshot(text)
        by_syscall, by_process = aggregate(rows, 'sysno'), aggregate(rows, 'pid')
        for row in by_syscall:
            row['name'] = names.get(row['sysno'], 'unknown_' + str(row['sysno']))
            row['mean_cpu_ns'] = row['cpu_ns'] / row['completed'] if row['completed'] else None
            row['mean_elapsed_ns'] = row['elapsed_ns'] / row['completed'] if row['completed'] else None
        summary = {**header, 'rows': len(rows), 'sha256': digest,
                   'totals': {field: sum(row[field] for row in rows) for field in [*SUMS, 'unfinished']},
                   'top_cpu': sorted(by_syscall, key=lambda r: r['cpu_ns'], reverse=True)[:20],
                   'top_elapsed': sorted(by_syscall, key=lambda r: r['elapsed_ns'], reverse=True)[:20],
                   'top_count': sorted(by_syscall, key=lambda r: r['completed'], reverse=True)[:20],
                   'process_cpu': sorted(by_process, key=lambda r: r['cpu_ns'], reverse=True)}
        summary['quality_ok'] = header['dropped'] == 0 and summary['totals']['cpu_over_elapsed'] == 0
        result['stages'][stage] = summary
        pending[stage] = (text, by_syscall, by_process)
    output.mkdir(parents=True)
    (output / 'syscall-names-source.rs').write_text(names_source)
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    report = ['# Syscall profile', '', 'Diagnostic completed-call aggregates; see summary.json for coverage and boundary limits.', '']
    for stage, (text, by_syscall, by_process) in pending.items():
        (output / (stage + '-raw.csv')).write_text(text)
        for label, rows in [('syscalls', by_syscall), ('processes', by_process)]:
            with (output / (stage + '-' + label + '.csv')).open('w') as stream:
                if rows:
                    writer = csv.DictWriter(stream, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
        report += [f'## {stage}', '', '| syscall | completed | errors | CPU s | elapsed s | unfinished |', '|---|---:|---:|---:|---:|---:|']
        for row in result['stages'][stage]['top_cpu']:
            report.append(f"| {row['name']} | {row['completed']} | {row['errors']} | {row['cpu_ns']/1e9:.6f} | {row['elapsed_ns']/1e9:.6f} | {row['unfinished']} |")
        report.append('')
    (output / 'report.md').write_text('\n'.join(report))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--syscall-names', type=Path, default=ROOT / 'sources/x-kernel/api/linux_sysno/src/arch/aarch64.rs')
    args = parser.parse_args()
    output = args.output or args.run / 'syscall-analysis'
    result = analyze(args.run.resolve(), output.resolve(), args.syscall_names)
    for stage, data in result['stages'].items():
        print(stage, json.dumps({'totals': data['totals'], 'quality_ok': data['quality_ok'], 'dropped': data['dropped']}))
    print(output / 'report.md')


if __name__ == '__main__':
    main()
