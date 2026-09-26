# 当前版本首帧关键路径：证据审查与采集设计

2026-09-27。结论：暂缓 openat 创建路径深挖，下一任务改为当前版本的启动时间预算。现有数据证明 munmap 优化节省累计 CPU，尚不能解释约 76 秒首帧的关键依赖，更不能推出每个 syscall 都比 Linux 慢。本轮只读分析源码和历史证据、修正文档；没有启动 QEMU、修改内核或形成新性能结论。

## 冻结起点与证据身份

- 新集成 worktree：`work/m5-004-startup-critical-path`，分支 `task/M5-004-startup-critical-path`。
- 分析起点：集成 `a0e2b5d1`（完整 `a0e2b5d1939658766e23fab0093d760b11d4bf9c`）；内核 `855a8c3c`（完整 `855a8c3cf2c8c4e868bd89ed32672d72395001d2`）。新 worktree 内核为同提交的独立只读 checkout。
- 原工作区仅 `AGENTS.md` 有用户未提交修改；不改动、不带入本任务提交。原补丁另存新 worktree 的 `artifacts/analysis/m5-004/main-agents.patch`。
- 本文下述历史 `artifacts/` 路径均相对原项目根目录；本轮派生记录在新 worktree 的 `artifacts/analysis/m5-004/`。未覆盖任何旧 CSV、trace、截图或摘要。
- 原配置仍是 AArch64/cortex-a76、QEMU 11.1.1、TCG thread=multi、2 GiB/4 vCPU、Wayland/Weston pixman、原始 index、no-sandbox、IPC 120 秒。未来每次运行仍须记录实际 argv、配置和输入 hash。

## 现有证据分别能回答什么

| 证据 | 可保留的观察 | 不能据此推断 |
| --- | --- | --- |
| [M5-002 五对 munmap 数据](../measurements/munmap-sparse-20260926.md) | munmap CPU 中位 37.654816 → 0.274485 秒；原页观察区间中位约提前 4 秒 | 37 秒全部在首帧关键路径；CPU 节省除以 4 是首帧收益上限 |
| [优化后复扫](../measurements/syscall-hotspots-post-munmap-20260927.md) | 完成调用的 syscall CPU 中位 33.271652 秒，openat 第一；原页观察中位 76.005–77.020 秒 | 76−33 是等待/用户态时间；openat 是下一处最有效首帧优化 |
| [同盘 Linux 历史单轮](../measurements/linux-first-frame-1s-20260925.md) | 当时同盘同资源约 10–11 秒出现原页正文 | 当前内核正式倍数；当前 profiler-on 与该单轮是严格配对 |
| [旧 CPU trace](../measurements/cpu-budget-20260926.md) | 计账修复后，两边同为 3003 个 ReadAtCurrentPos 区间，累计 thread CPU 6.108436 / 0.121108 秒 | 等同 3003 次纯 read syscall；后台资源校验挡住首帧；已证明公共根因 |
| [openat 两轮诊断及 C 探针](../measurements/openat-path-cost-20260927.md) | 路径遍历与末查找/创建占主要 openat CPU；真实创建比已有文件打开贵 | 创建位于首帧依赖链；跳过同步、权限或新增缓存已有依据 |

历史 Linux 正文首次可见与当前 profiler 的“完整帧 hash 相同”还是不同终点：前者第 11 秒已有正文，第 12 秒提示消失后才与 baseline 完全一致。下一轮同时记录正文首次可见和首次 hash 匹配，选择同一主口径比较；不能把二者混成一个精确时间。

两份旧 `availability.csv` 都是 `sched_slices=0`。没有 ready、switch、block、wake 记录，就无法把 off-CPU 区间拆成就绪排队和阻塞，也无法回溯谁在等待谁。

## 旧预算分析需要纠正的地方

本轮核对 `scripts/analyze_trace_cpu_budget.py` 与已保存 SQL/CSV，发现除了解释过强，还存在统计口径缺陷：

1. `WINDOW_QUERIES` 按 `s.ts >= start AND s.ts < end` 选片段，再加整个 `thread_dur`。跨出窗口的 CPU 会带入，窗口前开始但延续到窗口内的会漏掉。已存 `xkernel-fixed/first-paint-window/window_process_cpu.csv` 的 Browser `last_seconds=103.433`，窗口终点实际为 96.477112 秒。这不是精确的阶段 CPU，也不是可靠下界。
2. `cpu_timeline` 把完整 CPU 放入 slice 起始所在的 10 秒桶。因此 39.79 / 40 不能证明那个 10 秒区间接近饱和；不能用均匀按墙钟比例切 CPU 来伪造修复后的精确值。
3. 汇总器用 `process_name` 作唯一键，同名 Renderer 被后者覆盖。Linux FirstPaint 原 CSV 为 2.718 + 0.308 秒，summary 只留 0.308；x-kernel nonempty 漏掉 12.690 秒。恢复后也只是起点入窗的完整片段之和，不是修正后的窗口 CPU。进程必须使用生命周期 ID/PID，名称只能作标签。
4. `window_tracing_overhead` 没有限定 `depth=0`，会有父子重复计数风险，且沿用起点分桶。即使改成互斥 CPU，减去 tracing 线程也不能消除生产线程写事件、IPC、调度/缓存扰动等开销。因此“扣除追踪开销后 5.59 倍”不是无追踪性能结果。
5. `depth=0` 是每条 track 的层级，不自动保证同一线程多个 track 互斥；缺失 CPU 字段也不是零 CPU。两边都覆盖不全时，两个可见量的比值更不是总 CPU 比值的下界。
6. `CPU > wall` 的超额总量只是异常指标，不能当作全部计账误差的上界。历史 1.3% 不能证明剩余统计误差小于 1.3%。
7. 同名 `RunTask`、Mojo、Epoll 等 slice 内可含不同用户态工作、syscall、缺页和同步；任务平均值升高只支持跨操作公共成本的待验证假设。59.7% 聚合占用也不能判为“纯算力受限”。

旧数值保留为原算法产物，撤回“6.75 倍下界”“全谱系每操作变慢”“纯算力受限”和“多出的墙钟主要由 CPU 解释”等结论。本轮没有修复分析器；旧工具不得直接承担 M5-004 的精确分段预算。

## 可以复用的阶段线索，不是当前 76 秒的分解

旧计账修复后、munmap 优化前的 trace 里程碑如下，均以各自 Chromium startup 基准为零，不是 host launch 标记：

| 应用内里程碑 | x-kernel 秒 | Linux 秒 |
| --- | ---: | ---: |
| MainNavigationStart | 32.694 | 6.954 |
| MainNavigationFinished | 74.605 | 10.622 |
| BrowserWindow.FirstPaint | 83.449 | 11.621 |
| FirstWebContents.NonEmptyPaint3 | 102.193 | 13.919 |

这些数据只用于确定采集应覆盖启动前段、导航段及提交段，不能移植成当前 76 秒内各阶段的长度。`FirstPaint` 不是原页正文呈现，`NonEmptyPaint3` 也不替代 monitor 像素。原 Browser trace 早期约 46 秒缺口与追踪服务晚接管有关，但“只能改内核才能覆盖”也不是已证结论；应先验证现有启动标记和内核生命周期事件能覆盖到哪里。

现有 profiler 的停止边界也需要显式保留。根据七份 `metadata.json` 复算，“首张匹配帧采集结束 → host 收到 startup stopped 标记”在 M5-002 candidate 1–5 为 0.396521、0.375713、0.272406、0.333927、0.416822 秒，openat v1/v2 为 2.341297/2.795091 秒。这包含串口、guest 执行及标记回传，**不是全部发生在 profiler 仍开启期间的精确时长**。但足以说明不能把聚合统计窗口直接等同首帧窗口。派生输入 hash 和完整字段见新 worktree 的 `artifacts/analysis/m5-004/existing-window-boundaries.json`。

## 下一轮预算应如何闭合

对一个线程的一个已观测生命周期窗口，用互斥状态建立：

`窗口 = on-CPU + runnable 未运行 + blocked + unknown`。

缺失事件、窗口边界不明和线程身份不明都保留在 unknown；线程创建前/退出后不是其等待。on-CPU 内再分用户态、syscall、fault、其他内核/中断；fault 可能嵌套在 syscall 内，必须按嵌套状态划分互斥时间，不能再把两者相加。锁自旋通常仍属于 on-CPU，不能当作 blocked。

这只是线程预算。全进程 CPU 求和反映资源成本；首帧预算应从最终画面向前回溯依赖边，在不同线程间沿决定完成时刻的路径切换，每个墙钟区间只算一次。并行分支、后台资源校验、Weston 及 I/O worker 单独展示；只有“消费者等待某生产者结果”的证据，才能把生产者工作纳入关键链。没有依赖边时报告“未归因间隔”，不能用最大 CPU 线程补齐。

| 需要关联的节点 | 最小证据 | 缺失时的处理 |
| --- | --- | --- |
| host launch → browser exec/初始化 | 带 guest monotonic 的启动标记、exec 和 PID/TID 生命周期 | su/exec 前段单列；不拿 Chromium trace 的回溯起点代替 |
| 子进程建立 → IPC 可服务 | fork/exec/exit、实际任务身份、已有 Chromium IPC flow 或成对请求/响应 | zygote cmdline 不足以区分 renderer；spawn 不等于 ready |
| 导航 → 文档读取/解析/布局 | 原始 URL 的导航标识、renderer frame/navigation ID、应用 trace | 跨进程时间先后不是依赖证明；早期缺失保留 unknown |
| 合成 → Wayland 提交 | Chromium/Viz 事件，已有 WAYLAND_DEBUG 的 surface/buffer/commit 关联 | attach/commit 不证明原页内容或已经显示 |
| Weston → DRM → monitor 正文 | 合成器相关线程调度/等待、内核 DRM/virtio 边界、monitor 两张边界原图 | 无帧身份关联就不声称某次 commit 对应原页；像素区间作终点 |

### 最小观测层次

第一层先获得全启动期任务状态和 CPU，不再开启另一轮 openat 专项插桩。x-kernel 需要低干扰的生命周期、入队/切换/阻塞/成功唤醒事件；已有 syscall profiler 只能作为累计成本交叉核对。按 CPU 预分配有界二进制缓冲，禁止事件热路径格式化、动态分配和串口逐条打印；记录序号、容量、丢失数、每 CPU 时钟和 stop 原因，结束后导出。事件量/缓冲大小通过短校准决定，不能假定某个容量一定覆盖 76 秒。

第二层用相同运行内的应用里程碑、任务身份和调度状态找出最长未解释段。仅对该段需要的 wait queue/futex/epoll/socket/I/O/timer 添加对象标识、成功唤醒者、等待开始/结束/返回原因及请求关联；记录对象生命周期，避免地址复用错误配对。唤醒者不一定是数据生产者或锁持有者，仍须核对资源/消息依赖。必要时追踪链延伸到浏览器树外的 Weston、内核 worker、IRQ；不能只采 browser 后代。

第三层才对已确认关键路径的执行部分拆 syscall、fault、锁自旋或用户态工作。若多个匹配操作都高 CPU，再用同工作量 C 探针验证公共入口、计账/计时开销等假设。后台任务只有直接依赖或资源竞争证据时才升级为首帧优化候选。

TCG 下 guest 计账是虚拟时钟上“任务处于运行状态”的时间，不是目标硬件周期或宿主实际执行指令的时间；host 抢占 QEMU vCPU 可能混入 guest on-CPU 间隔。必须同时采 host QEMU 总 CPU、各 vCPU 线程运行/就绪等待、VmSwap、内存压力和系统负载，先排除宿主资源干扰。当前 `run_syscall_profile.py` 只采 QEMU 总 ticks、VmRSS/VmSwap、meminfo/loadavg，没有各 vCPU 线程调度和 PSI；Linux runner 也没有同等采集入口，不能称已完成对称观测。

### 当前源码可复用的入口与缺口

路径相对 `sources/x-kernel`，对应冻结的 `855a8c3c`：

| 入口 | 已有能力 | 需要补齐/校准 |
| --- | --- | --- |
| `process/kprocess/src/thread/cpu_time.rs`，`thread/core.rs:534` | User/Kernel/Inactive 账本与切换 | 无逐状态时间线；IRQ 可计入当前线程，不能等同纯 syscall CPU |
| `posix/process/src/runtime.rs:71` | 用户态进出、PageFault 返回处理 | fault 事件及嵌套区间，区别用户 fault 与 syscall 内存访问 |
| `task/ktask/src/run_queue.rs:1000,1871` | 已有 sched_wakeup、sched_switch tracepoint | switch-out 原因、首次就绪/重入队、唤醒来源、进程生命周期 |
| `task/ktask/src/wait_queue.rs:54` | 通用等待和 notify | 稳定对象 ID、具体等待类别、成功状态转换与唤醒原因 |
| `core/ktracing/src/lib.rs:25,221` | 现成 tracepipe 和事件写入 | 单个 TracePipeRaw(4096)，写入使用 `buf.to_vec()`；容量单位、覆盖/丢失语义需核对，不能直接用于全启动低扰动采集 |
| `fs/filesystems/procfs/src/trace_nodes/root.rs:38` | `/proc/tracing/events`、`/proc/tracing/trace` 控制与读取入口 | 先做短窗口功能/丢失校准，不因接口存在就认定捕获完整 |

已有事件入口可先复用，不能从“当前没有 sched trace”推出“内核完全没有 tracing”。现有 switch/wakeup 参数不足以独立恢复完整三态和等待依赖，聚合 `/proc/sched_stat` 也不能替代任务级时序。Linux 历史启动串口记录过 ftrace 初始化（43796 entries），说明有基础能力，但尚未核实所需 tracefs 事件和可用配置。

### Linux 对照与时钟

优先使用镜像内 Linux 的现有 tracefs 能力。先核对实际 kernel config、tracefs 挂载及 `available_events`：调度 switch/wakeup/new-task/exit、syscall、fault/IRQ、block 事件各自是否存在；这一步尚未执行，不能预设可用。通过自编 C/既有 shell 读取二进制或受控 trace 输出，不新增第三方 tracer、不替换 Linux 或用户态。若必要事件不可用，报告具体缺口，再缩小共同口径；不能拿 Linux 不存在的字段当零，也不偷偷降成不对称的“完整预算”。

host monotonic、guest monotonic、Chromium trace clock、WAYLAND_DEBUG 时间分别记录。用带 nonce 的双向标记把 guest 时间夹在 host 发送/接收之间，启动前后重复校准偏移和漂移，保留不确定区间；不得用 guest 日历时间或串口行到达时间硬对齐。stop 发生在首帧之后，分析必须按对齐后的帧区间截断/给上下界。只有完整 slice CPU 时，可给完全包含片段与边界片段的范围，不能猜测其内部 CPU 分布。

## 串行执行顺序与验收

后续实施分成 [M5-004](../tasks/M5-004.md) 的独立小闭环；本轮只完成分析：

1. 校准观测：运行/睡眠、单核就绪竞争、futex/pipe 定向唤醒、线程创建退出、跨 CPU 迁移、缺页与中断边界。证明状态闭合、无负时间/重复 on-CPU、CPU 不超可用容量；验证丢失检测和缓冲满行为。先核实 Linux 能力，避免做完单侧才发现无法对照。
2. 同一冻结工作盘、同 Chromium/Weston/原页/日志/profile/cache 条件，先完成一对串行 Linux→x-kernel 诊断；用于发现覆盖缺口，不报告正式倍数。只在覆盖合格后反转顺序复核关键段。任何时刻全机最多一个 QEMU，性能采样期间不并行构建。
3. 开启分层追踪的诊断与关闭新追踪的同条件基准分开记录。固定日志及已有 profiler 开关，不重启 M1-013 已取消的独立五开五关验收；新追踪自身仍须做必要扰动校准。不得直接从总 CPU 中减一个 tracing 线程估算无追踪首帧。
4. 输出每段 Linux/x-kernel 的墙钟区间、关键 TID/角色、on-CPU/runnable/blocked/unknown、依赖对象和证据 ID。保留线程全量资源预算及后台成本作为旁表，不把它们相加到首帧总时长。
5. 有“关键等待边 + 生产者工作 + 对照差异”才选择优化；如果只定位到一个窄阶段，明确下一项测量。正式收益必须回到原页 monitor、正常退出和必要回归，before/after 每侧至少五轮，报告中位数/范围及失败样本。

完成标准不是画出一张占满 76 秒的图，而是每段有可追溯状态和依赖，未知量显式保留。当前完整启动预算、关键阻塞对象和首要优化点仍未取得。
