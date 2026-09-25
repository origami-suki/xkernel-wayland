# 原页首次可见时间：1秒采样（2026-09-25）

本次单次运行 `m3-first-frame-1s-20260925`，原始 index 正文首次可见时间的保守采样区间为 **78.004–79.027 秒，约78–79秒**。

后续同盘、同参数及同一秒采样器的 [Linux 对照](linux-first-frame-1s-20260925.md) 得到正文首次可见 **10.017–11.028 秒**，并正常退出；两次均为单次诊断测量，不作为正式多轮性能结论。

| 画面 | 主机相对采集区间 | 目视结果 |
| --- | --- | --- |
| `frame-sample-078.ppm` | 78.004145–78.014450秒 | 提示栏下仍是空的浅灰正文区 |
| `frame-sample-079.ppm` | 79.015755–79.026897秒 | 原始标题、四色块、表格、SVG和表单可见 |

两帧均已目视核对。第79秒原图SHA256为 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`，与不可变 first-runnable 的正文帧逐字节相同。原图与派生PNG位于 `artifacts/runs/m3-first-frame-1s-20260925/`。

## 测量口径

- 起点是主机收到 guest 在浏览器启动命令前立即打印的标记。包含 su/exec 和后续加载，排除 Weston 准备；串口传输延迟未单独校准。不是精确的 exec 时间戳。
- 截图由主机单调时钟驱动，每秒一次，通过正式 QEMU monitor screendump；guest 不逐秒启动截图辅助命令，也不反复枚举进程。保留原有 WAYLAND_DEBUG 和浏览器 stderr 日志。
- 共151帧，实际相邻采集开始时间间隔0.984563–1.019508秒，中位1.003063秒；单次采集耗时7.680–13.255毫秒，中位8.702毫秒。每帧记录采集前后时间和hash。
- 这是用户要求的单次量级观察，不是五次正式性能比较，也不能据此给出稳定分位数、Linux/x-kernel速度比或最小够用的IPC期限。**页面可见耗时不等于子进程IPC握手耗时。**

## 固定输入与命令

内核保持干净 `31c8f270`，ELF SHA256 `7b17d189f670d69de3769dc20ad505ec0bdebbb805a4474db8ef87e9b881387e`；QEMU11.1.1/cortex-a76/TCG/2GiB/4vCPU，原生Wayland。工作盘SHA256 `9a122bcadc8efe19100f3e43cc72d3bc77796573b3572705658e2c6e74f59941`。浏览器仍为赛方Chromium142，`--ipc-connection-timeout=120 --no-sandbox`，原始index保持不变。

```sh
python3 scripts/run_guest.py --run-id <新的运行名> \
  --disk work/images/chromium-m1-fcntl-unknown-v1.img \
  --bundle artifacts/M1-011-fcntl-unknown-20260925/clean-bundle \
  --guest-commands tests/chromium/measure-first-frame.sh \
  --sample-every-second --timeout 240
```

脚本/制备检查、原始受测diff、18项host测试与时间分析索引保存在 `artifacts/M1-003-second-sampling-20260925/`；最终退出状态以run的metadata为准。此次没有更改内核或移动baseline标签。

最终采样按guest停止标记结束，151帧完整；随后Chromium清理请求强制KILL后wait未返回，runner在240秒上限以monitor退出，`timeout-commands`、QEMU=0/runner=1。测量区间有效，应用正常退出仍未通过；不能将runner的失败状态改写为通过。盘hash前后相同。
