#!/usr/bin/env python3
"""Check the pinned project inputs; this is not a kernel/browser test."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, env=None):
    return subprocess.check_output(args, cwd=ROOT, env=env, text=True, stderr=subprocess.PIPE).strip()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-assets", action="store_true", help="对原始材料计算完整 SHA-256")
    parser.add_argument("--qemu", help="本机 AArch64 QEMU 的绝对路径")
    args = parser.parse_args()
    baseline = json.loads((ROOT / "config/baseline.json").read_text())
    require(baseline["schema_version"] == 1, "不支持的清单版本")
    kernel = baseline["kernel"]
    dev = baseline["development"]
    require(kernel["sync_upstream"] is False, "项目约定不主动同步上游")
    require(dev["architecture"] == "aarch64" and dev["accelerator"] == "tcg", "需要 AArch64/TCG")
    require(dev["memory_mib"] == 2048 and dev["vcpus"] == 4, "当前开发约定为 2 GiB / 4 vCPU")
    source = ROOT / kernel["path"]
    # Hooks export the parent index/repository environment. Clear it only for
    # submodule commands; parent ls-files must still inspect the staged index.
    source_env = os.environ.copy()
    for name in run("git", "rev-parse", "--local-env-vars").splitlines():
        source_env.pop(name, None)
    require((source / kernel["defconfig"]).is_file(), "指定 defconfig 不存在")
    require(run("git", "-C", str(source), "rev-parse", kernel["tag"] + "^{commit}", env=source_env) == kernel["commit"], "tag 与锁定代码提交不符")
    require(run("git", "-C", str(source), "rev-parse", kernel["tag"], env=source_env) == kernel["tag_object"], "tag 对象发生变化")
    run("git", "-C", str(source), "merge-base", "--is-ancestor", kernel["commit"], "HEAD", env=source_env)
    require(not run("git", "-C", str(source), "status", "--porcelain", env=source_env), "内核有未提交改动，请先验证并提交")
    head = run("git", "-C", str(source), "rev-parse", "HEAD", env=source_env)
    entry = run("git", "ls-files", "--stage", "--", kernel["path"]).split()
    require(len(entry) == 4 and entry[:3] == ["160000", head, "0"], "暂存 submodule 指针与内核 HEAD 不一致")
    toolchain = tomllib.loads((source / "rust-toolchain.toml").read_text())
    require(toolchain["toolchain"]["channel"] == kernel["rust_toolchain"], "Rust 工具链与清单不符")
    print(f"PASS kernel: {head}; upstream tag {kernel['tag']} locked")

    qemu = args.qemu or dev["qemu_binary"]
    require(Path(qemu).is_absolute(), "QEMU 路径必须为绝对路径")
    version = run(qemu, "--version").splitlines()[0]
    match = re.search(r"version (\d+)\.(\d+)\.(\d+)", version)
    require(match is not None and int(match[1]) >= max(8, dev["qemu_minimum_major"]), "QEMU 必须 >= 8.0")
    actual_version = ".".join(match.groups())
    require(actual_version == dev["qemu_version"], f"QEMU 与当前开发基线不符：需要 {dev['qemu_version']}，实际 {actual_version}")
    print(f"PASS QEMU: {qemu}: {version}")

    for asset in baseline["assets"]:
        path = ROOT / asset["path"]
        require(path.is_file(), f"缺少输入：{asset['path']}")
        require(re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) is not None, "无效的 SHA-256")
        if args.verify_assets:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            require(digest == asset["sha256"], f"材料校验失败：{asset['path']}")
        print(f"PASS asset {'SHA-256' if args.verify_assets else 'exists'}: {asset['role']}")
    print("项目输入检查通过；未执行内核构建、启动或浏览器验收。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
