#!/usr/bin/env python3
"""Check upstream LLVM tags and optionally update versions-matrix.json + versions.env."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json


TAG_RE = re.compile(r"^llvmorg-(\d+)\.(\d+)\.(\d+)$")


def parse_versions_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_matrix(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("versions", [])


def save_matrix(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps({"versions": entries}, indent=2) + "\n", encoding="utf-8"
    )


def list_llvm_tags(max_pages: int = 5, per_page: int = 100) -> list[str]:
    tags: list[str] = []
    for page in range(1, max_pages + 1):
        params = urlencode({"per_page": per_page, "page": page})
        url = f"https://api.github.com/repos/llvm/llvm-project/tags?{params}"
        req = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "flang-releases-version-watch",
            },
        )
        with urlopen(req, timeout=30) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not batch:
            break
        tags.extend(item.get("name", "") for item in batch)
    return tags


def find_latest_patch(tags: list[str], tracked_minor: str) -> str:
    parts = tracked_minor.split(".", 1)
    if len(parts) != 2:
        raise ValueError(f"TRACKED_LLVM_MINOR must be '<major>.<minor>', got: {tracked_minor}")
    major = int(parts[0])
    minor = int(parts[1])

    best: tuple[int, int, int] | None = None
    for tag in tags:
        match = TAG_RE.match(tag)
        if not match:
            continue
        m_major, m_minor, m_patch = map(int, match.groups())
        if m_major != major or m_minor != minor:
            continue
        candidate = (m_major, m_minor, m_patch)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        raise RuntimeError(f"No llvm tags found for TRACKED_LLVM_MINOR={tracked_minor}")
    return f"{best[0]}.{best[1]}.{best[2]}"


def write_updated_versions_env(
    path: Path, latest_version: str, fork_ref_template: str, current_fork_ref: str
) -> str:
    new_fork_ref = fork_ref_template.format(version=latest_version)
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced_llvm = False
    replaced_ref = False

    out_lines: list[str] = []
    for line in lines:
        if line.startswith("LLVM_VERSION="):
            out_lines.append(f"LLVM_VERSION={latest_version}")
            replaced_llvm = True
            continue
        if line.startswith("LLVM_FORK_REF="):
            out_lines.append(f"LLVM_FORK_REF={new_fork_ref}")
            replaced_ref = True
            continue
        out_lines.append(line)

    if not replaced_llvm:
        out_lines.append(f"LLVM_VERSION={latest_version}")
    if not replaced_ref:
        out_lines.append(f"LLVM_FORK_REF={new_fork_ref}")

    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return new_fork_ref if current_fork_ref != new_fork_ref else current_fork_ref


def emit_output(output_path: str | None, values: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in values.items()]
    text = "\n".join(lines) + "\n"
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions-file", default="versions.env")
    parser.add_argument("--matrix-file", default="versions-matrix.json")
    parser.add_argument(
        "--fork-ref-template",
        default="llvmorg-{version}",
        help="Template for LLVM_FORK_REF when updating.",
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    # multi-version: check all tracked minors from matrix file
    parser.add_argument(
        "--multi",
        action="store_true",
        help="Check all tracked minors from versions-matrix.json.",
    )
    args = parser.parse_args()

    tags = list_llvm_tags()

    if args.multi:
        # multi-version mode: iterate all entries in versions-matrix.json
        matrix_path = Path(args.matrix_file)
        if not matrix_path.exists():
            raise SystemExit(f"Matrix file not found: {matrix_path}")

        entries = load_matrix(matrix_path)
        updates: list[dict] = []

        for entry in entries:
            tracked_minor = entry["tracked_minor"]
            current_version = entry["llvm_version"]
            latest_version = find_latest_patch(tags, tracked_minor)
            needs = latest_version != current_version

            updates.append({
                "tracked_minor": tracked_minor,
                "current_version": current_version,
                "latest_version": latest_version,
                "needs_update": needs,
                "patches_dir": entry["patches_dir"],
            })

            if args.write and needs:
                entry["llvm_version"] = latest_version

        if args.write and any(u["needs_update"] for u in updates):
            save_matrix(matrix_path, entries)

        # output as json for workflow consumption
        any_update = any(u["needs_update"] for u in updates)
        output_data = {
            "any_update": "true" if any_update else "false",
            "updates_json": json.dumps(updates),
        }

        # also output per-entry details for simpler consumption
        updated_entries = [u for u in updates if u["needs_update"]]
        if updated_entries:
            # use first update for backwards-compat single-version outputs
            first = updated_entries[0]
            output_data["latest_version"] = first["latest_version"]
            output_data["tracked_minor"] = first["tracked_minor"]

        emit_output(args.github_output, output_data)
        return

    # single-version mode (backwards compat): use versions.env
    versions_path = Path(args.versions_file)
    values = parse_versions_env(versions_path)
    current_version = values.get("LLVM_VERSION", "").strip()
    tracked_minor = values.get("TRACKED_LLVM_MINOR", "").strip()
    current_fork_ref = values.get("LLVM_FORK_REF", "").strip()

    if not current_version or not tracked_minor:
        raise SystemExit("versions.env must define LLVM_VERSION and TRACKED_LLVM_MINOR")

    latest_version = find_latest_patch(tags, tracked_minor)
    needs_update = latest_version != current_version
    fork_ref = current_fork_ref

    if args.write and needs_update:
        fork_ref = write_updated_versions_env(
            versions_path, latest_version, args.fork_ref_template, current_fork_ref
        )

    emit_output(
        args.github_output,
        {
            "current_version": current_version,
            "latest_version": latest_version,
            "needs_update": "true" if needs_update else "false",
            "tracked_minor": tracked_minor,
            "next_fork_ref": args.fork_ref_template.format(version=latest_version),
            "resolved_fork_ref": fork_ref or current_fork_ref,
        },
    )


if __name__ == "__main__":
    main()
