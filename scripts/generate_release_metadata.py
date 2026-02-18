#!/usr/bin/env python3
"""Generate release metadata and SHA256SUMS for release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_payload_artifacts(directory: Path) -> list[Path]:
    files = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(".tar.gz") or path.name.endswith(".zip"):
            files.append(path)
    return sorted(files, key=lambda p: p.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag-name", required=True)
    parser.add_argument("--build-run-id", required=True)
    parser.add_argument("--release-run-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--rebuild", required=True)
    parser.add_argument("--metadata-path", required=True)
    parser.add_argument("--sha256-path", required=True)
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir)
    metadata_path = Path(args.metadata_path)
    sha256_path = Path(args.sha256_path)

    payload_artifacts = list_payload_artifacts(artifacts_dir)
    if not payload_artifacts:
        raise SystemExit("No release payload artifacts found (.tar.gz/.zip).")

    metadata_artifacts = []
    for path in payload_artifacts:
        metadata_artifacts.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256sum(path),
            }
        )

    metadata = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": args.version,
        "tag_name": args.tag_name,
        "build_run_id": args.build_run_id,
        "release_run_id": args.release_run_id,
        "commit_sha": args.commit_sha,
        "rebuild": args.rebuild.lower() == "true",
        "artifacts": metadata_artifacts,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    checksum_targets = list(payload_artifacts)
    checksum_targets.append(metadata_path)
    lines = []
    for path in sorted(checksum_targets, key=lambda p: p.name):
        lines.append(f"{sha256sum(path)}  {path.name}")
    sha256_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
