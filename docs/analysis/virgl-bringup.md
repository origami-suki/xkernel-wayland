# virgl GPU 加速链路（M2-004）

## 已完成的宿主模块

2026-09-27 在集成 `83cad658`、内核 `855a8c3c` 的准确起点补齐宿主组件。没有升级已安装的软件；新增的三个 QEMU 模块均与原 QEMU `11.1.1-4` 匹配：

```sh
sudo pacman -S --needed qemu-hw-display-virtio-gpu-gl \
  qemu-hw-display-virtio-gpu-pci-gl qemu-ui-egl-headless virglrenderer
```

实际安装记录为 `artifacts/work/m2-004-virgl-20260927/host-install*.log`，安装前后包清单、包文件 SHA256 为同目录 `freeze.json`、`host-packages-manifest.json`。实际新增 virglrenderer 为 `1.3.0-2`，没有 removed/changed 包。

宿主 NVIDIA 节点已通过 EGL/GL 和 QEMU virgl 初始化；render node 不需要从桌面解绑：

```sh
python3 scripts/probe_virgl_host.py \
  --render-node /dev/dri/by-path/pci-0000:01:00.0-render \
  --output artifacts/work/m2-004-virgl-20260927/<新的宿主检查目录>
```

该脚本自行编译自编 C EGL 探针，使用明确节点建立 GBM/EGL/OpenGL context，拒绝常见软件 renderer。随后启动没有 kernel/rootfs、保持 `-S` 的 QEMU，核对 live argv、virgl qtree、进程实际打开的节点，取得 monitor screendump，正常 quit。它不是 guest 或 Chromium 功能测试。

独立实际运行 `independent-host-smoke/` 的结果：

| 项目 | 观察 |
| --- | --- |
| 宿主请求节点 | `/dev/dri/by-path/pci-0000:01:00.0-render` |
| 当次解析节点 / driver | `/dev/dri/renderD128` / `nvidia` |
| OpenGL renderer | `NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2` |
| EGL / OpenGL | 1.5 / 4.6.0，NVIDIA 615.71.09 |
| QEMU | AArch64/cortex-a76，TCG thread=multi，2 GiB、4 vCPU；virtio-gpu-gl-pci + egl-headless |
| Monitor 原图 | 640×480，921615 字节，完整 PPM；没有 guest 内容 |
| QEMU / probe 退出 | 0 / 0 |

独立验证运行全部 42 项 Python host 测试与相关 py_compile，均退出 0；也核对两个 runner 的完整默认 2D argv 未变化、virgl 节点选择及 metadata。验证位置为 `independent-verify/validation.json`、`commands.json`、`all-host-tests.log`。

独立验证发现 probe 中途 screenshot metadata 被最终 metadata 覆盖，却被计入自身哈希。最终版本排除 `metadata.json` 自身；独立 mock 执行真实 capture_frames 中途写入与 finally 覆盖，清单全部匹配，见 `independent-verify/final-manifest-verify.json`。该单行修复不改变 QEMU 运行逻辑，最终没有再跑真实 QEMU；前版真实运行证据保留原样。

## AMD 宿主退出故障

同机器 AMD 节点 `/dev/dri/by-path/pci-0000:06:00.0-render`（当次 renderD129、amdgpu）的 C EGL 探针报告 Radeon 780M 硬件 renderer、Mesa 26.2.3。但无 guest 的 QEMU 在 monitor quit 阶段 SIGSEGV，退出 -11；coredump 栈为 `qemu_display_cleanup → egl_cleanup → eglReleaseThread → driUnbindContext → libgallium`。

关闭 `GALLIUM_THREAD` 的一次定向检查仍失败；不是有效解决方案，不写入默认启动配置。故障及失败样本分别位于 `host-smoke/`、`host-smoke-no-gallium-thread/`。实际本机 NVIDIA headless 路线成功，因此后续使用已验证的 NVIDIA by-path 节点；未修改 QEMU/Mesa、未解绑实体显卡、未通过强制终止将故障记为成功。

## 显式运行入口

两个 guest runner 均接受：

```sh
--gpu-backend virgl --render-node /dev/dri/by-path/pci-0000:01:00.0-render
```

默认仍为 `--gpu-backend 2d`。virgl 必须显式传入存在且可读写的 render 字符设备；支持 by-path 链接，记录请求与解析路径以及相关宿主图形环境变量。2D 模式拒绝误传 render node。runner 继续核对实际 `/proc/<pid>/cmdline`。

仅增加上述参数不能让当前 x-kernel 的 Chromium 获得 GPU 加速。所有运行 metadata 的 `guest_acceleration_verified` 初始值为 false，实际图像和 renderer 须另外核对。

## Guest 当前验证结果

最终 GPU 命令/传统 virtgpu ABI、精确 descriptor 回滚和 PRIME/render/KMS 前端已引入为 `540dc4ee`、`80a11415`、`5ed709c5`。独立最终 372/372 单测、fresh coverage、build/clippy/fmt 及实际 query/caps、raw 4096 像素、真实 PRIME/SCM_RIGHTS 跨创建方关闭共享、原 Mesa 25.1.9 virgl/NVIDIA EGL 64 像素回读均通过。默认 2D 正式 monitor 红绿图、fbdev/mapping 和 KMS disable/RMFB 生命周期也通过；主区 `5ed709c5` 再次原语/2D 回归正常退出。准确 70 文件受测源码哈希、命令与证据见[任务卡](../tasks/M2-004.md)。不将目标单测的回收断言等同实时硬件后台资源计数。

随后在准确 `5ed709c5` 定位到绝对 sysfs 链接导致 root/kiosk libudev lookup 失败和 enum0，原 Weston 因此无法发现 card0，Chrome 未启动。上游 [!796](https://gitee.com/openkylin/x-kernel/pulls/796) 的 `720e85ad` 仅作最小 helper/静态链接 backport；独立 `ceca52ae` 五文件静态审查、227/227 单测及 fresh coverage、root/kiosk lookup+enum2、两轮 GPU 原语、2D/KMS 均通过。原 Weston 14.0.2 单一 GL renderer、原 seat 配置实际达到 virgl/NVIDIA、Virtual-1、Wayland socket ready，Weston/seat/guest/QEMU/runner 正常退出 0。未启动 Chromium，未引入动态 sysfs/整个 PR。

当前主内核 `1e86ceaf` 已通过 `cherry-pick -x` 引入该准确最小修复，集成冻结点 `983d102e`。主区独立发现/GPU/2D/Weston 四轮已通过；原 Chromium GPU 会话的结果见下节，完整 M2-004 尚未通过。

## Chromium 与图像的证据边界

Linux 同用户态原 GL 会话的最终 CDP 已证明硬件 ANGLE virgl、GPU compositing enabled、原始 URL/title；Browser.close ACK 后实际 SIGSEGV11，正常退出失败。修复前 x-kernel `5ed709c5` 的 VNC 应用轮次停在 Weston 设备发现，Chromium NOT RUN；这个历史发现故障由最小 sysfs 候选修复，本轮主区 Chrome 已运行：最终有效 CDP 证明硬件 ANGLE virgl/NVIDIA、GPU compositing enabled、原始 URL/title；VNC 补充图像仅白色主体/黑色顶部，无原页正文；Browser.close ACK 后真实 wait status139（信号11/core位），helper1，Weston/seat0，guest1/QEMU0/runner1，无 forced kill。分别见证据根目录 `linux-chromium-gpu-observer/REPORT.md`、`xkernel-chromium-gpu-vnc-independent/REPORT.md` 和 `sysfs-relative-candidate/REPORT.md`，不合并成原页加速通过。当前主集成最终报告在 `main-sysfs-chromium-independent/{REPORT.md,result.json}`，原始 CDP JSON、服务日志、bundle/工作盘及补充 RFB/PPM 均有索引。自编观察器 Browser.close 错误回复漏判仅八行修复，经独立差异审查、57/57 host 测试、严格编译/readback 通过；原三方/页面/参数未改。下一项最小验证待主 Agent 根据应用失败证据安排，不扩大归因。

QEMU 11.1.1 的实际 monitor screendump 返回 `Error: no surface`。准确源码核对说明 virgl scanout 为 SCANOUT_TEXTURE，`qemu_console_surface` 对非 SURFACE 返回 NULL，screendump 拒绝；EGL-headless 内部 CPU 回读不改变 console 类型。Linux observer 原报告保留核对与运行错误。正式 virgl 纹理 monitor 图像因此 **BLOCKED**，不能据此判为 guest 渲染失败，也不能改用 VNC 作为正式验收。

最后一项 Linux VNC 功能对照采用相同 newobserver/flags/session18/原用户态与 NVIDIA 参数，独立 Linux 6.12.110 工作盘；仅遇既有 render node EACCES 才做 runtime root:video0660。Linux 补充图像可见原页标题/四色块/表格/表单，与 x-kernel 白色正文不同。这证明同用户态及通道能够显示原页，未定位 x-kernel 白屏根因；Linux close ACK 后也 SIGSEGV11，正式 monitor 仍 BLOCKED，图像只能作为控制证据。原始索引在 `main-sysfs-chromium-independent/linux-vnc-control/`，未复跑软件基线/性能，未扩展开发。

可选 VNC 补充六文件经独立静态审查、52 项 host 测试与 py_compile；实际 NVIDIA red/green 原始 RFB 全像素核对 PASS。metadata 明确 formal BLOCKED，补充图像不证明 Chromium 原页或硬件 renderer，不提升 `guest_acceleration_verified`。此补充采集与既有正式 2D monitor 原图分别保留。

固定 `v0.2.0` 起点、AArch64/cortex-a76、TCG thread=multi、2 GiB、4 vCPU、NVIDIA 明确 render node 不变；保留原 Mesa 25.1.9、Weston 14.0.2、Chromium 142 和原始页面，没有新软件基线。历史所有权审查、texture/CLOEXEC 失败及 AMD 退出故障仍可追溯。**没有 GPU 原页首帧耗时或性能结论；按用户要求未复跑软件渲染基线或性能对照。**

协议与模式核对使用 [QEMU virtio GPU 文档](https://www.qemu.org/docs/master/system/devices/virtio/virtio-gpu.html)、[Mesa virgl 文档](https://docs.mesa3d.org/drivers/virgl.html)；本机行为由 QEMU 11.1.1 实际选项和上述运行证据验证。宿主清理路径另核对了 [QEMU v11.1.1 egl-helpers.c](https://github.com/qemu/qemu/blob/v11.1.1/ui/egl-helpers.c)。
