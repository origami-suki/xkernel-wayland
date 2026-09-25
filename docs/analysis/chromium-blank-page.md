# Chromium 正文空白：判断依据与逐项排查

**当前结果（2026-09-25）：原始 index 已显示并存档为 baseline/first-runnable。** 干净内核31c8f270，仅延长IPC连接期限至120秒即越过先前白屏；底层启动过慢原因未定位。以下第1–11节保留成功前的调查，最新结论见第12节及[首帧记录](../first-runnable.md)。

更新：2026-09-25。所属任务：[M3-001](../tasks/M3-001.md)。本文集中维护当前假设、排查顺序和结果；历史修复、上游候选及完整验收继续引用原任务卡，不重复建立问题文档。

## 1. 当前判断与版本边界

**优先假设是 x-kernel 的 Linux 用户态接口存在缺失、占位或语义不完整，导致 Chromium 的内容进程或进程间协作没有完成。尚未证明当前白屏由某一个具体接口造成。** 排查不能只看 syscall 是否有入口，还要检查参数、状态、副作用、对象生命周期、错误返回和等待唤醒。

源码中已有 virtio-gpu、DRM、共享内存和进程/同步实现；项目也已经验证 Weston 与动态 wl_shm 客户端显示。因此不将“官方图形适配尚不完整”写成“完全没有实现图形功能”，不凭赛题存在反推每个接口的实际状态。

- 本次开始时集成 HEAD：`10d0041b8664db4d741ac39396e573f05b524d05`；已有未提交文档改动，不能当作干净集成快照。
- 当前内核 HEAD：`ca59027cb5d6e02f2d2ce2cb9dd8872b1d1894ef`，本次静态核对开始时内核工作区干净；保留固定 v0.2.0 起点及已有本地修复。
- 本文主要白屏现场：`artifacts/runs/m3-cache-sync-after-20260925/`。它测试的是 `fc3f2348` 加冻结的缓存同步补丁，manifest 明确 `git-dirty=true`，ELF SHA256 为 `ccfa12f88b84a7bb2c6d0176d49cfb04b3050dd89f5b540447a20bd5980c7b59`。不能把该现场写成最新 `ca59027c` 的完整浏览器复跑。
- 功能对照：`artifacts/runs/m3-linux-observer-serialdrain-independent-20260925/`。使用同一赛方用户态来源和对应原生 Wayland/no-sandbox 配置；实际盘、命令及 seat 差异以各自 metadata 为准，不将不同 fixture 文件称为字节相同。
- 固定诊断配置：AArch64、QEMU 11.1.1、cortex-a76、TCG、2 GiB、4 vCPU；原生 Wayland、Weston DRM/pixman；原始 `file:///opt/ict-testpages/index.html`；沙箱关闭的限制明确保留。

## 2. 已知事实及其证明范围

| 已有证据 | 可以判断 | 不能据此判断 |
| --- | --- | --- |
| Linux 对照显示原始 index 的文字、色块、表格、表单和 SVG | 现有用户态、原页及对应运行路线具备可行性 | x-kernel 已具备相同接口语义 |
| M2 的 Weston/动态 wl_shm 客户端可见且能够正常退出 | 已测 DRM、共享缓冲和合成输出路径成立 | Chromium 的完整进程、缓冲和绘制路径全部成立 |
| 白屏现场第 8–12 帧显示 no-sandbox 提示栏及空白正文 | 浏览器自身界面至少有一帧到达屏幕 | renderer 已完成初始化或读到原页 |
| 原始浏览器日志包含非空 wl_buffer attach、commit、frame done 和 presentation 回执 | 已有实际缓冲提交与显示反馈 | 提交的缓冲中包含网页正文 |
| 成功 Linux 对照也出现 ANGLE/Vulkan 扩展初始化失败和 GPU 初始化退出 | 这些错误允许后续回退，单条报错不足以解释白屏 | x-kernel 的回退过程已经完整通过 |
| 白屏现场报告 `Network service crashed, restarting service`，所选 Linux 对照未出现该消息 | utility 服务异常/通信断开值得优先定位 | 已取得真实崩溃信号，或本地页面必须先实现完整网络栈 |
| 缓存同步修复后的现场未再记录 GPU `exit_code=132` | 已观察到的 CTR_EL0 陷阱有针对性修复证据 | GPU 或全部用户态执行问题已经解决 |
| 清理看门狗请求 KILL 后 wait 未结束，runner 超时 | 清理没有正常闭环 | 仅凭脚本输出就确认某个实际浏览器线程忽略 SIGKILL |

日志导出有重复：观察期间 tail 与最终 RAW_LOG 会重复同一消息。比较错误数量时，只取第一次 `RAW_LOG_BEGIN browser` 到对应 `RAW_LOG_END browser` 的完整区间；不得把重复输出算成独立崩溃。以上 run 的截图、完整命令、哈希与退出状态都保留原记录。

## 3. 为什么窗口可见而正文空白

Chromium 的 browser 进程负责浏览器界面及进程管理，renderer 负责网页内容，双方经 Mojo/IPC 协作；合成服务再接收内容帧并输出。简化后的排查链为：

```text
browser 发起原页导航
  → renderer 创建并完成初始化
  → 原页加载、解析与布局
  → 内容帧产生并交给合成服务
  → Ozone/Wayland 缓冲提交
  → Weston → DRM → QEMU 屏幕
```

浏览器提示栏可以先于网页内容出现。当前最重要的未知点是：正文还没有产生，还是产生后没有被合成/提交；不能仅凭白屏在两者之间作结论。

参考：[Chromium 多进程架构](https://www.chromium.org/developers/design-documents/multi-process-architecture/)、[Chromium 142.0.7444.59 Display Compositor](https://github.com/chromium/chromium/blob/142.0.7444.59/components/viz/service/display/README.md)。后者明确支持 GPU 或软件资源合成；Weston 使用 pixman 不能替代对 Chromium 自身绘制路径的验证。

## 4. 排查顺序与通过条件

一次只推进一个明确问题；没有新增证据时，先记录已知事实和缺失信息，不重复堆启动参数或扩大修改。

| 编号 / 优先级 | 本项要回答的问题 | 最小证据及通过条件 | 当前状态 |
| --- | --- | --- | --- |
| D0 / 前置 | 现有日志、截图、版本和观察字段能否支持判断？ | 核对两侧完整日志、现场版本及 procfs 真实实现，列出不可信字段 | **只读核对完成**；见第 5、6 节 |
| D1 / 最高 | 原页 renderer 是否创建、初始化并收到导航？ | 在当前准确内核上复现；取得 PID/TID 角色和初始化/导航阶段证据，区分 fork 成功、角色初始化与请求响应 | 已在当前内核重现空白并采到线程；初始化/导航尚未确认，见第 8 节 |
| D2 / 高 | Network Service 异常的最早失败是什么？ | 关联真实服务 PID、退出/信号或通信断开事件；同参数 Linux 对照；确认是否阻塞本次导航 | 已有服务重启消息，真实失败点待定位 |
| D3 / 高 | browser、renderer、utility 间是否有消息/共享对象或唤醒未完成？ | 从 D1/D2 指向的一条请求定位两端；缩成 FD/共享映射/事件等待的同盘 C 对照 | 候选；不得先重写所有 IPC 或 futex |
| D4 / 中 | renderer 是否解析并布局原页、生成内容帧？ | 初始化/导航已通过后，取得文档加载及首份内容帧证据 | 待 D1；不能从提示栏推断 |
| D5 / 中 | 回退后的合成是否接收并提交该内容帧？ | 关联内容帧、合成及实际 wl_buffer；核对回调、release、monitor 像素 | 界面缓冲已显示，正文帧链待验证 |
| D6 / 较低 | 是否存在资源不足、异常等待或调度导致的停滞？ | 可信线程状态、故障/分配事件、时间线；比较相同配置下的进展，区分慢与不再推进 | 尚无足够证据，不能用占位 RSS/CPU 字段排除或确认 |

D1 与 D2 如先获得确定的故障边界，可转入对应最小复现；不必等所有行都调查完才修复，也不预设需要补齐全部 Linux ABI。

## 5. 当前源码确认的候选接口

以下仅为本轮有限核对，**不是完整 syscall 审计或新的应用根因结论**。源码位置对应上述当前内核 HEAD。接口缺口、应用调用、因果验证是三种不同状态。

| 接口/路径 | 已确认的实现行为 | 与当前白屏的关系 |
| --- | --- | --- |
| `fcntl(F_DUPFD/F_DUPFD_CLOEXEC)` | 已在 `ec9df016` 修复最小编号、非法范围和软限制；[M1-008](../tasks/M1-008.md) 同盘动态/静态各 71 项通过 | 缺口已复现；原浏览器当前路径是否依赖它仍未证明 |
| fcntl POSIX/OFD 文件锁、`flock` | 同文件 SETLK/SETLKW 直接成功；GETLK 写 F_UNLCK；flock 为 TODO 后成功 | 占位已确认；实际调用及是否阻塞导航待关联 |
| 未识别的 fcntl 命令 | 已在 `31c8f270` 改为有效 FD 的 EINVAL、坏 FD 的 EBADF；[M1-011](../tasks/M1-011.md) 两版本各 20 项同盘通过 | 存在静默成功风险；必须先取得实际 cmd/arg，不能把所有 fcntl 都判为坏 |
| `membarrier` | `core/ksyscall/src/sync/membarrier.rs` 查询返回掩码，其他零 flags 请求只执行 `compiler_fence` 后成功 | 没有跨 CPU 执行同步的实现；是否被本次运行依赖未确认 |
| `prctl` 部分控制项 | `core/ksyscall/src/task/ctl.rs` 的 SET_SECCOMP、SET_DUMPABLE、SET_CHILD_SUBREAPER、SET_TIMERSLACK 等为空分支；部分 GET 返回固定值 | 按命中的具体 option 排查；NNP 已修复，不混写为仍未实现 |
| `seccomp` | `core/ksyscall/src/sys.rs::sys_seccomp` 返回 ENOSYS | 默认沙箱能力不足；当前 no-sandbox 配置不能据此宣称完整沙箱可用，也不能直接认定当前白屏由它导致 |
| DRM PRIME handle/fd | `io/drmdevice/src/card0.rs` 的两个方向只做数字转换，没有在这些处理路径建立或校验实际共享 FD | 非完整 dma-buf 语义；当前界面已走 wl_shm，正文是否选择 PRIME 需实际证据 |
| `/proc/<pid>/status` 部分字段 | `fs/filesystems/procfs/src/task_nodes/root.rs::format_task_status` 固定 Uid/Gid 为 0、CPU 掩码为 1、CPU 列表为 0 | 首先是观测限制；不能据此断言 kiosk 真正以 root 运行或只获 CPU 0 |

`Ok(0)` 本身不是缺陷判据：例如完成状态更新后返回 0 是正常实现。只有未执行所需操作、丢失参数、缺少状态或违反错误约定，才能记为具体缺口。`ENOSYS` 同样不自动是致命错误，需要确认应用是否可回退。

## 6. 第一项只读排查结果：观察边界

已核对当前 procfs：有 `comm`、`cmdline`、`task`、`maps`、`stat`、`status` 和 `fd`；当前 ThreadDir 枚举/lookup 不提供可直接使用的 `wchan`、`syscall` 或 `stack` 文件。不能照搬 Linux 的排查命令后把文件缺失当成进程没有阻塞。

已有 M3 记录说明 fork 后 cmdline 可能继承 Zygote 参数。新观察优先关联 `/proc/<pid>/task/<tid>/comm` 的线程名称、实际生命周期和应用阶段日志；线程名只能帮助识别角色，不能独自证明初始化、导航或绘制完成。status 的固定值也不能作为真实凭据、亲和性或内存计量。

据此，下一项最小验证是 D1：保持内核功能与原页不变，在准确当前 bundle 上复现，记录线程角色及 renderer/browser 初始化和导航的最早进展。需要额外可观测性时，先使用现有应用日志开关；仍不足再添加有明确问题的临时内核诊断，保留 patch 和准确产物，不改第三方软件源码/二进制。

## 7. 每项修复的闭环

1. 从原始应用现场取得实际参数、PID/TID、错误/信号或最后完成的请求；先区分运行配置、观察脚本与内核问题。
2. 疑似内核缺陷先查相关上游 issue/PR，覆盖关闭和合并状态；复用已有相同检索证据时说明日期与范围，访问失败与没有匹配结果分开记录。
3. 用最小自编 C 程序在同一用户态的 Linux/x-kernel 下对照，证明具体合同差异。只存在源码占位但没有应用关联的项目继续留在候选表。
4. 有可取上游候选时严格按 [AGENTS.md](../../AGENTS.md) 冻结、独立委派并阻塞等待；验证通过后引入准确受测的最小改动。没有相关补丁或验证受阻时按项目约定报告下一项最小验证，不自动扩大排查。
5. 记录接口复现、针对性测试、必要回归，以及原浏览器是否越过原停留点。接口测试通过与页面首帧通过分别标注。
6. 首次正确显示原始 index，立即保存 monitor screendump、日志、命令、准确代码/产物和不可变 `baseline/first-runnable`，不等后续完善。

单项记录字段：编号、假设、源码位置、实际触发、上游检索、Linux/x-kernel 对照、修复/测试状态、证据路径、应用进展、未解决问题。状态只使用待排查、已复现、通过、失败、阻塞；不得以候选项数量或 syscall 覆盖率代替首帧结果。

## 8. 当前内核的线程观察（2026-09-25）

运行 `m3-thread-roles-20260925` 使用干净 `ca59027c`、Build ID `cdf07a7145548b5d12259395b714daad4f573b8f8403a615f7b0b31da7169bc7`，保持原浏览器参数，只增加逐线程 comm 采集。命令为：

```sh
python3 scripts/run_guest.py --run-id m3-thread-roles-20260925 \
  --disk work/images/chromium-m1-cache-sync-v2.img \
  --bundle sources/x-kernel/target/xkmake/kplat-aarch64/release \
  --guest-commands tests/chromium/observe-threads.sh --timeout 250
```

结果：250 秒期限内取得计划 12 帧中的 8 帧，1–4 纯黑，5–8 为提示栏和空白正文；后四帧 hash 与既有白屏一致，已目视最后一帧。浏览器观察未完成、完整 RAW_LOG 未导出，runner=1、QEMU=0、`timeout-commands`，不能作为正常退出或 first-runnable。准确输入脚本以该 run 的 `guest-commands.sh` 为准。

已采到 browser 的 Chrome_IOThread、线程池及 CompositorTileW，子进程的 GpuWatchdog、Chrome_ChildIOT、Compositor 等线程。它们证明多个进程已进入线程初始化，但仍不足以证明原页 renderer 初始化、导航及内容帧完成，不把 D1 标为通过。

初版观察逐个执行外部 `cat` 读取 comm，会引入额外 fork/exec。当前 `tests/chromium/observe-threads.sh` 改用 shell 内建 read，避免这项可省去的观察开销；此修订已做生成脚本语法/命令保持检查，尚未重新执行完整浏览器观察。不能将两版观察耗时当成内核性能对比，或把观察超时直接归因于 cat。

原始运行证据在 `artifacts/runs/m3-thread-roles-20260925/`；`display-proof.json` 为逐帧检查，`last-frame-preview.png` 仅是最后一张 monitor PPM 的派生预览。有限静态核对的八个源码文件 hash 与既有日志比较在 `artifacts/M3-001-blank-page-20260925/inspection.json`。

## 9. 运行后接口清单与修复批次

用户要求：每次运行后记录实际出现的未实现调用，形成待处理清单。两侧 runner 现在在成功、失败和超时收尾时自动生成 `missing-interfaces.json`，将采集器副本/hash、清单 hash 写入 metadata；清单写入失败会使 runner 报失败，不以空清单替代错误。此前已经结束的 run 可用下面的命令追加派生索引，保留原日志与 metadata，不覆盖已有索引：

```sh
python3 scripts/missing_interfaces.py artifacts/runs/<run-id>
```

记录内容包括日志明确命名的 syscall、fcntl/prctl 子命令、可见 PID/TID、原始行号、原文和哈希。应用层的 ENOSYS/EOPNOTSUPP 消息保留为“待归属操作”，不从 libc 包装函数名猜测 AArch64 syscall。日志未打印的参数不补造；重复 tail/RAW_LOG 行号不是调用次数。

**这份清单按当前识别的日志格式收录未实现/不支持消息，不是完整 syscall trace。** 当前默认 warn 会遗漏仅 debug 输出的 `sys_dummy_fd` 路径；返回成功的占位实现也未必有任何日志。新格式需补采集规则。空清单不等于兼容性通过；超时未导出完整应用日志时尤其不能据此排除接口问题。需要查清安静返回或成功占位时，继续关联第 5 节源码并按具体假设增加定向观察。

| 已收录操作 | 证据来源 | 当前处理状态 |
| --- | --- | --- |
| `landlock_create_ruleset`，TID 29 | 既有白屏及当前线程观察的内核日志 | 已触发；浏览器随后仍有进展，是否只是能力探测及是否需实现待确认 |
| NETLINK bind 返回 Not supported (95) | 既有白屏及当前线程观察的应用日志 | 已触发；具体协议/参数及服务异常因果未确认，不等同于 bind 整体未实现 |
| `inotify_init()` 返回 Function not implemented (38) | 既有完整白屏应用日志 | 已触发的应用层操作；底层 syscall 与回退影响待关联，当前线程观察无完整应用日志不能据缺失排除 |

每项默认 `implementation_required=null`、`impact=not_assessed`。后续按证据归为：

1. **当前应用必需且阻塞**：补齐真正依赖的语义，优先修复。
2. **能力探测且能够正确回退**：保留返回 ENOSYS/不支持可以是正确行为，暂不作为首帧前置。
3. **返回成功但语义占位**：单独做合同和应用路径核对，不能因没有错误日志而略过。
4. **影响尚不明确**：留在清单，先补最小证据，不直接当作必修项。

允许把同一故障链上已确认相关的最小修复组成一批；每批完成接口对照和原浏览器复跑，再推进下一批。**不以“全部已见调用都实现”作为下一次复跑的门槛**：这会推迟发现回归，也无法保证覆盖此前未执行到的路径。所有能力的长期完备性与当前页面首帧是不同目标。

验证：新增采集器四项 host 测试，加原有 runner/frame/serial 测试，共 16 项通过；覆盖原文/行号保留、重复消息不计作调用次数、应用操作不冒充 syscall、空日志限制和拒绝覆盖旧证据。Python 编译检查、shell 语法检查及差异空白检查通过。`m3-interface-inventory-smoke-20260925` 与 `m3-interface-inventory-linux-smoke-20260925` 已实际走完两侧 runner、自动生成清单并正常关机，两者 guest/QEMU/runner=0、工作盘 hash 不变；这是采集流程验证，不是浏览器验收。

## 10. F_DUPFD 修复后复跑（2026-09-25）

用户授权自行实现本批已知缺口，并确认不再为这些接口逐项检索上游。M1-008 自行修复已提交内核 `ec9df016`，Linux/x-kernel 的动态/静态边界对照、13 项内核单测、M1/M2 及 Weston 回归通过，准确证据见任务卡。未作独立 Agent 验证。

`m3-dupfd-after-20260925` 保持 Chromium 参数，采用已有内建 read 线程观察版本；12 张 monitor 原图中后 6 张仍是相同的提示栏与白色正文，完整应用日志含 Network service crashed。观察循环完成，清理阶段 KILL 后 wait 未返回，300 秒超时，QEMU=0/runner=1。因此本项接口修复通过，原页首帧未通过；当前证据不能将 F_DUPFD 定为白屏根因，也不能排除它曾影响其他启动路径。下一项是未知 fcntl 命令的错误返回合同与应用阶段定位。

## 11. 未知 fcntl 修复及子进程连接超时边界

M1-011 内核 `31c8f270` 已通过 Linux/x-kernel 的动态/静态各 20 项对照和 F_DUPFD 各 71 项回归；详细记录见任务卡。未将文件锁占位等其他接口一并改为成功或拒绝。

`m3-fcntl-stages-20260925` 的详细应用日志确认 browser 对原始 index 调用 `FileURLLoader::Start`，但这不是加载完成。PID 86 的 cmdline 明确为 NetworkService，PID 98 仍继承 zygote cmdline；两者打印 `ChildThreadImpl::EnsureConnected()`。固定版本源码显示这是未收到连接完成回调时的超时终止函数（退出状态 0），不是连接成功。D1 已定位到 browser 发起文件加载、子进程 IPC 连接超时之间的边界；renderer 的准确 PID、响应和首个内容帧仍未确认。

本轮只加 `--v=1`，8 张 monitor 图全部黑；原始日志完整，清理强制 KILL，guest/runner=1/QEMU=0，最终正常关机。较短观察窗口不用于判断 F_DUPFD/未知命令修复的显示回归。下一项取两端 sendmsg/recvmsg 的 FD、长度、返回值及等待证据，缩小握手问题；不扩大为全部 IPC 或调度器重写。

## 12. 连接期限对照与首个内容帧

固定 Chromium 142.0.7444.59 的 ChildThreadImpl 默认连接期限为15秒；EnsureConnected 是超时终止回调，OnChannelConnected 应取消它。带上限的临时内核日志表明超时前已有双向消息及FD接收：诊断现场的NetworkService PID95收9次、发1次；zygote子进程PID102收34次、发3次，并收到11次control=24的消息。没有据此认定FD内容、全部握手或共享状态正确。诊断补丁和准确ELF保存在 `artifacts/M3-001-ipc-connect-20260925/`，补丁已经撤回，主内核干净。

同盘Linux详细日志对照 `m3-ipc-stages-linux-20260925` 原页可见且正常退出。干净内核31c8f270的默认参数长观察 `m3-fcntl-clean-long-20260925` 仍得到提示栏和空白正文。随后同内核/工作盘/观察脚本只增加 `--ipc-connection-timeout=120`，`m3-ipc-timeout120-20260925` 第7帧开始显示原页，7–12帧内容hash相同。三份官方页面hash逐一匹配原始压缩包。首次发现立即归档，未改第三方程序或页面。

这个结果支持“本次路径被过短的连接期限中止”，不证明所有接口正确，也没有定位慢初始化或调度的根因。不同观察开销/主机负载未作为正式性能实验控制，不能给出加速倍数。原页首帧已通过，正常清理仍未通过；文件锁、membarrier、prctl、NETLINK/inotify等保留待处理状态，不能因页面已出现而标为已修复。
