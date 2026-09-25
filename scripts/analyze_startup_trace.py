#!/usr/bin/env python3
"""Preserve Perfetto SQL results for an exported startup trace.

Slice duration is elapsed wall time, which can include blocking/preemption.
Nested or concurrent slices must not be added as exclusive CPU time.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import shlex
import subprocess

from run_guest import ROOT, sha256

QUERIES = {
    'bounds': 'SELECT start_ts, end_ts, (end_ts-start_ts)/1e9 AS span_seconds FROM trace_bounds',
    'processes': 'SELECT upid, pid, name, start_ts, end_ts FROM process ORDER BY pid',
    'threads': '''SELECT t.utid, p.pid, p.name AS process_name, t.tid, t.name AS thread_name,
        count(s.id) AS slices, min(s.ts) AS first_slice_ts,
        max(s.ts+max(s.dur,0)) AS last_slice_end_ts
        FROM thread t LEFT JOIN process p USING(upid)
        LEFT JOIN thread_track tt USING(utid) LEFT JOIN slice s ON s.track_id=tt.id
        GROUP BY t.utid ORDER BY slices DESC''',
    'stats': 'SELECT name, idx, severity, source, value FROM stats WHERE value != 0 ORDER BY severity, name, idx',
    'metadata': 'SELECT name, key_type, int_value, str_value FROM metadata ORDER BY name',
    'longest': '''SELECT p.pid, p.name AS process_name, t.tid, t.name AS thread_name,
        s.id, s.parent_id, s.category, s.name, s.ts, s.dur, s.dur/1e9 AS elapsed_seconds
        FROM slice s LEFT JOIN thread_track tt ON s.track_id=tt.id
        LEFT JOIN thread t USING(utid) LEFT JOIN process p USING(upid)
        WHERE s.dur > 0 ORDER BY s.dur DESC LIMIT 150''',
    'events': '''SELECT category, name, count(*) AS instances,
        round(sum(CASE WHEN dur>0 THEN dur ELSE 0 END)/1e9,6) AS inclusive_sum_seconds,
        round(max(dur)/1e9,6) AS max_elapsed_seconds,
        sum(CASE WHEN dur<0 THEN 1 ELSE 0 END) AS unfinished
        FROM slice GROUP BY category,name ORDER BY inclusive_sum_seconds DESC LIMIT 200''',
    'coverage': '''SELECT p.pid, p.name AS process_name, count(s.id) AS slices,
        min(s.ts) AS first_ts, max(s.ts+max(s.dur,0)) AS last_ts,
        sum(CASE WHEN s.dur<0 THEN 1 ELSE 0 END) AS unfinished_slices
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        GROUP BY p.upid ORDER BY p.pid''',
    'startup_and_navigation': '''SELECT id, parent_id, category, name, ts, dur,
        round(dur/1e9,6) AS elapsed_seconds FROM slice
        WHERE category='startup' OR
          (category='navigation' AND (name GLOB 'Navigation: file:*'
             OR name IN ('LoaderStartToFetchStart','LoaderStartToReceiveResponse')))
        ORDER BY ts,id''',
    'integrity_tasks': '''SELECT p.pid, t.tid, s.id, s.ts, s.dur,
        round(s.dur/1e9,6) AS elapsed_seconds FROM slice s
        JOIN thread_track tt ON s.track_id=tt.id JOIN thread t USING(utid)
        JOIN process p USING(upid)
        WHERE extract_arg(s.arg_set_id,'task.posted_from.function_name')='CheckResourceIntegrity'
        ORDER BY s.ts''',
    'integrity_reads': '''SELECT parent.id AS task_id,
        extract_arg(child.arg_set_id,'source.function_name') AS function,
        count(*) AS calls, round(sum(max(child.dur,0))/1e9,6) AS total_wall_seconds,
        round(max(child.dur)/1e9,6) AS max_wall_seconds
        FROM slice child JOIN slice parent ON child.parent_id=parent.id
        WHERE extract_arg(parent.arg_set_id,'task.posted_from.function_name')='CheckResourceIntegrity'
        GROUP BY parent.id,function ORDER BY parent.id,function''',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--trace-processor', type=Path,
                        default=ROOT / 'work/tools/perfetto/trace_processor')
    parser.add_argument('--output', type=Path,
                        help='new analysis directory; defaults to RUN/perfetto-analysis')
    args = parser.parse_args()
    trace = args.run.resolve() / 'chromium-startup.pftrace'
    output = args.output.resolve() if args.output else args.run.resolve() / 'perfetto-analysis'
    output.mkdir(exist_ok=False)
    processor = args.trace_processor.resolve()
    results = {}
    for name, sql in QUERIES.items():
        query = output / (name + '.sql')
        query.write_text(sql + ';\n')
        argv = [str(processor), 'query', '-f', str(query), str(trace)]
        process = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        (output / (name + '.csv')).write_text(process.stdout)
        (output / (name + '.log')).write_text(shlex.join(argv) + '\n' + process.stderr)
        process.check_returncode()
        results[name] = list(csv.DictReader(io.StringIO(process.stdout)))
    summary = {
        'trace': str(trace), 'trace_sha256': sha256(trace),
        'processor_version': subprocess.check_output([str(processor), '--version'], text=True),
        'sql_queries_passed': len(QUERIES), 'bounds': results['bounds'],
        'process_coverage': results['coverage'],
        'nonzero_error_or_data_loss_stats': [row for row in results['stats']
                                           if row['severity'] in ('error', 'data_loss')],
        'performance_sample_valid': False,
        'limitations': ['slice duration includes waiting and preemption',
                        'inclusive duration sums overlap; they are not exclusive CPU time',
                        'thread_dur is an OS-reported counter; calibrate the exact kernel before using it (see M1-012; historical 31c8f270 charges sleep as CPU)',
                        'successful SQL import alone does not prove complete process or startup coverage',
                        'guest trace and host screenshot clocks have not been aligned'],
    }
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
