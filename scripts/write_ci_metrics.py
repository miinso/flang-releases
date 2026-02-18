#!/usr/bin/env python3
"""Write CI metrics JSON from timing and sccache inputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_timing_lines(path: Path) -> dict[str, int]:
    timings: dict[str, int] = {}
    if not path.exists():
        return timings

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        try:
            timings[key] = int(value)
        except ValueError:
            continue
    return timings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform-id", required=True)
    parser.add_argument("--target-triple", required=True)
    parser.add_argument("--llvm-version", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--timings-file", required=True)
    parser.add_argument("--sccache-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    timings_file = Path(args.timings_file)
    sccache_file = Path(args.sccache_file)
    output_file = Path(args.output)

    durations = parse_timing_lines(timings_file)
    total_seconds = sum(durations.values())

    if sccache_file.exists():
        sccache_raw = sccache_file.read_text(encoding="utf-8")
    else:
        sccache_raw = "sccache stats unavailable"

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform_id": args.platform_id,
        "target_triple": args.target_triple,
        "llvm_version": args.llvm_version,
        "workflow": {
            "repository": args.repo,
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
            "commit_sha": args.sha,
        },
        "step_durations_seconds": durations,
        "timed_total_seconds": total_seconds,
        "sccache_stats_raw": sccache_raw,
    }

    output_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
