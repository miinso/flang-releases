#!/usr/bin/env python3
"""Resolve emsdk release/hash from LLVM version using a repository mapping file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


LLVM_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_major(version: str) -> int:
    match = LLVM_VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"Invalid LLVM version '{version}'. Expected X.Y.Z.")
    return int(match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-file", required=True, help="Path to emsdk-llvm-map.json")
    parser.add_argument("--llvm-version", required=True, help="LLVM version X.Y.Z")
    args = parser.parse_args()

    llvm_major = parse_major(args.llvm_version)
    map_path = Path(args.map_file)
    if not map_path.exists():
        raise SystemExit(f"Mapping file not found: {map_path}")

    payload = json.loads(map_path.read_text(encoding="utf-8"))
    policy = payload.get("flang_major_to_prev_llvm_major_latest_emsdk", {})
    releases = payload.get("releases", {})

    emsdk_release = policy.get(str(llvm_major))
    if not emsdk_release:
        raise SystemExit(
            f"No emsdk policy mapping for LLVM major {llvm_major} in {map_path}. "
            "Update emsdk-llvm-map.json."
        )

    release_row = releases.get(emsdk_release)
    if not release_row:
        raise SystemExit(
            f"Mapped emsdk release '{emsdk_release}' is missing from releases table."
        )

    emsdk_hash = release_row.get("emscripten_release_hash")
    if not emsdk_hash:
        raise SystemExit(
            f"Mapped emsdk release '{emsdk_release}' has no emscripten_release_hash."
        )

    expected_major = release_row.get("llvm_major_estimate")
    if expected_major is None:
        raise SystemExit(
            f"Mapped emsdk release '{emsdk_release}' has no llvm_major_estimate."
        )

    sys.stdout.write(f"EMSDK_VERSION={emsdk_release}\n")
    sys.stdout.write(f"EMSDK_HASH={emsdk_hash}\n")
    sys.stdout.write(f"EMSDK_EXPECTED_LLVM_MAJOR={expected_major}\n")


if __name__ == "__main__":
    main()

