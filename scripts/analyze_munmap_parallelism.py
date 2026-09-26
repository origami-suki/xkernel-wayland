#!/usr/bin/env python3
"""Recompute why the M5-002 munmap CPU saving is larger than the first-frame saving.

This is an analysis-only script for already archived runs. It does not build,
boot or modify anything; it re-reads the preserved per-syscall profile CSVs and
the host sampling JSONL and reports:

  * munmap CPU per PID, so the saving can be attributed to the process that
    actually paid it;
  * host CPU seconds, wall time and average busy vCPU count, so the CPU saving
    can be separated from critical-path time;
  * the capacity-normalized CPU saving (saved CPU / vCPU count), which
    is not a bound on the observed first-frame shift.

Inputs are the archived independent before/after sample set of M5-002. Output is
a JSON summary plus a printed table. See
docs/measurements/munmap-sparse-20260926.md for the recorded interpretation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys

# AArch64 syscall numbers used here.
SYSNO_MUNMAP = 215

# The browser process owns the first-frame critical path. In this fixed
# configuration it is consistently PID 29: the M1-013 hotspot scan and both
# M5-002 sample sets agree, and that PID is the one whose cmdline carries the
# original page URL. Pass --browser-pid to override.
DEFAULT_BROWSER_PID = 29


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv_with_optional_profile_header(path: str):
    """The profiler writes a `SYSCALL_PROFILE ...` line before the real header."""
    with open(path, newline="") as fh:
        first = fh.readline()
        if not first.startswith("SYSCALL_PROFILE"):
            fh.seek(0)
        yield from csv.DictReader(fh)


def munmap_cpu_by_pid(analysis_dir: str) -> dict[int, dict[str, float]]:
    path = os.path.join(analysis_dir, "startup-raw.csv")
    per_pid: dict[int, dict[str, float]] = {}
    for row in read_csv_with_optional_profile_header(path):
        try:
            sysno = int(row["sysno"])
            pid = int(row["pid"])
            completed = int(row["completed"])
            cpu_ns = int(row["cpu_ns"])
        except (TypeError, ValueError):
            continue
        if sysno != SYSNO_MUNMAP:
            continue
        entry = per_pid.setdefault(pid, {"completed": 0.0, "cpu_ns": 0.0})
        entry["completed"] += completed
        entry["cpu_ns"] += cpu_ns
    return per_pid


def host_usage(run_dir: str) -> dict[str, float]:
    path = os.path.join(run_dir, "profile-host-samples.jsonl")
    samples = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    if len(samples) < 2:
        raise ValueError(f"not enough host samples in {path}")
    ticks_per_second = samples[0]["ticks_per_second"]
    wall_s = (samples[-1]["host_monotonic_ns"] - samples[0]["host_monotonic_ns"]) / 1e9
    cpu_s = (samples[-1]["cpu_ticks"] - samples[0]["cpu_ticks"]) / ticks_per_second
    return {
        "samples": len(samples),
        "wall_s": wall_s,
        "host_cpu_s": cpu_s,
        "busy_vcpu_avg": cpu_s / wall_s,
    }


def median(values):
    return statistics.median(values)


def spread(values) -> dict[str, float]:
    return {"median": median(values), "min": min(values), "max": max(values)}


def collect(analysis_root: str, runs_root: str, variant: str, rounds: int,
            browser_pid: int) -> dict:
    per_round = []
    for index in range(1, rounds + 1):
        name = f"{variant}-{index}"
        analysis_dir = os.path.join(analysis_root, name)
        run_dir = os.path.join(runs_root, name)
        by_pid = munmap_cpu_by_pid(analysis_dir)
        usage = host_usage(run_dir)

        munmap_cpu_s = sum(v["cpu_ns"] for v in by_pid.values()) / 1e9
        munmap_calls = sum(v["completed"] for v in by_pid.values())
        browser = by_pid.get(browser_pid, {"completed": 0.0, "cpu_ns": 0.0})
        browser_cpu_s = browser["cpu_ns"] / 1e9

        per_round.append({
            "round": name,
            "analysis_dir": analysis_dir,
            "run_dir": run_dir,
            "munmap_cpu_s": munmap_cpu_s,
            "munmap_completed": munmap_calls,
            "browser_pid": browser_pid,
            "browser_munmap_cpu_s": browser_cpu_s,
            "browser_munmap_completed": browser["completed"],
            "other_pids_munmap_cpu_s": munmap_cpu_s - browser_cpu_s,
            "munmap_by_pid_s": {str(pid): v["cpu_ns"] / 1e9 for pid, v in by_pid.items()},
            **usage,
        })

    # Child PIDs shift between rounds, so a per-PID median would mix roles.
    # Aggregate by rank instead: the k-th heaviest munmap consumer in each round.
    rank_values: dict[int, list[float]] = {}
    for row in per_round:
        ordered = sorted(row["munmap_by_pid_s"].values(), reverse=True)
        row["munmap_top_rank_s"] = ordered[:5]
        for rank, cpu_s in enumerate(ordered[:5], start=1):
            rank_values.setdefault(rank, []).append(cpu_s)
    rank_median = {rank: median(vals) for rank, vals in rank_values.items()
                   if len(vals) == len(per_round)}
    top4_share = []
    for row in per_round:
        total = row["munmap_cpu_s"]
        top4_share.append(sum(row["munmap_top_rank_s"][:4]) / total if total else 0.0)

    return {
        "variant": variant,
        "rounds": per_round,
        "munmap_cpu_s": spread([r["munmap_cpu_s"] for r in per_round]),
        "munmap_completed": spread([r["munmap_completed"] for r in per_round]),
        "browser_munmap_cpu_s": spread([r["browser_munmap_cpu_s"] for r in per_round]),
        "other_pids_munmap_cpu_s": spread([r["other_pids_munmap_cpu_s"] for r in per_round]),
        "host_cpu_s": spread([r["host_cpu_s"] for r in per_round]),
        "wall_s": spread([r["wall_s"] for r in per_round]),
        "busy_vcpu_avg": spread([r["busy_vcpu_avg"] for r in per_round]),
        "munmap_top_rank_median_s": {str(rank): cpu for rank, cpu in rank_median.items()},
        "top4_share_of_munmap_cpu": spread(top4_share),
    }


def load_first_frames(metrics_path: str) -> dict[str, tuple[float, float]]:
    with open(metrics_path) as fh:
        metrics = json.load(fh)
    frames = {}
    for row in metrics.get("application", []):
        name = os.path.basename(row["run"])
        lower, upper = row["first_frame_interval_s"]
        frames[name] = (lower, upper)
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analysis-dir",
                        default="artifacts/M5-002-munmap-20260926/independent/analysis")
    parser.add_argument("--runs-dir",
                        default="artifacts/M5-002-munmap-20260926/independent/runs")
    parser.add_argument("--metrics",
                        default="artifacts/M5-002-munmap-20260926/independent/metrics.json")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--vcpus", type=int, default=4,
                        help="guest vCPU count for capacity-normalized CPU seconds")
    parser.add_argument("--browser-pid", type=int, default=DEFAULT_BROWSER_PID)
    parser.add_argument("--output", default=None,
                        help="write the JSON summary here (default: stdout only)")
    args = parser.parse_args()

    frames = load_first_frames(args.metrics)
    variants = {}
    for variant in ("baseline", "candidate"):
        variants[variant] = collect(args.analysis_dir, args.runs_dir, variant,
                                    args.rounds, args.browser_pid)
        for row in variants[variant]["rounds"]:
            lower, upper = frames[row["round"]]
            row["first_frame_lower_s"] = lower
            row["first_frame_upper_s"] = upper
        variants[variant]["first_frame_lower_s"] = spread(
            [r["first_frame_lower_s"] for r in variants[variant]["rounds"]])
        variants[variant]["first_frame_upper_s"] = spread(
            [r["first_frame_upper_s"] for r in variants[variant]["rounds"]])

    # Paired per-round deltas: the runs were executed B1/C1 ... B5/C5.
    paired = []
    for base, cand in zip(variants["baseline"]["rounds"], variants["candidate"]["rounds"]):
        paired.append({
            "round": base["round"].split("-")[-1],
            "munmap_cpu_saved_s": base["munmap_cpu_s"] - cand["munmap_cpu_s"],
            "first_frame_saved_s": base["first_frame_lower_s"] - cand["first_frame_lower_s"],
            "host_cpu_saved_s": base["host_cpu_s"] - cand["host_cpu_s"],
        })

    munmap_saved = variants["baseline"]["munmap_cpu_s"]["median"] - \
        variants["candidate"]["munmap_cpu_s"]["median"]
    frame_saved = variants["baseline"]["first_frame_lower_s"]["median"] - \
        variants["candidate"]["first_frame_lower_s"]["median"]
    capacity_seconds = munmap_saved / args.vcpus

    derived = {
        "vcpus": args.vcpus,
        "munmap_cpu_saved_median_s": munmap_saved,
        "first_frame_saved_median_s": frame_saved,
        "capacity_normalized_cpu_saved_s": capacity_seconds,
        "note": ("Saved CPU seconds divided by configured vCPUs expresses full-capacity "
                 "equivalent time, not a bound or prediction of first-frame improvement. "
                 "Critical-path scheduling and dependencies are not measured here."),
        "paired_deltas": paired,
        "paired_first_frame_deltas": spread([p["first_frame_saved_s"] for p in paired]),
    }

    inputs = {
        "analysis_dir": os.path.abspath(args.analysis_dir),
        "runs_dir": os.path.abspath(args.runs_dir),
        "metrics": os.path.abspath(args.metrics),
        "metrics_sha256": sha256_file(args.metrics),
        "metrics_sha256_scope": "only metrics.json is hashed here; per-run CSVs and "
                                "host samples are addressed by the paths recorded in rounds[]",
    }
    summary = {"tool": os.path.basename(__file__), "inputs": inputs,
               "variants": variants, "derived": derived}

    print(f"guest vCPUs: {args.vcpus}   browser pid: {args.browser_pid}")
    for variant in ("baseline", "candidate"):
        v = variants[variant]
        print(f"\n== {variant}")
        print(f"   munmap CPU      : {v['munmap_cpu_s']['median']:.4f} s "
              f"[{v['munmap_cpu_s']['min']:.4f}, {v['munmap_cpu_s']['max']:.4f}]")
        print(f"   of which browser: {v['browser_munmap_cpu_s']['median']:.4f} s")
        print(f"   other processes : {v['other_pids_munmap_cpu_s']['median']:.4f} s")
        print(f"   host CPU        : {v['host_cpu_s']['median']:.1f} s over "
              f"{v['wall_s']['median']:.1f} s wall")
        print(f"   busy vCPU       : {v['busy_vcpu_avg']['median']:.2f} / {args.vcpus} "
              f"({v['busy_vcpu_avg']['median'] / args.vcpus * 100:.0f}% utilised)")
        print(f"   first frame     : {v['first_frame_lower_s']['median']:.3f}-"
              f"{v['first_frame_upper_s']['median']:.3f} s")
        print("   munmap by rank (median): " +
              ", ".join(f"#{rank}={cpu:.3f}s"
                        for rank, cpu in v["munmap_top_rank_median_s"].items()) +
              f"   top4 share={v['top4_share_of_munmap_cpu']['median'] * 100:.1f}%")

    print("\n== derived")
    print(f"   munmap CPU saved     : {munmap_saved:.2f} s")
    print(f"   host CPU saved       : "
          f"{variants['baseline']['host_cpu_s']['median'] - variants['candidate']['host_cpu_s']['median']:.1f} s")
    print(f"   first frame saved    : {frame_saved:.3f} s (median)")
    print(f"   capacity-normalized CPU saved: {capacity_seconds:.2f} s (not a latency bound)")
    print("   paired first-frame deltas: " +
          ", ".join(f"{p['first_frame_saved_s']:+.1f}" for p in paired) +
          f"  (median {derived['paired_first_frame_deltas']['median']:+.1f})")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as fh:
            json.dump(summary, fh, indent=1, sort_keys=False)
            fh.write("\n")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
