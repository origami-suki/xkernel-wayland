# 赛方 rootfs、启动衔接与内存观测核查

核查日期：2026-09-24。代码固定于 `c548c427af64bc4fcb7ba9d486d77e006be69b4f`；本次只做离线文件/ELF/源码检查，未执行镜像内程序、构建或启动内核。文件存在不代表运行通过，包数据库版本不冒充运行时 `--version` 输出。

路线已由用户确认：**Wayland + 复用这份 rootfs**。下文 Xorg/JWM 与 inittab 是原镜像事实，不是继续实施 X11 的建议；在工作副本补齐 Weston、必要依赖及独立会话入口。以下保留离线调查时的状态；后续 M0-003 已完成增量与动态探测，当前结果见 [Wayland 用户态记录](wayland-rootfs.md)，不要将本次历史调查的“未运行”当作当前状态。

## 材料与可复核入口

原始压缩包与哈希见 [baseline.json](../../config/baseline.json)。`xz -dc` 解压到 `work/rootfs-audit/agentos-disk.img` 后移除写权限，全程以不带 `-w` 的 `debugfs` 读取，没有挂载或修改原镜像。

- 解压镜像：2,147,483,648 字节，整个文件直接是 ext4，没有先跳过分区偏移；UUID `aa169e42-e593-4422-999c-01ea5459b0a2`。
- 解压镜像 SHA-256：`441b4504cfa7fef53f8458f28b4a80eb71f4fd08104f9f260627993fce47fe84`。
- ext4 含 `64bit`、`metadata_csum_seed`、`metadata_csum` 等 feature。能被 host debugfs 读取不证明 x-kernel ext4 挂载成功，M0-002 必须实测。
- 原始输出：`artifacts/roadmap-audit/rootfs-inventory.txt`、`rootfs-session.txt`、`rootfs-extra.txt`、`rootfs-dump-results.txt`。
- 文件哈希与包表：`artifacts/roadmap-audit/rootfs-files.json`、`packages.json`；ELF 依赖：`chromium-needed.txt`。

复核命令（从集成仓库根执行，文件已由本轮生成）：

```sh
debugfs -R 'cat /etc/inittab' work/rootfs-audit/agentos-disk.img
debugfs -R 'cat /usr/local/bin/x11-session' work/rootfs-audit/agentos-disk.img
debugfs -R 'cat /lib/apk/db/installed' work/rootfs-audit/agentos-disk.img
readelf -l work/rootfs-audit/chromium.elf
readelf -d work/rootfs-audit/chromium.elf
```

`debugfs cat` 不自动替调用方跟随所有符号链接：直接读 `/etc/os-release` 曾报 short read；读取其实际内容 `/usr/lib/os-release` 成功。`/usr/bin/chromium` 也是链接，应审查 `/usr/lib/chromium/chromium-launcher.sh` 和真正的 ELF，不能把一次链接读取失败说成镜像损坏。

## 真实用户态组合

| 组件 | 包数据库/文件事实 | 对路线的影响 |
| --- | --- | --- |
| 系统与 libc | Alpine 3.22.0；musl 1.2.5-r10；BusyBox 1.37.0-r18 | 动态解释器是 `/lib/ld-musl-aarch64.so.1`，不能照搬 glibc/Debian 启动方式 |
| 浏览器 | Chromium 142.0.7444.59-r0；主 ELF 233,573,096 字节，AArch64 PIE | 已提供浏览器，先验证其 loader/依赖和进程行为，不先构建或替换 Chromium |
| 原显示服务/WM | Xorg 21.1.19-r0；JWM 2.4.6-r1；`/usr/libexec/Xorg` 为 AArch64 ELF | 记录原镜像 X11 组合，文件可保留；新会话由 Weston 替代其启动链 |
| 绘制/显示 | Mesa 25.1.9-r0，libdrm 2.4.124-r0，pixman 0.46.4-r0；有 Xorg modesetting 模块 | 优先复用库；新增 Weston 的 DRM/pixman 构建兼容性需验证。浏览器图形路径单独检查，不能从 compositor 软件合成推导无需 GPU/缓冲接口 |
| 输入 | libinput 1.28.1-r0；xf86-input-libinput 1.5.0-r0 | Weston 可复用 libinput 库；Xorg 输入模块不承担新会话输入，仍需验证 sysfs/libudev/event 节点 |
| Wayland | client/server/cursor/egl 库 1.23.1-r3 | 包表无 Weston；`/usr/bin/weston`、`/usr/lib/weston`、`/etc/xdg/weston` 均不存在。库存在不代表具备 Wayland 会话 |
| 字体 | font-noto-cjk、Noto、Open Sans 已安装 | 页面中文字体不是预设缺口，需用官方页实际截图检查 |
| Linux 对照材料 | linux-lts 6.12.110-r0，`/boot/vmlinuz-lts`、`initramfs-lts` 与模块目录存在 | 可优先评估它们作为相同 rootfs 的 Linux 对照；vmlinuz 是 ARM64 EFI gzip zboot，不能未经验证直接声称 `-kernel` 可启动 |

本轮读取 ELF 头与直接 `NEEDED` 列表，没有完成所有递归依赖解析，也没有验证已有 Chromium 的原生 Wayland/Ozone 能力。补包清单、依赖与运行探测进入 M0-003；不把“带 Chromium”当成新会话必定可用。

## 原镜像的启动衔接

### 1. 内核不会自动启动这份 inittab

`entry/src/main.rs:72` 将 PID 1 固定为 `/bin/sh -c <entry/src/init.sh>`。`entry/src/init.sh:21` 只有发现 `/sbin/init` **并且** PATH 内有 `openrc` 时才切入 init；镜像无 openrc 包及 `/sbin/openrc`，使用 BusyBox init。因此按现有源码静态推断，会落入 `/dev/console` 登录 shell 分支，而不是执行 `/etc/inittab` 的 kiosk 会话；实际启动仍待 M0-002 验证。

原镜像预期链如下，仅供理解来源；新 Wayland 会话不启动这套 X11 脚本：

```text
BusyBox init
  ├─ /etc/init.d/kiosk-boot
  ├─ tty1: /usr/local/bin/x11-session (respawn)
  │    ├─ Xorg :0 vt1 -nolisten tcp -auth /run/x11/auth
  │    ├─ su kiosk → JWM
  │    └─ su kiosk → Chromium → renderer / GPU / utility
  └─ ttyAMA0: getty
```

`kiosk-boot` 包含 mount、mdev、DHCP、后台 NTP；`x11-session` 又依赖 `/dev/ttyAMA0`、urandom/xauth、`su`、Unix socket 和清理旧进程。为新会话保留实际需要的挂载/运行目录准备，新增 Weston 及 Wayland Chromium 的入口；先从串口按层启动并保留退出原因，再整合 init/respawn。官方页面不依赖外网，不应让 DHCP/NTP 成为首个页面的硬前置。

### 2. 原会话指定 X11 + ANGLE/GL

脚本关键参数为 `--ozone-platform=x11 --use-gl=angle --use-angle=gl --disable-vulkan --disable-features=Vulkan --kiosk --incognito`，未带 `--no-sandbox`。脚本注释声称 GLX/llvmpipe；这是镜像作者的设计意图，实际 renderer/GL 后端须从运行日志确认。

原脚本保留作来源，不把 Xorg/GLX 试跑作为前置。新入口明确选择 Chromium 的 Wayland Ozone 后端；原 `--use-angle=gl` 等参数不是可直接照搬的 Wayland 配方。记录实际沙箱、GPU 和渲染/共享缓冲路径，不能用单进程通过多进程验收，也不能把关闭沙箱标为隔离能力已实现。`--disable-gpu` 并非经本版本验证的通用解法。

Weston 缺失已成为明确的补包任务；其版本、seat 管理方式、DRM/pixman 模块、设备枚举和 VT 需求尚待核对。M0-003 准备用户态，M1-004 先复现并打通 Unix STREAM fd/shared mmap，M2-001 验证 Weston 与 wl_shm 客户端。Xorg 的已知 VT/GLX 风险保留在 [内核核查](kernel-capabilities.md) 的原镜像分析中，不整体搬成新路线前置。

## 工作副本中的 Wayland 增量（计划，未实施）

从相同原始 rootfs 派生工作盘，复用 Chromium、musl、字体、libdrm/libinput/Wayland 等已有组件；按 Alpine 3.22/aarch64 实际包依赖补 Weston、对应 DRM/pixman 模块、最小客户端和所需 seat 组件。记录所有包版本、来源、哈希与配置差异，避免整盘发行版替换或无关升级。Linux 对照也使用这一套增量用户态。

会话至少明确运行用户、`XDG_RUNTIME_DIR` 的所有者/权限、`WAYLAND_DISPLAY`、共享内存目录、设备访问与退出清理；这些设置要和 Weston/Chromium 实际连接一致。先验证 guest DRM 真实输出，headless/nested 只提供对应层的诊断证据。完整执行边界见 [M0-003](../tasks/M0-003.md) 和 [M2-001](../tasks/M2-001.md)。

## 镜像欢迎页不是测试页

`/etc/kiosk/url` 是 `file:///usr/share/kiosk/index.html`。镜像该文件标题是 `x-agentos kiosk`，3,024 字节，SHA-256 为 `d34337bee3341cfd1feb775d68dc5c360e862b63d6fe126d2434a01754734f67`。

`testpages.tar.xz` 的原始 `index.html` SHA-256 是 `831cf28f3748940b450d1da325aa39a2dcaea2fee15b0532bf4111144d176b38`。打开默认欢迎页不满足 first-runnable 条件。应在可写工作副本中把三个原始测试页部署到独立目录，例如 `/opt/ict-testpages/`，记录复制步骤及 guest 文件哈希，再指定 `file:///opt/ict-testpages/index.html`；不改原始页、不覆盖原始镜像。

## 内存接口已有实现，但性能读数尚不能直接使用

以下路径均相对固定版 `sources/x-kernel/`；这是静态核查，不是兼容性测试结果。

| 能力 | 实际代码入口 | 结论与下一步 |
| --- | --- | --- |
| memfd 与 sealing | `posix/mm/src/memfd.rs:44` → `fs/filesystems/memfs/src/shmem.rs:303`；`posix/fs/src/fd_ops.rs:161` | 已有真实匿名文件、CLOEXEC、seals，不列为从零开发；测跨进程共享/关闭生命周期和 seal 错误码 |
| mmap/COW/权限 | `posix/mm/src/mmap.rs:469` → `mm/filemap/src/mmap.rs:135` → shared/private runtime；`mmap.rs:575` | 已区分 shared/private、权限与映射策略；用父子进程写入与重新映射验证语义，不能只看 syscall 返回 0 |
| madvise | `posix/mm/src/mmap.rs:389`、`:759` | 当前只接受 `MADV_DONTNEED`；其他 advice 返回 InvalidInput。先捕获浏览器实际 advice 和回退行为，不能断言必阻塞或全实现 |
| mlock / fcntl/flock | `mmap.rs:791`；`posix/fs/src/fd_ops.rs:122`、`:173`、`:181` | mlock2、文件锁及未知 fcntl 有返回成功的占位语义。记录继承风险，按实际调用写最小复现，不扩大“统一成功”策略 |
| `/proc/PID/status` | `fs/filesystems/procfs/src/task_nodes/root.rs:88`、`:599` | 无 VmRSS/VmHWM；任务目录无 statm/smaps 条目，不能预设现成 Linux 采样脚本可用 |
| `/proc/PID/stat` | `process/kprocess/src/stat.rs:133` | vsize/rss 没有赋值，沿 `Default` 为 0；有这些字段不等于有内存计量 |
| getrusage | `core/ksyscall/src/task/rusage.rs:39` | `ru_maxrss` 固定 0，不能用作峰值内存合格证据 |
| `/proc/meminfo` | `fs/filesystems/procfs/src/mem_nodes/root.rs:33` | 部分字段来自 allocator；多项为 0，MemAvailable=free+cache。能作全局分配趋势线索，不等价于浏览器进程树 RSS/PSS |

因此 M1-003 应前置测量定义和数据可信度探针：分配并触碰固定内存、fork/shared 映射、退出回收，检查采样值是否随真实工作量改变，明确定义进程树、共享页去重和峰值采样方法。先建立低干扰时间点与可用的全局趋势记录；尚无可靠浏览器内存度量时写“不可测”，不得用 0 或 host QEMU RSS 冒充 guest 峰值。官方统一主机脚本未提供，决赛 1.5G 的统计对象/单位/采样口径仍待核对。

内存计量尚不完整不能推迟首次可运行 tag。首帧满足时立即存档当时实现、时间证据和计量限制；以后重跑该不可变版本补测时，单独标注补测环境、方法与日期。
