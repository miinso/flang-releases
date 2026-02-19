#!/usr/bin/env python3
"""Validate that a tool reports the expected LLVM major version."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


LLVM_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def detect_llvm_major(tool: str) -> int:
    proc = subprocess.run(
        [tool, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"{tool} --version failed (exit {proc.returncode}).\n{text}")

    match = LLVM_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not parse LLVM version from '{tool} --version'.\n{text}")
    return int(match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", default="llvm-nm")
    parser.add_argument("--expected-major", required=True, type=int)
    args = parser.parse_args()

    detected = detect_llvm_major(args.tool)
    print(f"Detected LLVM major from {args.tool}: {detected}")
    print(f"Expected LLVM major: {args.expected_major}")

    if detected != args.expected_major:
        raise SystemExit(
            f"LLVM major mismatch for {args.tool}: detected {detected}, expected {args.expected_major}"
        )


if __name__ == "__main__":
    main()
