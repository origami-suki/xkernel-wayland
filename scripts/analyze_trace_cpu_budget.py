#!/usr/bin/env python3
"""Build a CPU / waiting budget from an already exported Chromium startup trace.

Purpose (M1-003 P3): split an existing trace into "how much CPU time each
process and thread actually burned, and when" instead of sampling single
instants with GDB.  No guest run, no kernel change, no new trace.

Method and its limits:

* CPU time comes from the per-slice ``thread_dur`` column, i.e. the OS-reported
  thread CPU time Chromium attached to each trace event.  On x-kernel this is
  only meaningful for kernels carrying the validated CPU-accounting fix
  (M1-012 / upstream !828); on the historical ``31c8f270`` the same column
  charges sleep as CPU time.
* Nested slices share the same CPU time, so every budget here aggregates
  ``depth = 0`` slices only.  CPU spent outside every slice, and CPU of
  processes that never reported an event, are invisible: all totals are
  lower bounds, never upper bounds.
* The trace contains no scheduler (ftrace) data, so off-CPU waiting can only be
  read as ``dur - thread_dur`` of a single slice; it must not be attributed to
  a specific lock, I/O or timer.
* A traced run is slower than an untraced one and this is a single diagnostic
  sample: these numbers must not be used as a performance result or as a patch
  benefit comparison.
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
    'availability': '''SELECT (SELECT count(*) FROM sched_slice) AS sched_slices,
        (SELECT count(*) FROM slice) AS all_slices,
        (SELECT count(*) FROM slice WHERE thread_dur IS NOT NULL) AS slices_with_cpu,
        (SELECT count(*) FROM slice WHERE depth=0) AS depth0_slices,
        (SELECT count(*) FROM slice WHERE depth=0 AND thread_dur IS NOT NULL) AS depth0_slices_with_cpu,
        (SELECT round(sum(max(thread_dur,0))/1e9,3) FROM slice WHERE depth=0) AS depth0_cpu_seconds,
        (SELECT round(sum(max(thread_dur,0))/1e9,3) FROM slice) AS nested_sum_cpu_seconds''',
    'process_coverage': '''SELECT p.pid, p.name AS process_name, count(s.id) AS slices,
        min(s.ts) AS first_ts, max(s.ts+max(s.dur,0)) AS last_ts,
        round(min(s.ts)/1e9,3) AS first_seconds, round(max(s.ts+max(s.dur,0))/1e9,3) AS last_seconds,
        sum(CASE WHEN s.dur<0 THEN 1 ELSE 0 END) AS unfinished_slices
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        GROUP BY p.upid ORDER BY p.pid''',
    'process_cpu': '''SELECT p.pid, p.name AS process_name, count(*) AS depth0_slices,
        round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds,
        round(sum(max(s.dur,0))/1e9,3) AS top_elapsed_seconds,
        round(min(s.ts)/1e9,3) AS first_seconds, round(max(s.ts+max(s.dur,0))/1e9,3) AS last_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE s.depth=0 GROUP BY p.upid ORDER BY cpu_seconds DESC''',
    'thread_cpu': '''SELECT p.pid, p.name AS process_name, t.tid, t.name AS thread_name,
        count(*) AS depth0_slices, round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds,
        round(sum(max(s.dur,0))/1e9,3) AS top_elapsed_seconds,
        round(min(s.ts)/1e9,3) AS first_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE s.depth=0 GROUP BY t.utid ORDER BY cpu_seconds DESC LIMIT 60''',
    'thread_coverage': '''SELECT p.pid, p.name AS process_name, t.tid, t.name AS thread_name,
        count(s.id) AS slices, round(min(s.ts)/1e9,3) AS first_seconds,
        round(max(s.ts+max(s.dur,0))/1e9,3) AS last_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        GROUP BY t.utid ORDER BY slices DESC LIMIT 60''',
    'cpu_timeline': '''SELECT CAST(s.ts/10000000000 AS INT)*10 AS bucket_start_seconds,
        p.pid, p.name AS process_name, count(*) AS depth0_slices,
        round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE s.depth=0 GROUP BY bucket_start_seconds, p.upid
        ORDER BY bucket_start_seconds, cpu_seconds DESC''',
    'category_cpu': '''SELECT s.category, count(*) AS depth0_slices,
        round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds,
        round(sum(max(s.dur,0))/1e9,3) AS top_elapsed_seconds
        FROM slice s WHERE s.depth=0
        GROUP BY s.category ORDER BY cpu_seconds DESC''',
    'self_cpu_by_name': '''WITH child AS (
        SELECT parent_id, sum(max(thread_dur,0)) AS child_cpu
        FROM slice WHERE parent_id IS NOT NULL GROUP BY parent_id)
        SELECT s.name, s.category, count(*) AS slices,
        round(sum(max(s.thread_dur,0) - min(max(s.thread_dur,0), coalesce(c.child_cpu,0)))/1e9,3)
            AS self_cpu_seconds,
        round(sum(max(s.thread_dur,0))/1e9,3) AS inclusive_cpu_seconds
        FROM slice s LEFT JOIN child c ON c.parent_id = s.id
        WHERE s.thread_dur IS NOT NULL
        GROUP BY s.name, s.category ORDER BY self_cpu_seconds DESC LIMIT 60''',
    'waiting_top': '''SELECT p.pid, p.name AS process_name, t.tid, t.name AS thread_name,
        s.category, s.name, round(s.ts/1e9,3) AS start_seconds, s.dur,
        round(s.dur/1e9,3) AS elapsed_seconds,
        round(max(s.thread_dur,0)/1e9,3) AS cpu_seconds,
        round((s.dur-max(s.thread_dur,0))/1e9,3) AS offcpu_seconds
        FROM slice s LEFT JOIN thread_track tt ON s.track_id=tt.id
        LEFT JOIN thread t USING(utid) LEFT JOIN process p USING(upid)
        WHERE s.depth=0 AND s.dur > 0 ORDER BY s.dur DESC LIMIT 60''',
    'startup_milestones': '''SELECT id, parent_id, category, name, ts, dur,
        round(dur/1e9,6) AS elapsed_seconds FROM slice
        WHERE category='startup' OR
          (category='navigation' AND (name GLOB 'Navigation: file:*'
             OR name IN ('LoaderStartToFetchStart','LoaderStartToReceiveResponse')))
        ORDER BY ts, id''',
    'integrity_tasks': '''SELECT p.pid, t.tid, s.id, round(s.ts/1e9,3) AS start_seconds,
        round(s.dur/1e9,3) AS elapsed_seconds, round(max(s.thread_dur,0)/1e9,3) AS cpu_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id JOIN thread t USING(utid)
        JOIN process p USING(upid)
        WHERE extract_arg(s.arg_set_id,'task.posted_from.function_name')='CheckResourceIntegrity'
        ORDER BY s.ts''',
    'integrity_reads': '''SELECT parent.id AS task_id, p.pid,
        extract_arg(child.arg_set_id,'source.function_name') AS function,
        count(*) AS calls, round(sum(max(child.dur,0))/1e9,6) AS total_elapsed_seconds,
        round(sum(max(child.thread_dur,0))/1e9,6) AS total_cpu_seconds,
        round(max(child.dur)/1e6,3) AS max_elapsed_ms, round(max(child.thread_dur)/1e6,3) AS max_cpu_ms
        FROM slice child JOIN slice parent ON child.parent_id=parent.id
        JOIN thread_track tt ON parent.track_id=tt.id JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE extract_arg(parent.arg_set_id,'task.posted_from.function_name')='CheckResourceIntegrity'
        GROUP BY parent.id, function ORDER BY parent.id, function''',
    'stats': 'SELECT name, idx, severity, source, value FROM stats WHERE value != 0 ORDER BY severity, name, idx',
}

# Optional stage-scoped queries.  They only run when the caller supplies an
# explicit [start, end) window in trace (guest monotonic) nanoseconds, so that
# two traces can be compared over their own stage boundaries instead of over
# whole-run spans of different length.
WINDOW_QUERIES = {
    'window_process_cpu': '''SELECT p.pid, p.name AS process_name, count(*) AS depth0_slices,
        round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds,
        round(sum(max(s.dur,0))/1e9,3) AS top_elapsed_seconds,
        round(min(s.ts)/1e9,3) AS first_seconds, round(max(s.ts+max(s.dur,0))/1e9,3) AS last_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE s.depth=0 AND s.ts >= {start} AND s.ts < {end}
        GROUP BY p.upid ORDER BY cpu_seconds DESC''',
    'window_thread_cpu': '''SELECT p.pid, p.name AS process_name, t.tid, t.name AS thread_name,
        count(*) AS depth0_slices, round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds,
        round(sum(max(s.dur,0))/1e9,3) AS top_elapsed_seconds,
        round(min(s.ts)/1e9,3) AS first_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE s.depth=0 AND s.ts >= {start} AND s.ts < {end}
        GROUP BY t.utid ORDER BY cpu_seconds DESC LIMIT 60''',
    'window_self_cpu_by_name': '''WITH child AS (
        SELECT parent_id, sum(max(thread_dur,0)) AS child_cpu
        FROM slice WHERE parent_id IS NOT NULL GROUP BY parent_id)
        SELECT s.name, s.category, count(*) AS slices,
        round(sum(max(s.thread_dur,0) - min(max(s.thread_dur,0), coalesce(c.child_cpu,0)))/1e9,3)
            AS self_cpu_seconds
        FROM slice s LEFT JOIN child c ON c.parent_id = s.id
        WHERE s.thread_dur IS NOT NULL AND s.ts >= {start} AND s.ts < {end}
        GROUP BY s.name, s.category ORDER BY self_cpu_seconds DESC LIMIT 40''',
    'window_tracing_overhead': '''SELECT p.name AS process_name, t.name AS thread_name,
        round(sum(max(s.thread_dur,0))/1e9,3) AS cpu_seconds
        FROM slice s JOIN thread_track tt ON s.track_id=tt.id
        JOIN thread t USING(utid) JOIN process p USING(upid)
        WHERE s.ts >= {start} AND s.ts < {end}
          AND (t.name LIKE 'Perfetto%' OR p.name LIKE '%TracingService%')
        GROUP BY t.utid ORDER BY cpu_seconds DESC''',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path,
                        help='run directory containing chromium-startup.pftrace')
    parser.add_argument('--trace', type=Path, help='explicit .pftrace path')
    parser.add_argument('--trace-processor', type=Path,
                        default=ROOT / 'work/tools/perfetto/trace_processor')
    parser.add_argument('--output', type=Path, required=True,
                        help='new analysis directory; must not exist')
    parser.add_argument('--vcpus', type=int, default=4,
                        help='guest vCPU count used for the utilisation reference')
    parser.add_argument('--window-start-ns', type=int,
                        help='stage window start in trace nanoseconds (with --window-end-ns)')
    parser.add_argument('--window-end-ns', type=int,
                        help='stage window end in trace nanoseconds (with --window-start-ns)')
    parser.add_argument('--window-label', default='window',
                        help='label recorded in summary.json for the supplied window')
    parser.add_argument('--only', help='comma-separated subset of query names to run')
    args = parser.parse_args()

    if bool(args.run) == bool(args.trace):
        parser.error('pass exactly one of --run or --trace')
    if (args.window_start_ns is None) != (args.window_end_ns is None):
        parser.error('pass both --window-start-ns and --window-end-ns, or neither')
    trace = (args.trace or (args.run / 'chromium-startup.pftrace')).resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    processor = args.trace_processor.resolve()

    queries = dict(QUERIES)
    window = None
    if args.window_start_ns is not None:
        if args.window_end_ns <= args.window_start_ns:
            parser.error('window end must be after window start')
        window = {'label': args.window_label,
                  'start_ns': args.window_start_ns, 'end_ns': args.window_end_ns,
                  'span_seconds': round((args.window_end_ns - args.window_start_ns) / 1e9, 3)}
        for name, sql in WINDOW_QUERIES.items():
            queries[name] = sql.format(start=args.window_start_ns, end=args.window_end_ns)

    if args.only:
        wanted = [name.strip() for name in args.only.split(',') if name.strip()]
        unknown = [name for name in wanted if name not in queries]
        if unknown:
            parser.error('unknown query name(s): ' + ', '.join(unknown))
        queries = {name: queries[name] for name in wanted}

    results = {}
    for name, sql in queries.items():
        query = output / (name + '.sql')
        query.write_text(sql + ';\n')
        argv = [str(processor), 'query', '-f', str(query), str(trace)]
        process = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        (output / (name + '.csv')).write_text(process.stdout)
        (output / (name + '.log')).write_text(shlex.join(argv) + '\n' + process.stderr)
        process.check_returncode()
        results[name] = list(csv.DictReader(io.StringIO(process.stdout)))

    bounds = results['bounds'][0] if 'bounds' in results else {}
    span = float(bounds['span_seconds']) if bounds else None
    availability = results['availability'][0] if 'availability' in results else {}
    cpu_depth0 = float(availability['depth0_cpu_seconds']) if availability else None
    per_process = {}
    for row in results.get('process_cpu', []):
        per_process[row['process_name']] = float(row['cpu_seconds'])
    window_cpu_capacity = span * args.vcpus if span is not None else None
    bucket_totals = {}
    for row in results.get('cpu_timeline', []):
        bucket = int(row['bucket_start_seconds'])
        bucket_totals[bucket] = bucket_totals.get(bucket, 0.0) + float(row['cpu_seconds'])
    window_summary = None
    if window is not None and 'window_process_cpu' in results:
        per_process_window = {}
        for row in results['window_process_cpu']:
            per_process_window[row['process_name']] = float(row['cpu_seconds'])
        window_cpu = sum(per_process_window.values())
        window_capacity = window['span_seconds'] * args.vcpus
        window_summary = dict(window)
        window_summary.update({
            'accounted_cpu_seconds_depth0': round(window_cpu, 3),
            'guest_cpu_capacity_seconds': round(window_capacity, 3),
            'accounted_cpu_share_of_capacity': round(window_cpu / window_capacity, 3),
            'cpu_seconds_by_process': per_process_window,
            'covered_processes': len(per_process_window),
            'note': 'CPU inside the window is a lower bound: slices that started '
                    'before the window, and processes without trace events, are not counted',
        })
    summary = {
        'trace': str(trace),
        'trace_sha256': sha256(trace),
        'processor_version': subprocess.check_output([str(processor), '--version'], text=True).strip(),
        'sql_queries_passed': len(queries),
        'bounds': bounds,
        'availability': availability,
        'vcpus': args.vcpus,
        'trace_span_seconds': span,
        'guest_cpu_capacity_seconds': round(window_cpu_capacity, 1) if window_cpu_capacity else None,
        'accounted_cpu_seconds_depth0': round(cpu_depth0, 1) if cpu_depth0 is not None else None,
        'accounted_cpu_share_of_capacity': (round(cpu_depth0 / window_cpu_capacity, 3)
                                            if cpu_depth0 is not None and window_cpu_capacity else None),
        'cpu_seconds_by_process': per_process,
        'cpu_seconds_by_10s_bucket': {str(k): round(v, 1) for k, v in sorted(bucket_totals.items())},
        'cpu_seconds_least_loaded_bucket': round(min(bucket_totals.values()), 2) if bucket_totals else None,
        'cpu_seconds_most_loaded_bucket': round(max(bucket_totals.values()), 2) if bucket_totals else None,
        'stage_window': window_summary,
        'performance_sample_valid': False,
        'limitations': [
            'depth-0 slices only: nested slices share CPU time, so totals are lower bounds',
            'CPU of threads that emitted no event, and CPU outside any slice, is invisible',
            'no sched (ftrace) data in the trace: off-CPU wait cannot be attributed to a lock, I/O or timer',
            'thread_dur is only trustworthy on a kernel with the validated CPU-accounting fix (M1-012); the historical 31c8f270 charges sleep as CPU',
            'traced runs are slower than untraced ones; one diagnostic sample, not a performance result',
            'guest trace clock and host screenshot clock are not aligned'],
    }
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
