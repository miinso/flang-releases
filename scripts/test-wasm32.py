#!/usr/bin/env python3
"""
Test wasm32 cross-compilation support for Flang.

This script:
1. Builds the wasm32 runtime library using Makefile.wasm32
2. Tests IR output (verifies i32 for wasm32, i64 for native)
3. Runs end-to-end test (compile -> link -> run with Node.js)
4. Installs runtime to sibling directory of native flang_rt
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, **kwargs):
    """Run a command and return output."""
    print(f"+ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result


def find_native_flang_rt_dir(install_dir: Path) -> Path:
    """Find the directory containing the native flang_rt library."""
    # Look for libflang_rt.runtime.a or flang_rt.runtime.lib (Windows)
    for pattern in ["**/libflang_rt.runtime.a", "**/flang_rt.runtime.static.lib"]:
        matches = list(install_dir.glob(pattern))
        if matches:
            return matches[0].parent

    # Fallback: look for any flang_rt file
    for pattern in ["**/libflang_rt*", "**/flang_rt*"]:
        matches = list(install_dir.glob(pattern))
        if matches:
            return matches[0].parent

    raise FileNotFoundError(f"Could not find native flang_rt in {install_dir}")


def find_script_dir() -> Path:
    """Find the directory containing this script."""
    return Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Test wasm32 cross-compilation")
    parser.add_argument("--install-dir", default="./install", help="Flang install directory")
    parser.add_argument("--build-dir", default="./build", help="Build directory for wasm32 runtime")
    parser.add_argument("--source-dir", default=".", help="Source root directory (contains llvm-project/)")
    parser.add_argument("--skip-build", action="store_true", help="Skip building wasm32 runtime")
    parser.add_argument("--skip-test", action="store_true", help="Skip running tests")
    parser.add_argument("--skip-install", action="store_true", help="Skip installing wasm32 runtime")
    args = parser.parse_args()

    install_dir = Path(args.install_dir).resolve()
    build_dir = Path(args.build_dir).resolve()
    source_dir = Path(args.source_dir).resolve()
    script_dir = find_script_dir()
    makefile = script_dir.parent / "Makefile.wasm32"
    flang = install_dir / "bin" / "flang-new"
    wasm32_runtime = build_dir / "libflang_rt.runtime.wasm32.a"

    if not flang.exists():
        # Try .exe for Windows
        flang = install_dir / "bin" / "flang-new.exe"
        if not flang.exists():
            print(f"Error: flang-new not found in {install_dir / 'bin'}")
            sys.exit(1)

    print("=" * 60)
    print("wasm32 Cross-Compilation Test")
    print("=" * 60)
    print(f"Install dir: {install_dir}")
    print(f"Build dir: {build_dir}")
    print(f"Source dir: {source_dir}")
    print(f"Makefile: {makefile}")
    print(f"Flang: {flang}")

    # Verify llvm-project exists
    llvm_project = source_dir / "llvm-project"
    if not llvm_project.exists():
        print(f"Error: llvm-project not found at {llvm_project}")
        sys.exit(1)

    # Step 1: Build wasm32 runtime
    if not args.skip_build:
        print("\n" + "=" * 60)
        print("Step 1: Building wasm32 runtime library")
        print("=" * 60)

        # Detect parallelism
        try:
            import multiprocessing
            jobs = multiprocessing.cpu_count()
        except:
            jobs = 4

        # Pass ROOT to make so it finds llvm-project and uses correct build dir
        result = run(f'make -f "{makefile}" ROOT="{source_dir}" BUILD="{build_dir}" -j{jobs}')
        if result.returncode != 0:
            print("FAIL: Failed to build wasm32 runtime")
            sys.exit(1)

        if not wasm32_runtime.exists():
            print(f"FAIL: Expected {wasm32_runtime} not found")
            sys.exit(1)

        print(f"PASS: Built {wasm32_runtime}")

    # Step 2: Test IR output
    if not args.skip_test:
        print("\n" + "=" * 60)
        print("Step 2: Testing IR output (type sizes)")
        print("=" * 60)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create test program
            hello_f90 = tmpdir / "hello.f90"
            hello_f90.write_text('''\
program hello
  print *, "Hello from wasm32!"
end program hello
''')

            # Test wasm32 target uses i32
            wasm_ll = tmpdir / "hello_wasm.ll"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -S -emit-llvm "{hello_f90}" -o "{wasm_ll}"')
            if result.returncode != 0:
                print("FAIL: Failed to compile for wasm32")
                sys.exit(1)

            wasm_ir = wasm_ll.read_text()
            print("wasm32 IR snippet:")
            for line in wasm_ir.splitlines():
                if "_FortranAioOutputAscii" in line:
                    print(f"  {line}")

            if "_FortranAioOutputAscii(ptr, ptr, i32)" in wasm_ir:
                print("PASS: wasm32 uses i32 for length parameter")
            else:
                print("FAIL: wasm32 should use i32, not i64")
                sys.exit(1)

            # Test native target uses i64
            native_ll = tmpdir / "hello_native.ll"
            result = run(f'"{flang}" -S -emit-llvm "{hello_f90}" -o "{native_ll}"')
            if result.returncode != 0:
                print("FAIL: Failed to compile for native")
                sys.exit(1)

            native_ir = native_ll.read_text()
            print("\nnative IR snippet:")
            for line in native_ir.splitlines():
                if "_FortranAioOutputAscii" in line:
                    print(f"  {line}")

            if "_FortranAioOutputAscii(ptr, ptr, i64)" in native_ir:
                print("PASS: native uses i64 for length parameter")
            else:
                print("FAIL: native should use i64")
                sys.exit(1)

            # Test wasm32 fir.allocmem lowers to malloc(i32) not malloc(i64)
            alloc_f90 = tmpdir / "alloc_test.f90"
            alloc_f90.write_text('''\
function make(n) result(arr)
  integer, intent(in) :: n
  real :: arr(n)
  arr = 1.0
end function

subroutine caller(n)
  integer, intent(in) :: n
  real :: res(n)
  interface
    function make(n) result(arr)
      integer, intent(in) :: n
      real :: arr(n)
    end function
  end interface
  res = make(n)
end subroutine
''')

            alloc_ll = tmpdir / "alloc_wasm.ll"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -S -emit-llvm "{alloc_f90}" -o "{alloc_ll}"')
            if result.returncode != 0:
                print("FAIL: Failed to compile allocatable array for wasm32")
                sys.exit(1)

            alloc_ir = alloc_ll.read_text()
            print("\nwasm32 malloc IR snippet:")
            for line in alloc_ir.splitlines():
                if "malloc" in line.lower():
                    print(f"  {line}")

            if "declare ptr @malloc(i32)" in alloc_ir:
                print("PASS: wasm32 malloc uses i32 parameter")
            else:
                if "declare ptr @malloc(i64)" in alloc_ir:
                    print("FAIL: wasm32 malloc uses i64 instead of i32")
                else:
                    print("FAIL: malloc declaration not found in IR")
                sys.exit(1)

            # Step 3: End-to-end test
            print("\n" + "=" * 60)
            print("Step 3: End-to-end test (compile, link, run)")
            print("=" * 60)

            # Compile to object
            hello_o = tmpdir / "hello.o"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -c "{hello_f90}" -o "{hello_o}"')
            if result.returncode != 0:
                print("FAIL: Failed to compile to object")
                sys.exit(1)

            # Link with emcc
            hello_js = tmpdir / "hello.js"
            result = run(f'emcc "{hello_o}" "{wasm32_runtime}" -o "{hello_js}"')
            if result.returncode != 0:
                print("FAIL: Failed to link with emcc")
                sys.exit(1)

            # Run with Node.js
            result = run(f'node "{hello_js}"')
            if result.returncode != 0:
                print("FAIL: Failed to run with Node.js")
                sys.exit(1)

            if "Hello from wasm32!" in result.stdout:
                print("PASS: wasm32 program ran successfully")
            else:
                print("FAIL: Expected 'Hello from wasm32!' in output")
                sys.exit(1)

    # Step 4: Install wasm32 runtime
    if not args.skip_install:
        print("\n" + "=" * 60)
        print("Step 4: Installing wasm32 runtime library")
        print("=" * 60)

        if not wasm32_runtime.exists():
            print(f"Error: {wasm32_runtime} not found. Run with --skip-build=false first.")
            sys.exit(1)

        # Find native flang_rt directory to install wasm32 runtime as sibling
        try:
            native_rt_dir = find_native_flang_rt_dir(install_dir)
            print(f"Found native flang_rt in: {native_rt_dir}")
            wasm32_rt_dir = native_rt_dir.parent / "wasm32-unknown-emscripten"
        except FileNotFoundError:
            # LLVM 20.x: no native flang_rt (FLANG_INCLUDE_RUNTIME=OFF)
            print("No native flang_rt found, using default install path")
            wasm32_rt_dir = install_dir / "lib" / "wasm32-unknown-emscripten"
        wasm32_rt_dir.mkdir(parents=True, exist_ok=True)

        # Copy runtime
        import shutil
        dest = wasm32_rt_dir / "libflang_rt.runtime.wasm32.a"
        shutil.copy2(wasm32_runtime, dest)

        print(f"Installed: {dest}")
        print(f"PASS: wasm32 runtime installed as sibling to native flang_rt")

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
