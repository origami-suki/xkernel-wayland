# 推进路线

2026-09-25：**原生 Wayland Chromium 已正确显示原始 index.html，并立即建立不可变 `baseline/first-runnable`**（集成 `6cf0331`、内核 `31c8f270`）。关键参数为 `--ipc-connection-timeout=120`；默认 15 秒会触发已观察到的子进程连接超时。F_DUPFD 和未知 fcntl 已修复并通过同盘对照，M0/M1/M2 既有验证保留。仍使用 no-sandbox，真实输入、页面自检、10/30 分钟稳定性、正常退出和性能计量待验证。见 [首个可运行记录](first-runnable.md)。

2026-09-25 上游 ELF 替换已收尾：!797 独立适配引入为内核 `ca59027c`，串口退出同步修复为集成 `8561f5e`；独立六轮对照/回归及主工作区 ELF、M1/M2、Weston 三轮正常关机通过。无效 ELF 的 errno/信号差异及旧 fixture 归因纠正见 [M1-002](tasks/M1-002.md)，该段记录首帧成功前的历史状态，当前结果见上方。

**用户已确定：采用 Wayland，复用赛方 rootfs。** 先前调查中提出的 X11 优先建议已撤回；Xorg/JWM 仅作为原镜像内容记录。后续补齐 Weston 和必要依赖、建立原生 Wayland 会话，不再安排路线比较或等待这项决策的批准。

日常先读本文件及当前任务卡，需要依据时再读专题调查：

- [赛题、评分与三份测试页](analysis/requirements-and-testpages.md)：硬要求、分值、页面自检边界。
- [rootfs、启动衔接与内存观测](analysis/rootfs-and-memory.md)：镜像事实、启动链、测量占位字段。
- [内核能力与缺口](analysis/kernel-capabilities.md)：代码入口、实际语义与最小验证建议。
- [版本与存档规则](baseline.md)、[验收矩阵](acceptance.md)：参数、first-runnable 和逐项证据。

## 修订所依据的现状

| 文件/静态代码事实 | 对推进顺序的影响 |
| --- | --- |
| 赛方是 Alpine + musl，带 Chromium 142、Mesa、Wayland 运行库、Xorg/JWM；无 Weston | 保留现有 rootfs 和浏览器，在可写副本补装 Weston/必要依赖；先验证 Chromium 的原生 Wayland/Ozone 能力 |
| 内核 PID 1 执行 `entry/src/init.sh`；镜像缺它用于切入 init 的 openrc 条件 | 先证明串口 shell、ELF loader、挂载，再衔接会话，不能期待 inittab 自动运行 |
| 默认 kiosk 欢迎页与 testpages/index.html 不同 | 从第一次浏览器试跑就部署、核对原始三页及 URL，防止误存档 |
| 已有 DRM legacy/atomic、dumb buffer/mmap、virtio-gpu 2D、memfd/seals、mmap/COW、futex/epoll | 不预排重写这些模块；按真实失败缩成独立修复任务 |
| DRM 查询、sysfs/鼠标节点、stream ancillary、PRIME 等存在具体风险或缺口；VT/KD 未完整实现 | 将 Unix STREAM fd 传递和 shared mmap 提升为 wl_shm 客户端前置；Weston 的 seat/DRM/输入路径独立验证，VT 需求按实际启动后端定位 |
| procfs RSS/峰值和 getrusage 有缺失或固定 0；DRM backing 回收存在静态风险 | 从 M0/M1 开始准备观测并校准读数；生命周期回归提前，不能拿占位值评分 |
| 赛题允许不同路线/启动参数；原始测试页离线可用 | 网络、3D、完整沙箱不作为首个静态页的默认前置 |

这些是已找到的能力边界与风险，**不等同于 Chromium 已触发的故障或可计分的缺口复现**。详情、代码位置与核查范围见调查报告。

## 路线与依赖

固定 x-kernel `v0.2.0` 起点，不主动同步上游；开发采用 AArch64、纯 TCG、2 GiB、4 vCPU。用户所指“系统 11.0”经核对实际为 **11.1.1**，使用 `/usr/bin/qemu-system-aarch64`；5.2.0 卸载与新版模块验证见 [M0-005](tasks/M0-005.md)。后续测量锁定实际版本和设备清单。

```text
M0-002 构建/串口启动 ─┬─ M0-003 复用 rootfs、补齐 Weston、部署原页
                     └─ M1-001 相同 Wayland 用户态的 Linux 对照
                                   │
                   按失败触发 M1-002 独立接口修复
                                   │
            M1-004 STREAM fd + shared mmap ─┐
                                          ↓
                 M2-001 Weston DRM/pixman + wl_shm 客户端
                          ├─ M2-002 输入 + 10 分钟稳定性
                          └─ M3-001 原生 Wayland Chromium + 原始 index.html
                                      └─ 首次成功立即 first-runnable 存档
                                      └─ M4 功能、30 分钟、循环与回收
                                                  └─ M5 有数据的优化
M1-003 计量定义/校准 ───────────────→ 首帧起采集 → M5 比较
证据/通用补丁/学习记录 ─────────────────────────→ M6 交付
```

完整输入、10 分钟验收及尚缺的计量字段均不得阻止首次成功存档。M1 是按问题展开的诊断/修复工作，不是先补齐 Linux 全部接口才允许试跑应用。

若后续兼容性诊断中的桥接链先满足既定首次可运行条件，也立即保存最早版本并注明实际链路；不能用它宣称原生 Wayland 已通过，也不能为了等待更理想的路线推迟或覆盖首版。

确定的显示链为 **现有 Chromium 原生 Wayland/Ozone → Weston → DRM/KMS → virtio-gpu → QEMU monitor screendump**。首轮优先验证 Weston 的 DRM 后端与 pixman 软件合成，具体模块、选项和 seat 管理方式按选定包版本/内置帮助核对，尚未认定可运行。Weston 用 pixman 不等于 Chromium 无 GPU/图形缓冲依赖，浏览器的软件绘制、EGL/ANGLE 或 dma-buf 协商要另外验证。

“复用 rootfs”指从同一赛方压缩包生成工作副本，保留现有 Chromium、musl、字体和可用运行库，只补齐 Wayland 会话所需组件与配置。记录新增/变更包的版本、来源、哈希、依赖求解结果、会话脚本和原页复制步骤；不重建另一份发行版镜像，不批量升级现有用户态。Xorg/JWM 可以留在盘中，无需为精简镜像而删除，但主线不启动原 `x11-session`。

Linux 对照使用同一补齐后的 Wayland 用户态与参数。headless/nested 后端可帮助定位加载和协议问题，却不满足 guest DRM 显示验收。优先验证 Chromium 原生 Wayland；若现有二进制存在能力限制，须先给出证据再评估包括 Xwayland 桥接在内的兼容办法，不能把桥接链路标为原生 Wayland。M0-003 已锁定 Weston 14.0.2、seatd/libseat 0.9.1，新增 16 个验签包且没有升级原包；原生 Wayland/Ozone 静态证据齐全，真正协议连接仍待 M3。具体重建和动态结果见 [Wayland 用户态记录](analysis/wayland-rootfs.md)。

## 阶段目标与退出条件

| 阶段 | 目标和退出条件 | 当前状态 |
| --- | --- | --- |
| M0 材料与可重复启动 | 输入锁定、工作副本可重建；正确工具链构建；同一配置两次到达交互 shell并正常结束；Wayland 补包清单和会话启动入口有证据 | 工程检查与证据已完成，阶段验收待用户确认 |
| M1 诊断、兼容性与计量 | 每个实际阻塞有阶段日志、最小复现、Linux 结果、errno/超时定位与回归；计量入口区分可信值和缺项 | ELF、STREAM fd、memfd seal、no_new_privs、调度TID查询、发送者凭据与proc task链接数已修复并独立验证；计量未实现，其余缺口按实际失败触发 |
| M2 图形与输入 | Weston DRM 会话和 wl_shm 客户端可见；buffer 内容可改变；键鼠经过虚拟设备到客户端；连续 10 分钟，非重启后拼接 | M2-001 显示与正常退出通过；真实键鼠/10分钟和资源循环待验证 |
| M3 首个可运行 Chromium | 目标是原生 Wayland 窗口正确显示原始 index.html；首次实际可运行就保存命令、screendump、日志、代码与不可变 tag，记录真实链路 | 原始 index 首帧已通过；baseline/first-runnable 已建立，IPC 期限为120秒；输入/稳定性/正常退出待验证 |
| M4 功能与稳定性 | renderer 等多进程；两个可切换页面/窗口；真实键鼠；JS/CSS 各 6 项及视觉核对；30 分钟无崩溃/OOM、每 2 分钟加载共 10 次无卡死 | 未开始 |
| M5 数据驱动优化 | 对比最早可运行 tag；每项假设有原始 before/after，各不少于 5 次、中位数与范围，并通过相同功能/稳定性回归 | 未开始，测量入口前置 |
| M6 交付与答辩 | 干净环境复现；初赛材料与决赛现场步骤分开核对；评分项对应证据；官方平台/脚本发布后迁移复测 | 未开始，文档与补丁持续积累 |

## 当前可执行任务

| 任务 | 目标/范围 | 依赖与最小验收 | 状态 |
| --- | --- | --- | --- |
| [M0-001](tasks/M0-001.md) | 项目约定、输入和版本锁定 | 哈希、submodule、提交检查正反例与 hooks，不声称功能完成 | 见任务卡 |
| [M0-004](tasks/M0-004.md) | 赛题/rootfs/源码调查与路线修订 | 结论指向原文、guest 文件或源码；计划/验证分离；文档链接检查 | 静态调查已验证 |
| [M0-005](tasks/M0-005.md) | 清除旧 QEMU、使用系统新版 | 来源/卸载清单、PATH、版本、TCG 与图形模块 smoke | 已验证，未启动 guest |
| [M0-002](tasks/M0-002.md) | 工具链、构建、工作盘与串口启动 | M0-001/005；固定配置两次启动、串口与 monitor、命令/退出日志 | 已验证：两次 shell/关机及 monitor 停止 |
| [M0-003](tasks/M0-003.md) | rootfs 复用与 Wayland 用户态补齐 | 补包/依赖清单可先离线准备，运行探测依赖 M0-002；Weston/Chromium 能力、会话配置、原页哈希与首个阻塞 | 可重建增量、版本/设备/原页已验证；浏览器装载阻塞已由 M1-002 修复 |
| [M1-001](tasks/M1-001.md) | Linux 对照与诊断入口 | 与 M0-003 可并行；同一补齐后的 rootfs/Wayland 包及参数；一个通过、一个失败/超时最小用例均有退出码/分层日志 | 最小 system 对照已验证；图形会话对照随 M2/M3 继续 |
| [M1-002](tasks/M1-002.md) | 首个阻塞：大型 PIE 与固定 ELF 解释器地址冲突 | 原盘/补齐盘均复现；最小 ELF 与 Linux 对照；解释器基址动态选择与非法 ELF 错误传播 | **已修复并回归通过**：原 Chromium `--version`、80 MiB BSS PIE、畸形 ELF errno、BusyBox/Weston/seatd 与重复启动均通过 |
| [M1-004](tasks/M1-004.md) | Wayland 的 STREAM fd/共享缓冲区 | M0-002 后做最小复现；SCM_RIGHTS + mmap + 生命周期，Linux 对照；M2 wl_shm 客户端的必要前置 | 接口验证通过：STREAM fd/共享映射/生命周期与 memfd seals 同盘 20 项通过，独立测试见任务卡 |
| [M1-003](tasks/M1-003.md) | 时间点、进程树与可信内存计量 | 随启动入口准备；首帧前接入可测部分，固定 0 字段不能算通过 | 计划 |
| [M2-001](tasks/M2-001.md) | Weston DRM/pixman 与动态 wl_shm 客户端 | M1-004；真实 monitor 图像变化、frame/release、正常退出 | **独立验收及干净复跑通过**；非 VT seat、暂允许无输入 |
| [M1-005](tasks/M1-005.md) | Chromium子进程的no_new_privs | M3实际失败；同盘ABI/线程/fork/exec/setid探针及独立回归 | 已修复：同盘五类NNP对照、130单测及M1/M2独立回归通过 |
| [M1-006](tasks/M1-006.md) | 活跃TID的调度参数查询 | M3日志及同盘动态/静态probe；查询语义与独立回归 | 已修复：动态/静态各33项、107单测及M1/M2独立回归通过 |
| [M1-007](tasks/M1-007.md) | Unix socket发送者凭据与Zygote PID握手 | 同盘SO_PASSCRED/SCM_CREDENTIALS正常路径probe及Chromium源码链 | 已修复：236项同盘凭据对照、256项单测及独立M1/M2回归通过 |
| [M1-009](tasks/M1-009.md) | proc task动态链接数与Zygote单线程检查 | 线程生命周期Linux同盘对照 | 已修复；动态/静态各76项、259项内核单测通过，Zygote状态回复成功，独立验收与干净复跑通过 |
| [M1-010](tasks/M1-010.md) | AArch64用户态缓存同步与GPU SIGILL | 实际异常指令、同盘Linux对照与每CPU探针 | 已修复：动态/静态各41项、18项内核单测及M1/M2回归通过；原页仍空白 |
| [M1-008](tasks/M1-008.md) | F_DUPFD最小编号与非法范围 | 同盘动态/静态各71项及13项内核单测 | 已修复，M1/M2及Weston回归通过；正文仍空白 |
| [M1-011](tasks/M1-011.md) | 未知fcntl命令的错误返回 | 同盘动态/静态各20项及F_DUPFD回归 | 已修复；browser发起原页加载，子进程IPC连接超时待定位 |
| [M3-001](tasks/M3-001.md) | Chromium 原生 Wayland 首帧与不可变基线 | M2-001 已满足；正确显示原始 index 后立即存档 | 原始 index 首帧已通过；baseline/first-runnable 已建立，IPC 期限为120秒；输入/稳定性/正常退出待验证 |

M0-002 不通过下载另一份 rootfs 或切换内核版本绕过构建/挂载问题。镜像中自带 Linux 只用于行为对照，不能作为 x-kernel 功能证据。

## 代码与应用之间的验证阶梯

以下是任务候选与停止条件，不要求全部实现。只有实际路径需要或已有可靠复现，才提升为实现任务。

| 关卡/候选任务 | 最小验证 | 失败时的入口与边界 |
| --- | --- | --- |
| M1-002a 进程与同步 | exec/clone 创建及 wait 回收；futex 唤醒/超时；epoll + eventfd/timerfd；browser/renderer 角色日志 | `core/ksyscall/src/task/`、`core/ksyscall/src/sync/`、`core/ksyscall/src/io_mpx/`、`process/kfd_objects/`；先定位退出/阻塞，不能用 single-process 达到多进程验收 |
| M1-002b 旧 ELF 样本归因纠正 | `bad-filesz` 实际是 `p_memsz=1 < p_filesz=2404` 的无效 ELF；旧 filemap/COW 缺陷推断撤回。当前上游适配以 ENOEXEC 提前拒绝，与 Linux 的 SIGSEGV 差异明确保留 | 见 [M1-002](tasks/M1-002.md) 三方同盘对照；`p_filesz < p_memsz` 可为正常 BSS，不能单凭该关系建立缺陷任务 |
| M1-004 共享内存与 fd | memfd→truncate→父子 MAP_SHARED；seals/CLOEXEC；Unix STREAM 传 fd 后 mmap；坏 fd/ancillary 截断/关闭回收；实际 wl_shm 集成交 M2-001 | `posix/mm`、`mm/filemap`、`fs/filesystems/memfs/src/shmem.rs`、`posix/net/src/`、`net/knet/src/unix/`；已有能力先测，stream 缺口先复现后修复 |
| M1-002c seat/设备发现 | Weston 实际采用的 libseat/seatd/启动方式、DRM 权限、libudev/sysfs 枚举和 evdev；若命中 VT/KD 再做最小复现 | `io/ktty`、`fs/filesystems/devfs`、`fs/boot`；不预设换 Wayland 就不需要 VT，不照搬 Xorg 的修复清单 |
| [M2-001](tasks/M2-001.md) 显示最小闭环 | DRM VERSION/UNIQUE→资源/dumb buffer→两种图案/present→Weston DRM/pixman→wl_shm 客户端→monitor图像变化 | `fs/filesystems/devfs/src/nodes/dri.rs`→`io/drmdevice/src/card0.rs`→`drivers/devices/virtio/src/gpu.rs`；先验证软件合成所需接口，不预设 PRIME/dma-buf 可用 |
| M2-002 输入/会话 | QEMU keyboard/mouse→guest event→libinput→客户端；10 分钟显示服务 PID 不更换 | virtio-input、`io/inputdev/src/lib.rs`、`fs/filesystems/devfs/src/nodes/event.rs`；核对 eventN/sysfs/EVIOCGABS，先验证相对鼠标 |
| M2-003 图形资源回收 | 至少 100 次 create/map/present/unmap/destroy/退出，资源计数回到可解释范围；此为团队回归阈值，可并行且不阻塞首次成功存档 | DRM `retained_pages`/GEM backing 生命周期；不能靠重启服务清理并声称无泄漏 |
| [M3-001](tasks/M3-001.md) 浏览器首帧 | loader→browser/renderer→原生 Wayland surface/buffer→Weston→DRM present；正确显示原始 index；立即存档 | 原始 index 首帧已通过；baseline/first-runnable 已建立，IPC 期限为120秒；输入/稳定性/正常退出待验证 |

**本路线的 wl_shm 必须通过 Unix STREAM 的 SCM_RIGHTS 传递共享文件 fd，并正确映射同一对象。** DGRAM 已实现不代表此链通过；M1-004 与 Weston DRM/设备探测可并行，但 wl_shm 客户端/Chromium 不能跳过该依赖。PRIME/dma-buf、EGL/ANGLE 是否需要继续按浏览器实际协商验证，不把它们和 wl_shm 混为一项。沙箱、namespace、seccomp、membarrier 与用户凭据按实际调用定位，参数及最终隔离状态公开记录。

## 功能、计量与优化拆分

M4 分成四个独立闭环，每项有任务卡、日志和提交：

1. **M4-001 多进程/多页面**：记录 browser、renderer 的 PID/PPID/命令行和生命周期；GPU/utility 按实际模式记录存在或缺失原因，不要求每种角色必须独立出现；两个标签页或窗口切换，截图与进程记录对应。
2. **M4-002 真实交互与官方页**：QEMU 输入键鼠，记录回显、点击和跳转；interaction 单轮自检后等待，复跑先刷新；CSS 6/6 加截图目视核对；localStorage 不影响核心通过数。
3. **M4-003 稳定性/回收**：30 分钟持续会话包含每 2 分钟一次、共 10 次加载；记录 crash/OOM/超时、进程/FD/buffer/mapping 增量；失败保留现场，不用 respawn 掩盖。
4. **M4-004 资格线准备**：可靠峰值采样覆盖声明的对象与进程树；本地对 1.5G 预检，最终随官方脚本校准；2 GiB RAM 不证明峰值达标。

M1-003 提前定义启动首帧、页面加载、renderer 创建、峰值内存四类指标的起止点、时钟、样本及失败处理；CPU 作补充。显示服务 ready 与浏览器发起时刻分开记。DOM 结果不代表 monitor 已显示首帧；host QEMU RSS 不直接代表 guest 浏览器内存。正式口径未给出时保存原始时间线和本地定义，不标为官方成绩。

M5 每次只处理一个有数据支撑的问题，例如输入 poll 空转、DRM present/复制、buffer 回收、共享页或 renderer 创建；没有瓶颈证据就不排优化。明显正确性问题及时修复，不故意留泄漏/忙等制造改善。3D、零拷贝、完整沙箱是候选扩展，在稳定首版之后根据数据及用户取舍决定。

比较固定主机、QEMU/TCG、2 GiB/4 vCPU、页面/浏览器版本、设备、分辨率、缓存及日志级别，各不少于 5 次，给中位数和 min/max，失败样本也保留。以 `baseline/first-runnable` 为起点；后续正确性修复版本可另列，不能替换最早 tag。

## 评分、交付与外部依赖

初赛：图形 20、浏览器 20、兼容性 25、性能观测 15、文档 10、演示 10。决赛：稳定性 20、功能 20、性能资源 20、优化创新 20、工具 10、答辩 10。逐项解释见 [需求核查](analysis/requirements-and-testpages.md)，不混用初赛的方法分与决赛的名次分。

- 缺口须有真实复现，初赛最多 8 个计分，不为凑数虚构问题。
- 上游**已合并**补丁初赛最多占 12 分、决赛最多占 9 分。通用修复随开发整理，跟踪“可提交/已提交/已合并”；外发、push、PR 仍需用户授权，维护者合并是外部依赖。
- 工具须分别证明能定位崩溃、卡顿、泄漏，并有文档和一键入口。复用现有 syscall 日志/symbolization，不预设 guest 的 strace/perf/eBPF 已可用。
- **M6-001** 汇总设计报告、仓库、配置、构建/运行/测试脚本、日志、screendump、数据、完整演示录像；**M6-002** 干净目录复现；**M6-003** 收到官方平台/参数/脚本后现场流程复测。
- 当前附件没有单独的官方 QEMU 启动脚本或计量脚本。先按已定开发参数推进；正式统计对象、1.5G 单位、分辨率/缓存须随官方材料核对，不自行补成赛事硬规则。

## 用户学习与阶段验收

| 阶段 | 关键调用链与亲手实验 | 学习/阶段验收 |
| --- | --- | --- |
| M0 | 运行 run_guest.py 并对照 Build ID/设备/shell；比较版本能执行、socket能连接、真正显示像素 | 实现/证据完成，学习与阶段验收待用户完成 |
| M1 | 运行 `large-pie` 观察解释器基址如何随主映像 PT_LOAD 变化，再对比 Linux/x-kernel 的 fd、字节、errno | 最小用例、Linux 对照与修复回归已完成；**用户决定跳过亲手实验**，阶段验收仍待用户确认 |
| M2 | 改变 dumb buffer 颜色，观察 screendump/page flip；一次鼠标移动追到 evdev | 待图形环境 |
| M3/M4 | 开第二页，关联 renderer PID、共享内存、焦点/输入，再关页观察回收 | 待浏览器 |
| M5/M6 | 重跑至少 5 个样本，解释时钟、缓存和共享页统计如何影响结果 | 待可靠数据 |

每日已授权实现通过对应检查后可提交；阶段结果由用户验收。状态由证据推进，不因调查完整而自动提升为运行通过。用户可对某项亲手实验选择跳过，跳过时在该任务卡内明确记录决定与日期，不留「待确认」这类模糊状态；跳过不改变实现与证据的结论，也不等于阶段验收通过。
