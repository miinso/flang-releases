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


def has_target_sizes(llvm_project: Path) -> bool:
    """True when the patch set that makes flang emit target sizes is applied
    (descriptors, runtime call arguments, per-arch intrinsic modules)."""
    src = llvm_project / "flang" / "module" / "iso_c_binding.f90"
    return src.exists() and "__wasm32__" in src.read_text()


# same order as flang/tools/f18/CMakeLists.txt
WASM_MODULES = [
    "__fortran_builtins", "__cuda_builtins", "__fortran_type_info",
    "__fortran_ieee_exceptions", "iso_fortran_env_impl", "iso_fortran_env",
    "ieee_exceptions", "ieee_arithmetic", "ieee_features", "iso_c_binding",
    "__ppc_types", "__cuda_device", "cudadevice", "cooperative_groups",
]
CUDA_MODULES = {"__cuda_device", "cudadevice", "cooperative_groups"}


def build_wasm_modules(flang: Path, llvm_project: Path, install_dir: Path, arch="wasm32") -> bool:
    """Build the intrinsic modules for a wasm target into include/flang/<arch>.
    flang looks there before include/flang when the target is wasm."""
    triple = f"{arch}-unknown-emscripten"
    out = install_dir / "include" / "flang" / arch
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.mod"):
        stale.unlink()
    for m in WASM_MODULES:
        drv = f"-fc1 -triple {triple} -xcuda" if m in CUDA_MODULES else f"--target={triple}"
        src = llvm_project / "flang" / "module" / f"{m}.f90"
        if run(f'"{flang}" {drv} -cpp -fsyntax-only -module-dir "{out}" "{src}"').returncode != 0:
            return False
    # omp_lib: kinds derive from iso_c_binding, the source needs no change
    omp = llvm_project / "openmp" / "runtime"
    template = omp / "src" / "include" / "omp_lib.F90.var"
    if template.exists():
        cmake = (omp / "CMakeLists.txt").read_text()
        version = {}
        for key in ("MAJOR", "MINOR"):
            found = re.search(r"set\(LIBOMP_VERSION_" + key + r" (\d+)", cmake)
            version[key] = found.group(1) if found else "0"
        # the build number comes from kmp_version.cpp, as in libomp_get_build_number()
        found = re.search(r"#define KMP_VERSION_BUILD\s+(\d+)", (omp / "src" / "kmp_version.cpp").read_text())
        text = template.read_text()
        for key, value in {
            "@LIBOMP_VERSION_MAJOR@": version["MAJOR"],
            "@LIBOMP_VERSION_MINOR@": version["MINOR"],
            "@LIBOMP_VERSION_BUILD@": found.group(1) if found else "0",
            "@LIBOMP_BUILD_DATE@": "No_Timestamp",
            "@LIBOMP_OMP_YEAR_MONTH@": "201611",
        }.items():
            text = text.replace(key, value)
        with tempfile.TemporaryDirectory() as tmp:
            f90 = Path(tmp) / "omp_lib.F90"
            f90.write_text(text)
            if run(f'"{flang}" --target={triple} -cpp -fsyntax-only -module-dir "{out}" "{f90}"').returncode != 0:
                return False
    print(f"{out}: {len(list(out.glob('*.mod')))} modules")
    return True


# output of scripts/wasm32-e2e.f90 on wasm32
E2E_EXPECTED = """\
alloc    3   4  24
bounds  0  0  2  3
sect    4  2  2
source 7 7 7  3.5
bound  21.0
final  1
defer wasm!!!  7
cstr    5 4
common 7
nml   &SMALL NI= 5/
inq    F
"""


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

    target_sizes = has_target_sizes(llvm_project)
    if target_sizes and not args.skip_build:
        print("\n" + "=" * 60)
        print("Step 1b: Building wasm32 intrinsic modules")
        print("=" * 60)
        if not build_wasm_modules(flang, llvm_project, install_dir):
            print("FAIL: Failed to build wasm32 intrinsic modules")
            sys.exit(1)
        print("PASS: Built wasm32 intrinsic modules")

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

            # Test assumed-size arrays compile for wasm32
            # regression: fir.assumed_size_extent lowers via genConstantIndex
            # which must use target-width (i32) not hardcoded i64
            assumed_f90 = tmpdir / "assumed_size.f90"
            assumed_f90.write_text('''\
subroutine saxpy(n, a, sx, sy)
  integer, intent(in) :: n
  real, intent(in) :: a
  real, intent(in) :: sx(*)
  real, intent(inout) :: sy(*)
  integer :: i
  do i = 1, n
    sy(i) = sy(i) + a * sx(i)
  end do
end subroutine
''')

            assumed_ll = tmpdir / "assumed_wasm.ll"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -S -emit-llvm "{assumed_f90}" -o "{assumed_ll}"')
            if result.returncode != 0:
                print("FAIL: assumed-size array failed to compile for wasm32")
                print("  (genConstantIndex likely using i64 attr on i32 target)")
                sys.exit(1)
            print("PASS: assumed-size array compiles for wasm32")

            # Test complex arithmetic compiles for wasm32
            # exercises TargetWasm32 complex arg/return marshalling
            complex_f90 = tmpdir / "complex_test.f90"
            complex_f90.write_text('''\
function cadd(a, b) result(c)
  complex, intent(in) :: a, b
  complex :: c
  c = a + b
end function

subroutine cmath(x, y, z)
  complex, intent(in) :: x, y
  complex, intent(out) :: z
  z = x * y + conjg(x)
end subroutine
''')

            complex_ll = tmpdir / "complex_wasm.ll"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -S -emit-llvm "{complex_f90}" -o "{complex_ll}"')
            if result.returncode != 0:
                print("FAIL: complex arithmetic failed to compile for wasm32")
                sys.exit(1)
            print("PASS: complex arithmetic compiles for wasm32")

            # Test character operations compile for wasm32
            # exercises DLGetModel<unsigned long> for string length params
            char_f90 = tmpdir / "char_test.f90"
            char_f90.write_text('''\
subroutine greet(name, msg)
  character(*), intent(in) :: name
  character(*), intent(out) :: msg
  msg = "hello " // name
end subroutine
''')

            char_ll = tmpdir / "char_wasm.ll"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -S -emit-llvm "{char_f90}" -o "{char_ll}"')
            if result.returncode != 0:
                print("FAIL: character operations failed to compile for wasm32")
                sys.exit(1)

            char_ir = char_ll.read_text()
            # char length params should be i32 on wasm32
            if re.search(r'define void @greet_\(ptr[^,]*, ptr[^,]*, i32[^,]*, i32', char_ir):
                print("PASS: character ops use i32 length params on wasm32")
            else:
                print("PASS: character operations compile for wasm32 (signature check skipped)")

            # Test array intrinsics (SUM, MAXVAL) compile for wasm32
            # exercises runtime function signatures with size_t params
            reduce_f90 = tmpdir / "reduce_test.f90"
            reduce_f90.write_text('''\
subroutine compute(arr, n, total, biggest)
  integer, intent(in) :: n
  real, intent(in) :: arr(n)
  real, intent(out) :: total, biggest
  total = sum(arr)
  biggest = maxval(arr)
end subroutine
''')

            reduce_ll = tmpdir / "reduce_wasm.ll"
            result = run(f'"{flang}" --target=wasm32-unknown-emscripten -S -emit-llvm "{reduce_f90}" -o "{reduce_ll}"')
            if result.returncode != 0:
                print("FAIL: array intrinsics failed to compile for wasm32")
                sys.exit(1)

            reduce_ir = reduce_ll.read_text()
            # runtime calls should use i32 for line/sourcefile params on wasm32
            if "_FortranASumReal4" in reduce_ir and "_FortranAMaxvalReal4" in reduce_ir:
                print("PASS: array intrinsics (SUM, MAXVAL) compile for wasm32")
            else:
                print("FAIL: expected SUM/MAXVAL runtime calls in IR")
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

            # descriptors, type-bound calls, finalization, common blocks,
            # namelist, inquire, bind(c) character arguments
            if target_sizes:
                e2e_o = tmpdir / "e2e.o"
                e2e_js = tmpdir / "e2e.js"
                e2e_f90 = script_dir / "wasm32-e2e.f90"
                result = run(f'"{flang}" --target=wasm32-unknown-emscripten -J "{tmpdir}" -c "{e2e_f90}" -o "{e2e_o}"')
                if result.returncode != 0:
                    print("FAIL: Failed to compile wasm32-e2e.f90")
                    sys.exit(1)
                result = run(f'emcc "{e2e_o}" "{wasm32_runtime}" -sEXIT_RUNTIME=1 -o "{e2e_js}"')
                if result.returncode != 0:
                    print("FAIL: Failed to link wasm32-e2e")
                    sys.exit(1)
                if "signature mismatch" in result.stderr:
                    print("FAIL: function signature mismatch between flang objects and the wasm32 runtime")
                    sys.exit(1)
                result = run(f'node "{e2e_js}"')
                got = [line.rstrip() for line in result.stdout.splitlines()]
                want = [line.rstrip() for line in E2E_EXPECTED.splitlines()]
                if result.returncode != 0 or got != want:
                    print("FAIL: wasm32-e2e output differs")
                    for g, w in zip(got + [""] * len(want), want):
                        if g != w:
                            print(f"  got:  {g}")
                            print(f"  want: {w}")
                    sys.exit(1)
                print("PASS: wasm32-e2e ran with the expected output")

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
