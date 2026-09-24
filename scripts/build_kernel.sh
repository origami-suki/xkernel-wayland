#!/usr/bin/env bash
# Recreate the M0 configuration and build through the pinned kernel's Makefile.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
kernel_dir="$project_dir/sources/x-kernel"
toolchain_dir="$project_dir/work/toolchains/aarch64-linux-musl-cross"
[[ -x "$toolchain_dir/bin/aarch64-linux-musl-gcc" ]] || {
    echo 'Missing pinned cross toolchain; see docs/tasks/M0-002.md' >&2
    exit 2
}
export PATH="$toolchain_dir/bin:$PATH"
export CARGO_BUILD_JOBS="${M0_BUILD_JOBS:-2}"
[[ "$CARGO_BUILD_JOBS" =~ ^[1-9][0-9]*$ ]] || { echo 'M0_BUILD_JOBS must be positive' >&2; exit 2; }
for tool in aarch64-linux-musl-gcc aarch64-linux-musl-ar aarch64-linux-musl-ranlib rust-objcopy; do
    command -v "$tool" >/dev/null || { echo "Missing $tool; see docs/tasks/M0-002.md" >&2; exit 2; }
done
mkdir -p "$project_dir/artifacts/build-m0"
run_dir=$(mktemp -d "$project_dir/artifacts/build-m0/build-XXXXXXXX")
exec > >(tee "$run_dir/build.log") 2>&1
echo "Build evidence: $run_dir"
cd "$kernel_dir"
git rev-parse HEAD > "$run_dir/kernel-commit.txt"
git status --porcelain > "$run_dir/kernel-status-before.txt"
git -C "$project_dir" rev-parse HEAD > "$run_dir/integration-commit.txt"
uname -a > "$run_dir/host.txt"
rustc -Vv > "$run_dir/rustc.txt"
aarch64-linux-musl-gcc --version > "$run_dir/gcc.txt"
rust-objcopy --version > "$run_dir/objcopy.txt"
cp platforms/kplat-aarch64/qemu_defconfig .config
make defconfig
make build
cp .config "$run_dir/kernel.config"
cp target/xkmake/kplat-aarch64/release/bundle.toml "$run_dir/bundle.toml"
readelf -n target/xkmake/kplat-aarch64/release/kernel.elf > "$run_dir/elf-notes.txt"
sha256sum target/xkmake/kplat-aarch64/release/kernel.{elf,bin} > "$run_dir/kernel.sha256"
git status --porcelain > "$run_dir/kernel-status-after.txt"
echo 'Build complete; guest boot is a separate check with scripts/run_guest.py.'
