#!/usr/bin/env python3
"""Generate emsdk<->LLVM mapping data from upstream canonical sources.

Sources:
- emsdk/emscripten-releases-tags.json (release -> hash)
- emsdk/bazel/revisions.bzl (release -> per-platform checksums)
- emscripten/ChangeLog.md (release notes with LLVM update anchors)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen


EMSDK_TAGS_URL = (
    "https://raw.githubusercontent.com/emscripten-core/emsdk/main/"
    "emscripten-releases-tags.json"
)
EMSDK_REVISIONS_URL = (
    "https://raw.githubusercontent.com/emscripten-core/emsdk/main/"
    "bazel/revisions.bzl"
)
EMSCRIPTEN_CHANGELOG_URL = (
    "https://raw.githubusercontent.com/emscripten-core/emscripten/main/ChangeLog.md"
)

SEMVER_ONLY_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
SECTION_HEADER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:\s*\(.*\))?(?:\s*-\s*\d{2}/\d{2}/\d{2})?\s*$"
)
LLVM_VERSION_RE = re.compile(r"LLVM\s+(\d+)\.(\d+)\.(\d+)")
REVISION_START_RE = re.compile(r'^\s*"(\d+\.\d+\.\d+)": struct\(\s*$')
REVISION_FIELD_RE = re.compile(
    r'^\s*(hash|sha_linux|sha_linux_arm64|sha_mac|sha_mac_arm64|sha_win)\s*=\s*"([0-9a-f]+)",\s*$'
)


@dataclass(frozen=True, order=True)
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> "SemVer":
        m = SEMVER_ONLY_RE.match(text)
        if not m:
            raise ValueError(f"Not a semantic version: {text}")
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def short_branch(self) -> str:
        return f"{self.major}.{self.minor}"

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def fetch_text(url: str) -> str:
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw",
            "User-Agent": "flang-releases-emsdk-map-generator",
        },
    )
    with urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def parse_emsdk_release_hashes(tags_json_text: str) -> Dict[str, str]:
    payload = json.loads(tags_json_text)
    releases = payload.get("releases", {})
    out: Dict[str, str] = {}
    for key, value in releases.items():
        if SEMVER_ONLY_RE.match(key):
            out[key] = value
    return out


def parse_revisions_bzl(revisions_text: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    current_release: Optional[str] = None
    current_fields: Dict[str, str] = {}

    for raw in revisions_text.splitlines():
        if current_release is None:
            m = REVISION_START_RE.match(raw)
            if m:
                current_release = m.group(1)
                current_fields = {}
            continue

        field_match = REVISION_FIELD_RE.match(raw)
        if field_match:
            current_fields[field_match.group(1)] = field_match.group(2)
            continue

        if raw.strip() == "),":
            out[current_release] = current_fields
            current_release = None
            current_fields = {}

    return out


def is_section_underline(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and all(ch == "-" for ch in stripped)


def parse_changelog_sections(changelog_text: str) -> Dict[str, List[str]]:
    lines = changelog_text.splitlines()
    headers: List[Tuple[int, str]] = []
    i = 0
    while i < len(lines) - 1:
        header_match = SECTION_HEADER_RE.match(lines[i].strip())
        if header_match and is_section_underline(lines[i + 1]):
            semver = (
                f"{header_match.group(1)}."
                f"{header_match.group(2)}."
                f"{header_match.group(3)}"
            )
            headers.append((i, semver))
            i += 2
            continue
        i += 1

    sections: Dict[str, List[str]] = {}
    for idx, (line_no, semver) in enumerate(headers):
        body_start = line_no + 2
        body_end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        sections[semver] = lines[body_start:body_end]
    return sections


def highest_llvm_version_in_section(section_lines: List[str]) -> Optional[SemVer]:
    found: List[SemVer] = []
    for line in section_lines:
        for m in LLVM_VERSION_RE.finditer(line):
            found.append(SemVer(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    if not found:
        return None
    return max(found)


def infer_branch_versions(
    section_explicit: Dict[str, Optional[SemVer]]
) -> Dict[str, Tuple[Optional[SemVer], Optional[str], str]]:
    """Infer LLVM version per emsdk release within each major.minor branch.

    Returns:
      release -> (llvm_version, anchor_release, inference_mode)
    """
    by_branch: Dict[str, List[SemVer]] = {}
    for release in section_explicit:
        sv = SemVer.parse(release)
        by_branch.setdefault(sv.short_branch(), []).append(sv)

    out: Dict[str, Tuple[Optional[SemVer], Optional[str], str]] = {}

    for branch, versions in by_branch.items():
        versions_sorted = sorted(versions, key=lambda x: x.patch)  # ascending patch
        assigned: Dict[str, Tuple[Optional[SemVer], Optional[str], str]] = {}

        current_version: Optional[SemVer] = None
        current_anchor: Optional[str] = None

        first_known_index: Optional[int] = None
        first_known_version: Optional[SemVer] = None
        first_known_anchor: Optional[str] = None

        for idx, sv in enumerate(versions_sorted):
            key = str(sv)
            explicit = section_explicit.get(key)
            if explicit is not None:
                current_version = explicit
                current_anchor = key
                if first_known_index is None:
                    first_known_index = idx
                    first_known_version = explicit
                    first_known_anchor = key
                assigned[key] = (explicit, key, "explicit")
                continue

            if current_version is not None:
                assigned[key] = (current_version, current_anchor, "forward_fill")
            else:
                assigned[key] = (None, None, "unknown")

        if first_known_index is not None and first_known_version is not None:
            for idx in range(first_known_index):
                key = str(versions_sorted[idx])
                assigned[key] = (
                    first_known_version,
                    first_known_anchor,
                    "backward_fill",
                )

        for sv in versions_sorted:
            key = str(sv)
            out[key] = assigned[key]

    return out


def latest_release_per_llvm_major(
    release_to_llvm: Dict[str, Optional[SemVer]],
    min_llvm_major: int,
) -> Dict[str, str]:
    grouped: Dict[int, List[SemVer]] = {}
    for release, llvm_ver in release_to_llvm.items():
        if llvm_ver is None:
            continue
        if llvm_ver.major < min_llvm_major:
            continue
        grouped.setdefault(llvm_ver.major, []).append(SemVer.parse(release))
    out: Dict[str, str] = {}
    for major, emsdk_versions in grouped.items():
        out[str(major)] = str(max(emsdk_versions))
    return out


def flang_prev_major_policy_map(
    llvm_major_to_latest_emsdk: Dict[str, str],
    start_flang_major: int,
    end_flang_major: int,
) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for flang_major in range(start_flang_major, end_flang_major + 1):
        prev_llvm_major = str(flang_major - 1)
        out[str(flang_major)] = llvm_major_to_latest_emsdk.get(prev_llvm_major)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="emsdk-llvm-map.json",
        help="Path to write generated JSON mapping data.",
    )
    parser.add_argument(
        "--flang-major-range",
        default="19-23",
        help="Range for derived previous-major policy map (e.g. 19-23).",
    )
    parser.add_argument(
        "--min-emsdk-version",
        default="3.1.0",
        help="Minimum emsdk semantic version to include in output.",
    )
    parser.add_argument(
        "--min-llvm-major",
        type=int,
        default=16,
        help="Minimum LLVM major to keep in llvm_major_latest_emsdk summary.",
    )
    args = parser.parse_args()

    start_major_s, end_major_s = args.flang_major_range.split("-", 1)
    start_major = int(start_major_s)
    end_major = int(end_major_s)
    if start_major > end_major:
        raise SystemExit("--flang-major-range must be ascending, e.g. 19-23")
    min_emsdk_version = SemVer.parse(args.min_emsdk_version)

    tags_text = fetch_text(EMSDK_TAGS_URL)
    revisions_text = fetch_text(EMSDK_REVISIONS_URL)
    changelog_text = fetch_text(EMSCRIPTEN_CHANGELOG_URL)

    release_hashes = parse_emsdk_release_hashes(tags_text)
    revision_rows = parse_revisions_bzl(revisions_text)
    changelog_sections = parse_changelog_sections(changelog_text)

    section_explicit: Dict[str, Optional[SemVer]] = {}
    for release, section_lines in changelog_sections.items():
        section_explicit[release] = highest_llvm_version_in_section(section_lines)

    inferred = infer_branch_versions(section_explicit)

    releases: Dict[str, dict] = {}
    release_to_llvm_version: Dict[str, Optional[SemVer]] = {}

    for release in sorted(release_hashes.keys(), key=lambda r: SemVer.parse(r)):
        if SemVer.parse(release) < min_emsdk_version:
            continue
        llvm_ver, anchor, mode = inferred.get(release, (None, None, "unknown"))
        release_to_llvm_version[release] = llvm_ver

        revision = revision_rows.get(release, {})
        binary_checksums = {}
        for key in [
            "sha_linux",
            "sha_linux_arm64",
            "sha_mac",
            "sha_mac_arm64",
            "sha_win",
        ]:
            if key in revision:
                binary_checksums[key] = revision[key]

        releases[release] = {
            "emscripten_release_hash": release_hashes[release],
            "llvm_version_estimate": str(llvm_ver) if llvm_ver is not None else None,
            "llvm_major_estimate": llvm_ver.major if llvm_ver is not None else None,
            "llvm_inference": {
                "mode": mode,
                "anchor_release": anchor,
            },
            "binary_checksums": binary_checksums,
        }

    llvm_major_map = latest_release_per_llvm_major(
        release_to_llvm_version, args.min_llvm_major
    )
    policy_map = flang_prev_major_policy_map(llvm_major_map, start_major, end_major)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "emsdk_releases_tags_json": EMSDK_TAGS_URL,
            "emsdk_bazel_revisions_bzl": EMSDK_REVISIONS_URL,
            "emscripten_changelog_md": EMSCRIPTEN_CHANGELOG_URL,
        },
        "notes": [
            "llvm_version_estimate is inferred from emscripten ChangeLog release notes.",
            "Some releases do not explicitly mention LLVM updates; those are branch-filled.",
            "Use this map as an operational guide, not as a formal ABI guarantee.",
        ],
        "llvm_major_latest_emsdk": llvm_major_map,
        "flang_major_to_prev_llvm_major_latest_emsdk": policy_map,
        "releases": releases,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Wrote {args.output}")
    print("llvm_major_latest_emsdk:", json.dumps(llvm_major_map, sort_keys=True))
    print(
        "flang_major_to_prev_llvm_major_latest_emsdk:",
        json.dumps(policy_map, sort_keys=True),
    )


if __name__ == "__main__":
    main()
