# XKernelGUISupport

“中国电子杯”赛题六的构建、测试与证据管理仓库。目标是在 x-kernel 上运行 Chromium，保存首个可运行版本，再通过测量改进性能。

## 已确定的开发条件

- x-kernel 起点：`v0.2.0`，提交 `c548c427af64bc4fcb7ba9d486d77e006be69b4f`；不主动同步上游。
- AArch64 QEMU `11.1.1`（本机系统包 `11.1.1-4`），纯 TCG，2 GiB RAM，4 vCPU。开发阶段设备可按需要添加，最终记录完整设备参数。
- 使用 `../Assets/agentos-disk.img.xz` 和 `../Assets/testpages.tar.xz`，原始材料保持不变。
- 已确定采用 Wayland：复用赛方 rootfs 的 Chromium、musl、字体和已有运行库，在工作副本补齐 Weston 与会话配置；主线为原生 Wayland Chromium → Weston → DRM/KMS → virtio-gpu。
- 用户负责目标、重要取舍与阶段验收；Agent 完成实现、验证、提交和学习材料。
- 按验收条件推进，不预设日历排期。

## 入口

- [Agent 约定](AGENTS.md)
- [Git 与提交约定](CONTRIBUTING.md)
- [版本与首个可运行基线](docs/baseline.md)
- [阶段与任务](docs/roadmap.md)
- [验收矩阵](docs/acceptance.md)
- [任务卡模板](docs/tasks/TEMPLATE.md)

## 获取与检查

```sh
git submodule update --init
git config --local core.hooksPath .githooks
python3 scripts/check_project.py --verify-assets
```

`sources/x-kernel` 保留内核上游历史，集成仓库通过 submodule 指针固定实际使用的提交。不要使用 `git submodule update --remote`。新环境需将提供的 Assets 放在本仓库的同级目录。

机器专用配置可放入被忽略的 `.local/`。本机默认 QEMU 已统一为系统包提供的 `/usr/bin/qemu-system-aarch64`，实际版本为 `11.1.1`（用户所指系统新版，经检查并非 `11.0`）。旧的 `/usr/local` QEMU `5.2.0` 已卸载；`qemu-img`、`qemu-io` 和 `qemu-nbd` 也使用系统 `11.1.1`。检查脚本接受 `--qemu /absolute/path`，并检查版本与 `config/baseline.json` 一致。已补齐系统 `virtio-gpu` / `virtio-gpu-pci` 及 GTK 显示模块（`qemu-ui-opengl` 是 GTK 包依赖），virtio 键鼠设备由系统模拟器提供。详见 [M0-005 环境任务卡](docs/tasks/M0-005.md)。

当前推进状态以 [路线图](docs/roadmap.md) 为准，接口实现与运行证据见 [M1-004](docs/tasks/M1-004.md)。构建/启动入口为 `scripts/build_kernel.sh` 和 `scripts/run_guest.py`；fd/共享映射/seals 对照入口为 `scripts/prepare_rights_tests.py`。[M2-001](docs/tasks/M2-001.md) 的 Weston/wl_shm 显示及正常退出已验证，入口为 `tests/drm/session.sh`。下一项是 [M3-001](docs/tasks/M3-001.md) 的 Chromium 原始页首帧；真实键鼠和长时稳定性仍未验证。

Wayland 工作盘重建：`python3 scripts/rootfs_prepare.py --output work/images/new-wayland.img --evidence artifacts/new-wayland`（输出盘和证据目录须未使用）。精确包锁、准备步骤和已知限制见 [用户态说明](docs/analysis/wayland-rootfs.md)。
