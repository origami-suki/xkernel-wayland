# x-kernel v0.2.0 图形、输入与进程接口审计

审计日期：2026-09-24。源码基线：`c548c427af64bc4fcb7ba9d486d77e006be69b4f`；审计时内核工作区干净。本次只读源码及材料，不运行内核、不修复内核，也没有将已有单元测试视为本项目已通过的验证。

已读项目 roadmap、acceptance、内核 AGENTS、`build-workflow` / `code-guidelines` 技能及审查证据规则。本报告是基线能力调查，不是某个 PR 的 diff 审查；结论来自当前源码调用链。行号以本基线为准，下文路径相对 `sources/x-kernel/`。

路线决定：用户已确定 **采用 Wayland，并复用赛方 rootfs**。以下保留原镜像 Xorg 会话作为材料事实；现行主线是在工作镜像中补齐并固定 Weston 用户态，以 DRM backend + pixman renderer 作为首轮验证组合。本轮只修订规划，没有安装 guest 软件或运行该组合。

## 结论与路线影响

1. **Wayland 主线已确定，但 Weston 用户态尚需补齐。** 镜像实际会话是 Xorg → jwm → Chromium X11，只有 Wayland 库不等于已有 Weston。保留赛方 rootfs 的 Chromium、musl 和其他可复用组件，在工作副本中记录 Weston 版本、包来源、依赖及会话变更；不再以 Xorg 先跑通作为前置。
2. **显示不是从零开始。** 已有 virtio-gpu 2D、DRM legacy/atomic KMS、dumb buffer、mmap、flip event 和 fbdev；首轮工作是把设备、用户态、接口串起来并找具体不兼容。不存在完整 Mesa virtio 3D/PRIME 栈的证据。
3. **Weston 的会话、设备获取和输入发现必须按选定版本验证。** 默认内核 init 不会因 rootfs 带 inittab 就自动进入图形会话；内核缺 Linux VT 接口和 tty1，sysfs 不提供完整 DRM/input/PCI 枚举。libseat/seatd 或其他实际选中的 launcher/backend 是否需要这些接口，要查对应版本和运行日志；不能承诺采用 Wayland 就没有 VT 依赖。
4. **Unix STREAM SCM_RIGHTS 与 shared mmap 是 wl_shm 客户端的前置。** syscall 层能解析 SCM_RIGHTS，DGRAM 能携带，STREAM 会丢弃 ancillary。先做两进程 FD 传递与共享页复现、修复和回归，再验收 wl_shm buffer 的提交、显示和释放。Weston 自身 DRM 启动探测可与这项接口工作并行。
5. **普通 clone/exec/futex/epoll 均有实际实现，沙箱接口明显不完整。** 镜像原脚本未禁用 Chromium 沙箱；Wayland 会话需重新记录 Chromium Ozone 后端、渲染与沙箱参数及实际结果，不能直接沿用 X11/GLX 参数，也不能以 no-op 成功掩盖安全语义。
6. **资源回收、输入空闲开销和可见帧证据要提前。** 现有 DRM mmap backing 有长生命周期保留；输入 poll 依赖主动重查；DRM flip 完成事件本身不能证明屏幕显示成功。它们属于早期观测和针对性回归，不等到最终优化才发现。

## 证据等级

- **已有实现**：已读到实际状态更新、设备提交或等待/唤醒链；仍需 guest 验证。
- **明确限制**：源码可确定的拒绝、no-op、固定值或未携带状态。
- **未确认**：上层程序会选择哪条分支、运行效果和性能没有执行证据。

“已有实现”不等于全部 Linux 语义完整，也不等于目标 rootfs 已跑通。以下任务建议中的修复必须先取得最小复现和 Linux 对照。

## 1. 构建、QEMU 与 PID 1

### 实际入口

- `Makefile:55-77,129-146,166-175`：Make → host Cargo `xkmake` → xconfig/Cargo/QEMU；`make run` 会构建，`make justrun` 消费已有 bundle。
- 实际 defconfig 是 `platforms/kplat-aarch64/qemu_defconfig`，第 18–19 行已启用 virtio GPU/input；第 35 行禁用 virtio MMIO，实际总线配置须以展开后的 `.config` 为准。
- `Kconfig:184-190` 默认 `NR_CPUS=4`；`xtask/xkmake/src/cli.rs:200-209` 默认内存仍是 `1g`。
- `xtask/xkmake/src/qemu.rs:81-88` 通过 PATH 执行 `qemu-system-aarch64`，没有专门版本选择器。主机切换系统 QEMU 11.1.1 后仍要记录 `command -v`、真实路径和 `--version`。
- `qemu.rs:143-159` 非 VMM AArch64 为 `virt,gic-version=3`，TCG CPU 为 `cortex-a76`；`ACCEL=n` 使硬件加速不启用。为可复现性需在项目运行记录中固定 `ACCEL=n MEM=2g SMP=4`，不要继承默认 1 GiB。
- `qemu.rs:475-541`：`GRAPHIC=y` 且 GPU feature 启用才追加 `virtio-gpu-{pci/mmio suffix}`；同时追加 `-vga none -serial mon:stdio`。**该函数没有追加 virtio 键盘/鼠标。** 输入设备需显式添加，并先用系统 QEMU 11.1.1 的 `-device help` 验证名称。
- monitor/serial 分离、`screendump` 和 QMP socket 需在项目启动脚本固定；避免与 `-serial mon:stdio` 叠加出互相争用的 stdio 后端。

### 文档与代码差异

`docs/ai/skills/build-workflow/SKILL.md` 中 `platforms/aarch64-qemu-virt/defconfig` 和 `aarch64-qemu-virt` 平台名不匹配本版实际目录；内核 AGENTS 的 `platforms/kplat-aarch64/qemu_defconfig` 正确。按以下源码支持的入口准备配置：

```sh
cd sources/x-kernel
cp platforms/kplat-aarch64/qemu_defconfig .config
make defconfig
make build
# DISK_IMG 必须指向项目工作副本；输入与监控参数由经过检查的运行脚本追加。
make run ACCEL=n MEM=2g SMP=4 GRAPHIC=y DISK_IMG=/absolute/path/to/working-rootfs.img
```

以上是待执行入口，不是本次成功运行记录；示例最后一行自身尚不具备键鼠和独立截图控制通道。

### 启动脚本接线

`entry/src/main.rs:72,79-95` 将 `entry/src/init.sh` 编入内核，并以 `/bin/sh -c <脚本>` 启动 PID 1。`init.sh:17-35` 仅在 `/sbin/init` 可执行且 `command -v openrc` 成功时转交 init，否则进入 console 登录 shell。

材料脚本见项目 `artifacts/roadmap-audit/rootfs-session.txt`：镜像是 BusyBox inittab → `kiosk-boot` → `x11-session`，没有 OpenRC。**因此 rootfs 含 inittab 不代表 x-kernel 默认会执行它。** M0 启动要分别证明 shell 启动成功、新 Wayland 会话入口执行、正确退出/重启；不能将未执行会话误判为 DRM 故障。在工作镜像中新增可复建的 Weston 会话与运行时目录配置，记录原 X11 自动启动项如何停用；保留原始镜像和历史脚本证据。工作镜像中的会话适配与内核功能修复分别提交。

## 2. 显示调用链

```text
Wayland 客户端 wl_shm buffer
  → Unix STREAM SCM_RIGHTS 传文件 FD + compositor MAP_SHARED
  → Weston pixman 合成（首轮待验证）
  → DRM backend 的 libdrm ioctl / mmap
  → devfs /dev/dri/card0
  → drmdevice::Card0 的 dumb / FB / CRTC 状态
  → display::DisplayDevice 的 scanout resource 接口
  → VirtIoGpuDev
  → RESOURCE_CREATE_2D + ATTACH_BACKING
  → TRANSFER_TO_HOST_2D + SET_SCANOUT + RESOURCE_FLUSH
  → QEMU 显示面与 monitor screendump
```

驱动探测/发布：`drivers/integration/kdriver/src/driver_registry/virtio/mod.rs:234-243`；设备节点：`fs/filesystems/devfs/src/nodes/dri.rs:14-29`，只有 display backend 可用时创建 card0。

| 路径 | 实际状态与证据 | 规划影响 |
| --- | --- | --- |
| virtio-gpu 2D | `drivers/devices/virtio/src/gpu.rs:75-101,179-231` 创建 backing resource，present 依次执行 transfer、set scanout、flush；命令通过 `add_notify_wait_pop` 同步完成 | 先跑独立彩条/双 buffer probe，不先实现驱动 |
| dumb buffer | `io/drmdevice/src/card0.rs:802-883` 校验尺寸、32 bpp、flags，分配连续物理页，创建 scanout resource，返回 handle/offset；`635-648` 真正映射 backing | 检验 pitch/size、写后刷新、两色交替，而非只测 ioctl=0 |
| dumb 约束 | `consts.rs:152-159` 单 buffer 最大 8 MiB；`card0.rs:809-831` 仅 32 bpp、连续物理分配 | 固定首轮分辨率并记录；高分辨率/碎片化属于有依据的后续用例 |
| legacy KMS | `card0.rs:688-726,787-798,1144-1196` 有 SETCRTC、PAGE_FLIP、ADDFB2；FB 必须与资源宽高/pitch 一致，XRGB8888/ARGB8888 单 plane | 作为 Weston DRM backend 的候选设备路径验证；实际使用 legacy 或 atomic 由所选版本、探测结果及配置记录确定 |
| atomic KMS | `card0.rs:1025-1083,1256-1329` 接受并保存属性，TEST_ONLY、NONBLOCK、PAGE_FLIP_EVENT；最终只将选中 FB 整幅 present | 不等于通用 atomic compositor 支持；裁剪/缩放/多 plane 未见设备应用链 |
| flip/read/poll | `card0.rs:484-505,669-683,1224-1253` 有事件队列、序号、用户数据、poll 唤醒；空队列返回 WouldBlock | 针对 event user_data、重复 flip、非阻塞读、队列清空做回归 |
| fbdev | `fs/filesystems/devfs/src/nodes/fb.rs:137-148,151-257` 暴露 shadow mmap；仅 write 和 FBIOPAN_DISPLAY 调 `fb_present` | 单纯 mmap 写不自动刷新；保留为已有能力说明，不作为当前 Weston DRM 主线的替代验收 |
| 3D/render node | GPU 协议仅 2D scanout；devfs 只注册 card0，driver name 为 `simpledrm`（`consts.rs:36`） | 不得把有 virtio-gpu 推导为 VirGL、完整 Mesa DRM driver 或 renderD128 可用；先验证 Weston pixman，再独立确认 Chromium Wayland 渲染/提交路径 |

### 明确简化与风险

- **优先复现 DRM 版本/唯一名的两阶段查询**：`card0.rs:123-145,165-175` 在写回实际长度后，无条件将完整字符串写到 name/date/desc/unique 指针，没有按输入缓冲容量截断，也没有跳过 null 指针。`posix/types/src/ptr.rs:221-228` 确实向用户地址复制，`core/kuaccess/src/lib.rs:126-134,290-295` 对非法地址报错。因此“长度 0、指针 NULL，仅查询所需长度”的具体输入会返回 BadAddress，短缓冲输入也未获容量保护。先用独立 ioctl probe 和 rootfs libdrm 复现；不能越过这一关就假设 Weston 会走到 CREATE_DUMB。
- `card0.rs:194-216,508-510`：SET_CLIENT_CAP、AUTH_MAGIC、SET/DROP_MASTER 返回成功，但不保存对应 capability/master/auth 状态；GET_MAGIC 固定 1。首版单显示会话可先验证，不能宣称完整 DRM 多客户端权限模型。
- `card0.rs:233-247`：PRIME handle→fd 直接 `fd = handle as i32`，fd→handle 直接数字转换，未分配/查验共享 dma-buf 文件。返回成功不代表 FD 可用。当前先验证 wl_shm + pixman；后续 Chromium 或 compositor 实际选择 dma-buf/PRIME 时须先独立复现，不能在上层强行当作有效 FD，也不能宣称所有 Wayland 客户端都只用 wl_shm。
- `card0.rs:1201-1221`：`present_fb` 不返回错误；设备不可用直接返回，设备提交错误只打 warn。PAGE_FLIP 后仍可能排入完成事件。因此 ioctl=0 和 flip event 都不能代替 screendump。
- `card0.rs:989-1021`：WAIT_VBLANK 是按 60 Hz 时长 sleep 后增序号，未读取硬件 vblank；首帧/帧时延测量要标明其含义。
- `card0.rs:372,396,635-648,887-906`：mmap 把 backing `Arc<GlobalPage>` 存入 `retained_pages`，全文件没有删除该表项的路径；DESTROY_DUMB 只删除 dumb 和 host resource。Card0 在 devfs 建树时一次创建，非每次 open 新建。已有静态证据显示“映射过的 buffer 保留”，应安排 create/map/unmap/destroy 循环验证回收，再设计正确 VMA 生命周期；不能直接删 Arc 造成悬空 mmap。
- GPU 的三个同步 host round trip 是现有行为。先保留功能基线、计时和 CPU 占用，之后才决定是否值得改队列/提交方式。

## 3. Weston DRM/pixman 的会话、设备与输入前置

首轮采用 Weston DRM backend + pixman renderer。Weston 版本、包拆分、可用 backend/renderer、实际 launcher 和命令参数尚未核定；先在赛方 rootfs 工作副本中清点并固定，按该版本的本地帮助、配置和必要文档确认启动方式。pixman 是 compositor 合成端的选择，不能据此保证 Chromium 的 EGL/ANGLE、GPU 进程或 buffer 协议已经兼容。

| 接口 | 代码证据 / 已知状态 | 当前验证边界 |
| --- | --- | --- |
| Weston 用户态 | 原镜像没有 Weston，现有会话脚本为 X11 | 补齐包和递归依赖，核对 AArch64/musl；固定 DRM/pixman、libinput 和实际会话管理组件，不能只安装同名启动程序 |
| console/TTY | `fs/filesystems/devfs/src/nodes/tty_nodes.rs:13-40` 只注册 tty、console、ptmx、pts | 没有 tty0/tty1/ttyN；所选 Weston launcher 或 libseat/seatd 后端是否请求这些节点必须验证 |
| VT/KD ioctl | `io/ktty/src/tty/mod.rs:239-347` 是 termios、winsize、job control、PTY 集合，未匹配项返回 NotATty | VT_GETSTATE/VT_ACTIVATE/KDSETMODE 等不在处理集合；不可假设 Wayland、root 启动或 seatd 自动消除此依赖 |
| sysfs / libudev | `fs/filesystems/memfs/src/lib.rs:94-105` 用 ramfs 建 sysfs；`fs/boot/src/lib.rs:317-327` 只见 fb0/device/subsystem → `whatever` 的图形补位链接 | 实测 Weston/libinput 如何找到 DRM card 与输入设备；所需属性、枚举及热插拔支持按实际路径补齐 |
| seat 与设备权限 | DRM SET/DROP_MASTER 是成功 no-op；输入 grab 也是 no-op，详见本报告对应条目 | 记录设备打开、授权、激活/失活及 FD 移交所需语义；不能把 root 可打开节点等同于 seat 协议已支持 |
| Wayland socket / runtime | Unix STREAM 有 bind/listen/accept、字节收发和 poll；ancillary 缺口见第 5 节 | 验证运行时目录属主/权限、socket 建立及客户端连接；wl_shm 前必须通过 STREAM FD 传递 + shared mmap |
| pixman / DRM | 已有 dumb buffer、legacy/atomic KMS 与 2D scanout | 验证软件合成结果、实际 KMS 分支、frame callback 与 buffer release；保留 screendump，不能仅以 Weston 进程存活为通过 |
| Chromium Ozone Wayland | 原浏览器 ELF 可复用，但镜像脚本固定 X11 参数 | 核对本包的 Wayland 能力；逐项记录后端、buffer 协议、渲染与沙箱参数，保留 GPU 进程重启/失败日志 |

实施阶梯为：用户态版本与依赖固定 → seat/DRM 枚举及 Weston 输出启动 → 已通过 FD/shared mmap 用例的 wl_shm 最小客户端；随后并行推进真实键鼠验收和 Chromium Wayland 首帧。Weston 输出启动与 IPC 最小用例可并行；它们汇合后才能证明完整客户端显示链。先补实际用到的状态、错误返回或有依据的会话配置，不对 VT/设备 ioctl 统一返回成功。

### 保留的 X11 镜像事实，不作为当前主线任务

原 `x11-session` 运行 `Xorg :0 vt1 -nolisten tcp -auth ...`，没有额外 xorg.conf；Chromium 参数包含 `--ozone-platform=x11 --use-gl=angle --use-angle=gl --disable-vulkan --disable-features=Vulkan`，脚本注释声明 GLX/llvmpipe。上述配置是历史材料证据，不是新的 Wayland 启动模板。Xorg modesetting、GLX、DRI3 和最小 X 客户端无需先跑通。

现有 SysV SHM 仍是可复用接口：`posix/ipc/src/shm.rs:420-463,476-563,575-665` 有 shmem backing、共享 filemap、IPC_RMID/最后 detach 回收；同时 `shmget` 既存对象要求精确 size/mode，`shmat` 的 SHM_REMAP 仅声明，`shmctl` 只支持 IPC_SET/IPC_STAT/IPC_RMID。它不替代 Wayland wl_shm 所需的 FD 传递与映射，也不因原 X11 会话可能使用 MIT-SHM 就成为本轮必须修复的全部范围。

## 4. 键鼠链与发现机制

```text
QEMU virtio 键盘/相对鼠标
  → PCI virtio probe
  → VirtIoInputDev::read_event / capability 查询
  → inputdev 注册表
  → devfs EventDev::read / ioctl / poll
  → Weston 所选版本的 libinput / seat 输入路径
  → Wayland seat、焦点与客户端事件
  → 焦点、键盘布局、指针事件
  → Chromium DOM 的真实输入反馈
```

- `drivers/devices/virtio/src/input.rs:59-82,109-121` 确有设备初始化、EvBits 查询和 `pop_pending_event`；空队列返回 WouldBlock。
- `io/inputdev/src/lib.rs:23-32,41-58,73-75` 管理已探测设备，devfs 建树时 drain；运行时新设备能否形成新节点尚未验证。
- `fs/filesystems/devfs/src/nodes/event.rs:192-223` 返回 Linux 风格 InputEvent，维持按键状态；第 233–247 行提供版本和 ID，后续分支查询名字、能力、按键。
- **设备名风险**：`event.rs:374-395` 根据 BTN_MOUSE 把鼠标注册为 `/dev/input/mice`，该节点数据仍为 InputEvent；非鼠标才注册 eventN。不能把 mice 名称视为 PS/2 协议兼容，也不能假设扫描 eventN 的输入驱动能发现鼠标。
- **绝对坐标缺口**：`event.rs:323-326` 对 EVIOCGABS 范围的查询直接 `Ok(0)`，不填 absinfo；第 293–297 行 EVIOCGPROP 同样直接 0。先用相对鼠标缩小首版范围，tablet 需要独立能力验证。
- `event.rs:249` EVIOCGRAB 是成功 no-op，没有独占状态。
- **等待机制限制**：`event.rs:359-368` 注册 poll 后主动 `wake_by_ref`，注释明确没有 callback wake source；`drivers/devices/virtio/src/pci.rs:267-269` 对 Input 置 INTERRUPT_DISABLE。因此需要测无输入时的 CPU 与睡眠行为；不能把 poll 存在等同于事件驱动低空闲开销。

验收必须从 QEMU 输入面注入并观察 evdev → 窗口 → 页面，分别覆盖按下/抬起、输入框字符、鼠标移动/点击、焦点和快捷键。JS `dispatchEvent` 不能证明这条链。

## 5. Unix FD 传递与 poll

### 已确认调用链

`core/ksyscall/src/dispatch.rs:711,750-751` → `posix/net/src/socket.rs:229-272` / `io.rs:197-216,369-396` → `cmsg.rs:134-172` → `knet` 对应 transport。

- `cmsg.rs:153-166` 对 SCM_RIGHTS 查 fd table，保存 `Arc<VfsFile>`，不是仅传原进程 fd 数字。
- `io.rs:115-140` 接收 ancillary 时向接收者 fd table 安装文件；此处 `add_file(f, false)`，close-on-exec 等边界仍须测试。
- **Unix STREAM 缺口**：`net/knet/src/unix/stream.rs:224-340` send/recv 只处理 byte ring、flags、等待，未消费/产生 `options.ancillary`；`stream/channel.rs:103-109` 也没有 ancillary/FD 队列。sendmsg 可以返回写入字节数，但接收端收不到 FD。
- **Unix DGRAM**：`net/knet/src/unix/dgram.rs:196-204,228-257` 确实将 ancillary 随 packet 入队并在 recv 取出。`socketpair` 的 SOCK_SEQPACKET 分支也直接使用 DgramTransport（`posix/net/src/socket.rs:247-250`），不是独立 seqpacket 完整实现。
- **凭据范围**：`cmsg.rs:168-169` 不识别 SCM_CREDENTIALS；`stream.rs:88-110` PassCredentials 是 no-op，PeerCredentials 有返回路径。不要笼统写 Unix credentials 已完整实现。
- STREAM 有可读/可写/关闭唤醒：`stream.rs:278-285,315-339` 和 `stream/channel.rs:30-64,111-132`。这些应保留并回归，不能修 FD 传递时退化回忙轮询。

最低验证用例是两进程 `socketpair(AF_UNIX, SOCK_STREAM)`，发送一个共享文件 FD 与标记字节，接收侧确认收到安装在自身 fd 表中的有效 fd、映射相同内容（两个进程的 fd 数字可以相同，不以数字不同作为条件），关闭发送侧 fd 后仍可使用；再测短读/分段、多个 FD、控制缓冲不足、接收侧退出和资源回收。DGRAM 与 STREAM 分开报告。**采用 Wayland 后，此项与 shared mmap 是 wl_shm 最小客户端的明确前置**；随后验证 pool/buffer 创建、内容更新、frame callback、buffer release 及退出回收。不能因为 Weston 空桌面已出现而跳过客户端 FD 路径。

## 6. Chromium 进程与同步

| 接口 | 实际实现 / 限制 | 验证任务 |
| --- | --- | --- |
| clone/clone3 | `core/ksyscall/src/task/clone.rs:203-284` 分线程/进程创建、VM/FS/files/signal 共享、TLS、clear-child-tid；`clone3.rs:39-87` 转到共同 CloneRequest | 同 rootfs libc 的 pthread、fork→exec→wait、FD 继承；不以单进程浏览器代替多进程验收 |
| vfork | `clone.rs:181-188` 遇 VFORK 删除 VM flag，使用私有 mm；没有 Linux 的共享 mm 与阻塞父进程行为 | 针对实际 posix_spawn/zygote 路线复现；记录是已有 fallback，不宣称完整 vfork |
| exec | `task/execve.rs:24-137` 解析 argv/env、查文件、加载、重设上下文；`process/kexec/src/loader.rs:390-459,520-590` 处理解释器、ELF、auxv 和脚本 | AArch64 动态 ELF 和子进程拉起；多线程进程直接 exec 在 `execve.rs:58-61` 返回 WouldBlock |
| futex | `sync/futex.rs:126-199` WAIT/WAKE/BITSET/REQUEUE/CMP_REQUEUE/WAKE_OP；`process/kfutex/src/table.rs:73-136,451-493` bucket 锁内复检、入队、超时/信号取消与唤醒竞态处理 | libc mutex/condvar、跨进程 shared futex、超时/信号/退出；未匹配命令 Unsupported |
| epoll | `io_mpx/epoll.rs:29-159` create/ctl/pwait/pwait2；`process/kfd_objects/src/epoll.rs:275-335,508-582,799-886` 有 LT/ET/ONESHOT、注册、ready queue、重置 | socket/pipe/eventfd/timerfd 的真实组合，ready 后 drain/rearm、close 和超时 |
| seccomp | `sys.rs:325-326` 返回 ENOSYS；`task/ctl.rs:109-124` PR_SET_SECCOMP 成功 no-op、GET=0、SET_NO_NEW_PRIVS 合法参数也 ENOSYS | 记录 Wayland 会话中 Chromium 的实际沙箱配置与启动结果；无完整沙箱的证据 |
| namespaces | `clone.rs:367-379` NEWNET/NEWUSER/NEWCGROUP/NEWTIME/NEWPID 返回 ENOSYS | 不能将 namespace sandbox 当作可用；按实际调用分解必要接口 |
| prctl 其他 | `task/ctl.rs:154-179` dumpable/subreaper/THP/timerslack 多为固定值或 no-op | 若 Chromium 命中并依赖副作用再补最小测试，避免按接口名批量实现 |
| membarrier | `sync/membarrier.rs:31-42` QUERY 返回掩码，其他 cmd 只执行 compiler_fence 后成功 | 4 vCPU 环境下不能据返回成功推导跨 CPU 屏障已落实；追踪实际 Chromium/V8 调用后确定优先级 |
| 未分发调用 | `dispatch.rs:831-856` rseq 和其他未处理 syscall 返回 Unsupported；当前未见 unshare/setns/execveat 对应分支 | 保留 errno 与触发上下文，不统一返回 0；rootfs 可否降级要实际验证 |

原镜像 X11 会话降低到 kiosk 用户并保留沙箱默认值。新 Wayland 会话应记录 Weston 和 Chromium 的实际用户、设备权限、Ozone 后端与沙箱配置；**若为首帧使用限定的禁沙箱运行配置，仍须标明这是运行配置，不能声称内核已兼容沙箱**。保留实际失败日志、完整参数和适用范围；最终多进程、键鼠、页面功能验收继续执行。

内存 mmap/memfd/seals/测量字段的细化由同轮 rootfs 与内存审计覆盖；本报告没有重复把“有 syscall 名”升级为通过。

## 7. 建议拆成可独立验收的任务

| 顺序 | 任务边界 | 验收 / 证据 | 学习实验 |
| --- | --- | --- | --- |
| P0 / M0 | 实际构建链、系统 QEMU 11.1.1、工作镜像、PID 1 与 Wayland 会话接线 | 源码/配置哈希；TCG 2 GiB/4 CPU 的完整命令；console 启停、新会话入口执行日志 | 比较内核 CMDLINE、编入的 init.sh 与镜像 inittab，说明哪个进程实际是 PID 1 |
| P0 / M0 | 基于赛方 rootfs 补齐并固定 Weston DRM/pixman 用户态 | 包来源、版本、依赖、构建能力与实际 launcher；新会话的用户、runtime 目录、原 X11 自启动停用记录 | 列出 Weston、libinput、libseat/seatd 的实际角色，区分协议、renderer 与会话管理 |
| P0 / M1 | 相同 Wayland 工作镜像的 Linux 对照与错误捕获 | 同版本 Weston/Chromium Wayland 命令、Linux 成功/失败结果和分层日志；x-kernel 首个错误可定位 | 保持工作镜像与参数不变，只换内核，对比设备获取/DRM 第一个分歧 |
| P0 / M1，wl_shm 前置 | Unix STREAM FD / 共享缓冲 | 两进程 SCM_RIGHTS + shared mmap 的 Linux/x-kernel 对照；短读、close、泄漏回归 | 接收 FD 后关闭发送者原 fd，解释 open file description 的生命周期 |
| P0 / M2-a | DRM 查询、原语彩条与 flip | VERSION/UNIQUE 的空缓冲与分配后查询；create/map/ADDFB2/SETCRTC/flip；两帧不同的 monitor screendump；errno/事件数据 | 修改 dumb 像素后分别不提交/提交，观察“写内存”与“显示”之间的接口 |
| P0 / M2-b | Weston DRM/pixman 输出与 wl_shm 最小客户端 | 按版本验证 seat/VT/设备发现；客户端 buffer 更新可见，frame/release 事件和连续 10 分钟会话记录 | 对比客户端共享页、Weston 合成 buffer 与 monitor 图像，定位每层职责 |
| P0 / M2-c | 键鼠设备与 Wayland 客户端输入 | QEMU 设备、evdev/libinput 能力、按下/抬起/鼠标 event、seat 焦点和客户端真实回显 | 从 QEMU 注入一次点击，分别观察 evdev、Wayland 客户端事件、网页计数 |
| P0 / M3-a | Chromium Ozone Wayland 的渲染与沙箱路径 | 复用镜像浏览器；核对 Wayland 能力、实际 buffer 协议、渲染/context 与 GPU 进程日志，记录参数和限制 | 对照 Chromium browser/zygote/renderer/GPU 进程角色与实际 IPC；解释 compositor pixman 不等于浏览器后端 |
| P0 / M3-b | 原始 index.html 首个可见窗口 | monitor screendump、代码/命令/日志立即建立不可移动 baseline/first-runnable | 页面更新前后观察 renderer 与 GPU/显示会话参与关系 |
| P1 / M2-M4 | 显示与 IPC 资源回收 | 100 次 create/map/unmap/destroy 与窗口启动退出，内核内存/FD/resource 趋势 | 关闭窗口后检查哪些对象由句柄、mmap 或 devfs 持有 |
| P1 / M4 | 多进程同步、稳定性和输入空闲 | pthread/futex/epoll、30 分钟与加载循环；输入空闲 CPU 的原始数据 | 同样输入频率对比空闲和活动状态，解释 poll 自唤醒的代价 |

P0 表示当前主线应先验证的依赖，不表示静态报告已证明每项都会运行失败。Wayland 与复用赛方 rootfs 已确定，无需重复设置路线批准门；具体包版本和支持参数在实施时核定。VT/seat/sysfs 的必要修复由所选版本的实际路径和复现决定，Xorg 特有工作不在当前主线；没有命中的历史兼容性债务不应无边界扩成全部 Linux ABI 实现。首次满足原始 index.html 可见条件仍须立即存档，不等 10 分钟或完整输入回归完成。

## 8. 本次完成与未完成

已完成：核对固定提交；阅读构建/QEMU/启动入口、DRM→virtio 设备链、fbdev、TTY、sysfs、输入、Unix stream/dgram 与 cmsg、clone/exec/futex/epoll/sandbox 的关键实现；保留实际 rootfs 会话事实，并按用户确定的 Wayland 路线重排依赖。

未执行：guest Weston 安装与版本/启动参数核定、内核构建、QEMU 启动、Linux 对照、Weston/Chromium Wayland、输入注入、内核单元测试和动态性能测量。原 Xorg 会话同样未运行。所有运行验收保持未验证。报告中列出的 stub / no-op / 丢弃状态是当前源码事实；对上层应用影响的条件和未确认部分已分别标明。
