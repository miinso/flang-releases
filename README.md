# flang-releases

[Flang](https://flang.llvm.org/) (LLVM Fortran compiler) binaries with **wasm32 cross-compilation** support.

## Platforms

| Target                    | Host           |
| ------------------------- | -------------- |
| x86_64-unknown-linux-gnu  | Linux x86_64   |
| aarch64-unknown-linux-gnu | Linux ARM64    |
| x86_64-apple-darwin       | macOS x86_64   |
| arm64-apple-darwin        | macOS ARM64    |
| x86_64-pc-windows-msvc    | Windows x86_64 |

Linux builds are compiled on Alpine (musl libc), producing portable binaries that work on any Linux distribution regardless of glibc version.

Each release includes `libflang_rt.runtime.wasm32.a` for cross-compiling Fortran to WebAssembly.

## Release Assets

Release artifact naming is stable:

- `flang+llvm-{version}-{target_triple}.tar.gz` (Linux, macOS)
- `flang+llvm-{version}-{target_triple}.zip` (Windows)

Every release also includes:

- `SHA256SUMS.txt`
- `release-metadata.json`

Compiler entrypoints are normalized in every archive:

- Unix: `bin/flang` and `bin/flang-new`
- Windows: `bin/flang.exe` and `bin/flang-new.exe`

## Usage

Download from [Releases](https://github.com/miinso/flang-releases/releases) and extract.

```bash
# Native compilation
flang-new -o hello hello.f90

# Cross-compile to wasm32 (requires Emscripten)
flang-new -c --target=wasm32-unknown-emscripten -o hello.o hello.f90
emcc hello.o -L$FLANG/lib/clang/21/lib/wasm32-unknown-emscripten -lflang_rt.runtime.wasm32 -o hello.js
```

## CMake

```cmake
set(CMAKE_Fortran_COMPILER /path/to/flang-new)
```

Or via command line:

```bash
cmake -DCMAKE_Fortran_COMPILER=/path/to/flang-new ..
```

## Make

```makefile
FC = /path/to/flang-new
FFLAGS = -O2

%.o: %.f90
	$(FC) $(FFLAGS) -c $< -o $@
```

## Bazel

Use with [rules_fortran](https://github.com/miinso/rules_fortran):

```starlark
load("@rules_fortran//fortran:repositories.bzl", "flang_register_toolchains")

flang_register_toolchains()
```

## Why

Fortran remains widely used in numerical computing, scientific simulation, and optimization libraries. This project enables running such code in browsers and other WebAssembly runtimes—useful for interactive demos, client-side computation, or porting legacy numerical code to the web.

## Further Reading

- [Fortran in the Browser](https://chrz.de/2020/04/21/fortran-in-the-browser/) (2020)
- [Compile FORTRAN to WebAssembly and Solve Electromagnetic Fields in Web Browsers](https://niconiconi.neocities.org/tech-notes/fortran-in-webassembly-and-field-solver/) (2023)
- [Fortran on WebAssembly](https://gws.phd/posts/fortran_wasm/) (2024)
- [LLVM Fortran Levels Up: Goodbye flang-new, Hello flang!](https://blog.llvm.org/posts/2025-03-11-flang-new/) (2025)
- [math/openblas: switch to flang](https://bugs.freebsd.org/bugzilla/show_bug.cgi?id=228011) (2025)

## Build

### CI Topology

- `ci-pr.yml`: lightweight PR validation (Linux x86_64, preset `linux-x86_64`, wasm32 e2e)
- `ci-full.yml`: full 5-platform builds (scheduled Tue/Fri 01:00 UTC + manual)
- `release.yml`: release-only workflow, consumes artifacts from `ci-full` run or rebuilds

`release.yml` workflow_dispatch inputs:

- `rebuild` (bool): rebuild all platforms in release run
- `source_run_id` (string): consume artifacts from a specific `ci-full` run
- `tag` (string): override release tag
- `version` (string): single source of version truth for artifact/tag/build (recommended to fill this only)
- `llvm_fork_repo` (string, advanced): LLVM fork repo override
- `llvm_fork_ref` (string, advanced): LLVM fork ref override
- `draft` (bool): draft/public release toggle

When `rebuild=false`, `release.yml` downloads artifacts from an existing successful `ci-full.yml` run (same commit SHA by default, or explicit `source_run_id`).
When `rebuild=true`, `release.yml` builds from `llvm_fork_repo`/`llvm_fork_ref` (input override or `versions.env`/auto defaults).

All build jobs use `.github/workflows/_build-platform.yml`.

### LLVM Source Policy

LLVM source is pulled from fork, not from upstream `llvm/llvm-project` directly.

- `LLVM_FORK_REPO` and `LLVM_FORK_REF` are defined in `versions.env`
- CI fails immediately if `LLVM_FORK_REF` is empty
- Fork ref is expected to include wasm32 patch set already applied

### Automatic LLVM Version Tracking

`llvm-version-watch.yml` runs daily and:

1. Finds latest LLVM patch version in `TRACKED_LLVM_MINOR`
2. Opens a PR when `versions.env` needs an update
3. Updates `LLVM_VERSION` and `LLVM_FORK_REF` using `flang-wasm32-llvmorg-{version}` convention
