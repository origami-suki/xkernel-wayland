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

## Guest 所需模块及验收

现有 rootfs Mesa 25.1.9 已含 virgl 实现，当前没有必须替换或升级第三方用户态的证据。当前 x-kernel 使用本地 2D virtio 适配器，DRM identity 为 simpledrm、只有 card0、没有 virtgpu 3D UAPI。完整链按以下独立模块推进：

1. 复用已经锁定的 kvirtiodrivers 0.13.3，暴露真实协商与 capset，保留 2D；不得在关中断自旋锁内暴露长 GPU fence 等待。
2. DRM 每个 file description 的 context/GEM handle 与 close 生命周期、真实 render 节点和发现；只能宣告确实实现的能力。
3. 传统 virgl GETPARAM/CAPS、resource/map/transfer、submit/WAIT、GEM_CLOSE；C probe 执行并读回预期像素，验证错误输入与生命周期。
4. 真正 PRIME/DMA-BUF fd、跨进程 import/export 与同步、3D resource 的 KMS scanout；不得用现有 handle/fd 数值转换冒充共享，也不得用旧 shadow 覆盖 GPU 图像。
5. 原 Mesa EGL/GLES、Weston/Chromium 实际运行，确认 renderer 是 virgl，原始 index 正确显示且正常退出。

首个内核模块正在独立 worktree 实现/验证。**完整 guest GPU 链路尚未完成；本记录没有 GPU 加速后的原页首帧耗时。** 按用户要求，不重跑现有软件渲染基线或性能对照。

协议与模式核对使用 [QEMU virtio GPU 文档](https://www.qemu.org/docs/master/system/devices/virtio/virtio-gpu.html)、[Mesa virgl 文档](https://docs.mesa3d.org/drivers/virgl.html)；本机行为由 QEMU 11.1.1 实际选项和上述运行证据验证。宿主清理路径另核对了 [QEMU v11.1.1 egl-helpers.c](https://github.com/qemu/qemu/blob/v11.1.1/ui/egl-helpers.c)。
