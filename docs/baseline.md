# 版本与性能基线

## 已确认的开发起点

用户指定 x-kernel `v0.2.0`。这是 annotated tag：tag 对象为 `7e556fe8dffbb0db80a5c29fb7a52de69af04f93`，解引用后的代码提交为 `c548c427af64bc4fcb7ba9d486d77e006be69b4f`。原始仓库位于 https://gitee.com/openkylin/x-kernel 。实际开发提交由 submodule 指针保存，基于该提交继续开发，不主动同步上游。

开发固定 AArch64、TCG、2 GiB RAM、4 vCPU。用户确认开发设备不受限制；每次测试须记录实际设备组合。最终比赛平台由组委会提供，不将本地设备选择冒充决赛配置。guest 配置的 2 GiB RAM、rootfs 镜像的 2 GiB 文件大小、决赛性能资格线的 1.5G 峰值内存是三个不同概念。

输入清单及 SHA-256 见 `config/baseline.json`。原始压缩包保持只读使用，后续解压到工作目录，并为解压镜像和工作镜像分别记录校验和/生成步骤。

用户已确定 Wayland + 复用赛方 rootfs。工作副本保留现有浏览器和运行库，增量补齐 Weston/必要依赖、Wayland 会话入口与原始测试页；首轮优先验证原生 Wayland Chromium 和 Weston DRM/pixman。包版本、来源、增量配置和启动参数必须可重建。镜像带 Xorg 只说明其原始配置，不改变当前路线。

## 本机现状与源码差异

- 2026-09-24 已按用户要求卸载 `/usr/local` 的 QEMU 5.2.0。默认命令现在选择 `/usr/bin/qemu-system-aarch64`，实际版本 **11.1.1**（用户口述 11.0 指系统新版）；精确版本见 `config/baseline.json`。
- 已补齐系统 virtio-gpu 与 GTK 模块，完成 TCG/2 GiB/4 vCPU、GPU/keyboard/mouse 加载及 monitor screendump 的 host smoke；这没有启动 guest，真正内核启动仍待 M0-002。迁移范围与原始证据见 [M0-005](tasks/M0-005.md)。
- v0.2.0 的实际 defconfig 为 `platforms/kplat-aarch64/qemu_defconfig`。README 与构建技能中的 `platforms/aarch64-qemu-virt/defconfig` 在此版本不存在。
- 实际 defconfig 已启用 `KFEAT_DRIVER_VIRTIO_GPU` 和 `KFEAT_DRIVER_VIRTIO_INPUT`；这只能证明配置开启，不能证明图形运行正常。
- `rust-toolchain.toml` 固定 Rust 1.95.0；格式化流程另提到 nightly-2026-03-08，相关工具是否齐全在构建任务中确认。
- Makefile 的运行变量包括 `MEM`、`SMP`、`ACCEL` 和 `DISK_IMG`。启动封装需明确传入 2g、4、n 和工作镜像，不依赖默认值。
- `GRAPHIC=y` 添加 GPU，不自动添加 keyboard/mouse。guest 输入设备须显式添加并验证；host 模块可加载不等于 guest 驱动/枚举通过。
- 赛方 rootfs 带 Xorg/JWM/Chromium，不带 Weston；内核 init 与镜像 BusyBox inittab 尚未接通，镜像默认欢迎页不是官方测试页。详见 [rootfs 调查](analysis/rootfs-and-memory.md)。

## 本队首个可运行版本

由本队按以下最小条件建立 `baseline/first-runnable`：

1. 在本项目 x-kernel/QEMU 环境内通过已确定的 Wayland 会话运行 Chromium，记录实际 Ozone/协议链路；
2. 创建实际可见的浏览器窗口；
3. 正确显示提供的原始 `index.html`，保存 QEMU monitor screendump、运行日志和完整命令。

首次满足时立即存档，保留全部来源与状态。10/30 分钟稳定性、完整交互与优化有独立里程碑，不用它们推迟首次存档。该定义是团队开发约定，不声称是组委会追加的解释。

存档应同时固定集成与内核提交、所有本地补丁、rootfs 工作镜像/构建方法及校验和、用户态版本、配置和命令。不得将未提交工作区结果关联到干净 tag。截图和日志必须可追溯到存档版本；立即测量当时可测的指标，其余记录缺失原因。tag 创建后不移动、不覆盖。

## 测量与比较

Linux 是接口行为对照；本队首个可运行版本是性能比较起点。镜像自带 Linux 内核/initramfs，可优先验证作为同 rootfs 对照，尚未启动。对照必须使用相同的 Weston/依赖增量和会话参数，不能以 Linux 原 X11 会话成功推导 x-kernel Wayland 已具备能力。每次测试记录主机 CPU、内存、操作系统、QEMU 版本、TCG 参数、页面/浏览器版本、分辨率、缓存条件和日志级别。每项至少 5 次，保留原始记录，报告中位数及 min/max；失败运行也保留并说明。

首帧、页面加载、renderer 创建和峰值内存的本地测量定义应在观测任务中明确，未来与官方测量脚本核对；不要将 host QEMU RSS 直接当作 guest 浏览器峰值内存。优化比较保留功能与场景一致性，不以降低工作量或增加基线开销制造改善。

v0.2.0 的 procfs/getrusage 存在固定 0 的内存字段，M1-003 须先校准，不能据此宣称满足 1.5G。计量不完整时仍立即保存首次可运行 tag；后来重跑原版本补测时标明补测环境/日期/方法，不移动 tag，不把补测数据说成首次当场已测。
