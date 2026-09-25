# 同一工作镜像的 Linux 首帧对照（2026-09-25）

目的：核对原页启动约 78–79 秒是否是这份用户态镜像在当前 QEMU 配置下的普遍表现。使用与 x-kernel 测量相同的工作镜像、Chromium 参数、QEMU 设备和每秒截图方法，只运行一次 Linux 对照。

## 已观察到的显示结果

| 阶段 | Linux 本次采样区间 | x-kernel 既有单次采样 |
| --- | --- | --- |
| 首个非黑浏览器窗口 | 9.018–10.026 秒 | 约 73–74 秒 |
| 原始页面正文首次可见 | **10.017–11.028 秒** | **78.004–79.027 秒** |
| 与 first-runnable 内容帧完全相同 | 第 12 秒采样，结束于 12.012 秒 | 第 79 秒采样 |

Linux 第 10 秒是提示栏、全屏提示及空白正文；第 11 秒已显示原始标题、四色块、表格、SVG 和表单，顶部仍有短暂全屏提示；第 12 秒提示消失。三帧均已目视核对。第 12 秒原始 PPM SHA256 为 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`，与 x-kernel 第 79 秒及不可变 first-runnable 内容帧完全相同。

时间起点与 [x-kernel 测量](first-frame-1s-20260925.md) 相同：主机收到 guest 在浏览器启动命令前打印的标记；包含 su/exec，排除 Weston 准备。区间使用前一张尚无内容截图的采集开始，到第一张内容截图的采集结束，保留采样和串口传输误差。这不是 IPC 握手时长。

## 输入和必要差异

- 工作镜像：`work/images/chromium-m1-fcntl-unknown-v1.img`，SHA256 `9a122bcadc8efe19100f3e43cc72d3bc77796573b3572705658e2c6e74f59941`，与 x-kernel 78–79 秒测量使用的镜像逐字节相同。
- Linux 来自该盘的 `6.12.110-0-lts`，runner 保留原内核/initramfs 与派生启动材料；不更换 rootfs 中的 Chromium、库或测试页。
- 两侧相同：QEMU 11.1.1、AArch64/cortex-a76、virt/gic-version=3、TCG thread=multi、2 GiB、4 vCPU；virtio-blk/rng/gpu/keyboard/mouse，无网卡，snapshot 模式。
- 两侧相同：原生 Wayland、Weston DRM/pixman、`--no-sandbox --ipc-connection-timeout=120`、原始 index、WAYLAND_DEBUG/stderr 日志；测量脚本仍等待 150 秒，host 每秒执行 monitor screendump。
- 必要差异：Linux 使用既有 `SEATD_VTBOUND=1` 会话，x-kernel 使用 0；内核、驱动及启动/initramfs 路径不同。
- 比较脚本已核对磁盘 hash、采样器 hash、原浏览器测量脚本和 QEMU CPU/内存/设备/显示参数；结果为 `artifacts/M1-003-linux-comparison-20260925/input-comparison.json`。

原始 Linux runner 只有 guest 标记截图入口。本次为它接入与 x-kernel 同一份 `OneSecondSampler`，增加 `--sample-every-second`；没有另写计时算法或改变浏览器参数。18 项既有 host 测试、Python 编译、shell 语法和差异检查通过。

## 复跑入口与证据

先生成 Linux 入口：在 `tests/chromium/measure-first-frame.sh` 原文前加一行 `export SEATD_VTBOUND=1`。本次生成文件及原脚本 hash 在 `artifacts/M1-003-linux-comparison-20260925/`。

```sh
python3 scripts/run_linux_guest.py \
  --run-id <新的运行名> \
  --disk work/images/chromium-m1-fcntl-unknown-v1.img \
  --guest-commands artifacts/M1-003-linux-comparison-20260925/linux-measure-first-frame.sh \
  --sample-every-second --timeout 240
```

- 原始运行：`artifacts/runs/m3-linux-first-frame-1s-20260925/`。
- monitor 原始边界图：`frame-sample-010.ppm`、`frame-sample-011.ppm`、`frame-sample-012.ppm`；同名 PNG 仅供预览。
- 输入、受测 runner patch、测试输出和分析：`artifacts/M1-003-linux-comparison-20260925/`。
- 完整取得 150 张原图（sample-000 至 sample-149），由 guest 停止标记结束；第 12 秒起所有采样帧均与 baseline 内容帧同 hash。
- Chromium/Weston/seatd 均正常退出 0，没有强制终止标记；guest/QEMU/runner 均 0，`guest-poweroff-f`，磁盘前后 hash 不变。
- 相邻采集开始间隔 0.981622–1.019114 秒，中位 1.003039 秒；单次 screendump 耗时 7.365–13.028 毫秒。

成功 Linux 日志仍有 4 次 GPU 初始化失败退出及 Vulkan 扩展不支持消息，但没有 `EnsureConnected()` 超时或 Network Service 重启消息。统计只取第一份完整 RAW_LOG browser，排除重复导出；这些报错的存在或数量不能直接解释 x-kernel 额外的启动等待。

## 解释范围

这次同盘 Linux 在约 10–11 秒显示原页，说明这份用户态组合在相同 QEMU 资源配置下具备明显早于 78–79 秒显示的能力；不支持“镜像本身无法正常显示”或“仅由该镜像必然产生约 80 秒启动时间”的解释。下一步应聚焦 x-kernel 下增加的加载、图形回退、IPC 和调度等待。

这不证明镜像没有任何缺陷，也不把全部额外耗时直接归到某个内核函数。它是一次诊断对照，尚未进行交错多轮、宿主缓存/负载控制或正式五次性能比较，因此不报告稳定均值、加速比例或通用性能结论。
