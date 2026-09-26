# M1-003：启动 CPU 预算（复用已有 trace，2026-09-26）

2026-09-26，按用户要求执行上一轮分析中的 P3：**只用已有 trace 建立"CPU 花在哪"的预算**，不启动 guest、不重新追踪、不修改内核或第三方软件。全部输入是 2026-09-25/26 已归档的三条 Chromium startup trace（两条作主对照，一条计账修复前的用于交叉核对）。

**结论摘要：到达 `Startup.BrowserWindow.FirstPaint`，x-kernel 至少消耗 Linux 的 6.75 倍 CPU 秒数（≥95.651 s 对 14.172 s），4 vCPU 容量占用比例却相近（28.7% 对 30.5%）。** 也就是说多出来的墙钟时间主要是**多做的 CPU 工作**，不是同比增加的闲等；而且同一阶段内不同子系统的每次调用 CPU 都高出 5–22 倍，追踪机制自己也高出约 22 倍，说明这是**全谱系**的每操作成本放大，不是某一个热点函数。原因仍未定位到内核路径，本轮没有得出可提交的优化。

## 输入、工具与口径

| 项 | x-kernel | Linux 对照 |
| --- | --- | --- |
| trace | `artifacts/runs/m1-012-main-chromium-trace/chromium-startup.pftrace` | `artifacts/runs/m3-perfetto-startup-linux-20260925/chromium-startup.pftrace` |
| SHA-256 | `850570b52050076bcdc3c60bfd74c88561808fd78bac8539475f7f63e1248317` | `d2fd56a37a37341896d4404ed49c6cf3fc55d2ffa9a814e592d0f81ae6c3c01f` |
| 内核/系统 | 主区组合内核 `604d846e655cd409c7c3eba8b76db821840b7a51`，计账修复已合入 | 同盘原配 Linux `6.12.110-0-lts` |
| 运行 | `m1-012-main-chromium-trace`，磁盘前后 hash 相同，guest/QEMU/runner 均 0（[M1-012](../tasks/M1-012.md)） | `m3-perfetto-startup-linux-20260925`，正常关机（[Perfetto 记录](perfetto-startup-20260925.md)） |
| 输入盘 | `work/images/m1-012-combined-trace.img`，sha256_before `f6893e01c5f736a0dc90d35e7fc521504598aa802d6fe7b53719f27b3b264f3d` | `work/images/chromium-startup-trace-v1.img`，**同一个 sha256** |
| 共同条件 | QEMU 11.1.1、cortex-a76、TCG thread=multi、2 GiB、4 vCPU、原 `index.html`、`WAYLAND_DEBUG`/stderr 日志 | 同上 |

两侧输入盘逐字节相同（三条相关运行的前后盘 hash 均为上表值，且 `unchanged=true`，均为 `-snapshot`），因此用户态内容不是变量。两侧仍是**不同日期、不同宿主状态、各一次**的运行，未被交错执行；宿主内存/swap 状态没有在当次记录。

工具为官方 `trace_processor` v58.2（`work/tools/perfetto/trace_processor`，来源/哈希见 `artifacts/M1-003-perfetto-20260925/perfetto-tool.json`）。

口径：

- CPU 时间取每个 slice 的 `thread_dur`（Chromium 随事件上报的**线程 CPU 时间**）。该字段只在带计账修复的内核上可用，历史 `31c8f270` 会把睡眠计成 CPU；本轮 x-kernel 侧满足条件，Linux 侧本来就是正确计账。
- 阶段基准校验：把 `Startup.BrowserMessageLoopFirstIdle` 等指标视为"以浏览器进程起始为 ts、以经过时间为 dur"的启动指标后，`Startup.FirstWebContents.MainNavigationStart` 与导航 slice 的实际 ts 一致（x-kernel：13.028 + 32.694 = 45.722 对实际 45.714；Linux：8.309 + 6.954 = 15.263 对实际 15.261）。因此下文窗口起点用该基准时刻，误差在 10 毫秒量级。
- 嵌套 slice 共享同一段 CPU，因此所有预算只汇总 `depth=0` 的 slice；**任何 slice 之外的 CPU、没有上报事件的进程都不可见**，全部数字都是下界。
- 两条 trace 都没有调度器数据，`dur - thread_dur` 只能表示"该 slice 内线程不在 CPU 上"，**不能归因到具体锁、I/O 或定时器**。
- 阶段窗口不用整轮跨度对齐，而用各自的应用内里程碑：以 `Startup.*` 指标的基准时刻为"浏览器起始"，加 `Startup.BrowserWindow.FirstPaint`、`Startup.FirstWebContents.NonEmptyPaint3` 的经过时间得到窗口终点。
- 追踪本身拖慢被追踪对象，两轮各只有一次，且 x-kernel 的追踪开销远大于 Linux（见下）。**这些数字是诊断数据，不是性能成绩、也不是补丁收益对比。**

## 结果 A：阶段窗口的 CPU 预算

| 窗口 | x-kernel | Linux | 倍数 |
| --- | ---: | ---: | ---: |
| 浏览器起始 → `FirstPaint`（墙钟） | 83.449 s | 11.621 s | 7.18× |
| 同窗口已计入 CPU（`depth=0`） | **≥95.651 s** | 14.172 s | **≥6.75×** |
| 同窗口 4 vCPU 容量 | 333.796 s | 46.484 s | — |
| 容量占用比例 | 28.7% | 30.5% | ≈1× |
| 扣除追踪自身开销后的 CPU | 73.467 s | 13.154 s | 5.59× |
| `FirstPaint` → `NonEmptyPaint3`（墙钟） | 18.744 s | 2.298 s | 8.16× |
| 同窗口已计入 CPU | 44.783 s | 2.611 s | 17.15× |
| 容量占用比例 | **59.7%** | 28.4% | — |

窗口内的进程分布（CPU 秒）：

| 进程 | x-kernel（83.4 s 窗口） | Linux（11.6 s 窗口） |
| --- | ---: | ---: |
| Browser | 61.183 | 10.966 |
| Renderer（本轮首个） | 15.523 | 0.308 |
| NetworkService | 6.633 | 0.807 |
| TracingService | 6.423 | 0.299 |
| StorageService | 3.153 | 0.360 |
| Extension Renderer | 1.536 | 1.328 |
| GPU Process | 1.200 | 0.104 |

两个窗口的**容量占用比例几乎相同**（28.7% 对 30.5%），但 x-kernel 花了 7.18 倍墙钟、6.75 倍 CPU。第二个窗口 x-kernel 已接近饱和（59.7%），全程 10 秒桶里最高一段达到 39.79 CPU 秒 / 40 秒容量。这与"应用主要在干等"的解释不符；至少在已覆盖区间，时间被 CPU 工作吃掉。

## 结果 B：整轮时间线与进程分布（覆盖不全，仅作形状参考）

| 项 | x-kernel | Linux |
| --- | ---: | ---: |
| trace 跨度 | 159.657 s | 128.399 s |
| 已计入 CPU（`depth=0`） | 171.536 s | 29.607 s |
| 容量占用比例 | 26.9% | 5.8% |
| 峰值 10 秒桶 | 39.79 CPU 秒（100–110 s） | 16.89 CPU 秒（10–20 s） |
| 110 s 起每 10 秒桶 | 1.6–10.2 CPU 秒持续存在 | 0.5 CPU 秒（近乎空闲） |

Linux 的 CPU 集中在追踪开始后的头 20 秒，之后基本空闲；x-kernel 的 CPU 从 50 s 一直铺到 110 s。两条 trace 的跨度不同（x-kernel 的观察窗更长），因此该表只用来说明"工作是分散且持续消耗 CPU"，不用于计算倍数。

## 结果 C：同一操作的成本

**(1) 完全同工作量的一次对照——资源完整性检查的 3003 次读取**（同一镜像、同一文件、同样 3003 次调用，Chromium 源码同一版本）：

| 项 | x-kernel | Linux | 倍数 |
| --- | ---: | ---: | ---: |
| 调用次数 | 3003 | 3003 | 1× |
| 累计经过时间 | 16.024 s | 0.132 s | 121× |
| 累计 **CPU** 时间 | **6.108 s** | 0.121 s | **50×** |
| 单次最长经过 / CPU | 92.867 / 12.539 ms | 4.133 / 3.979 ms | 22× / 3.2× |
| 平均每次 CPU | 约 2.03 ms | 约 0.040 ms | 50× |

这是本轮最干净的一条证据：调用次数与语义相同，差异落在 CPU 上而不只是排队等待（经过时间倍数 121× 大于 CPU 倍数 50×，说明还有额外的调度/I-O 等待）。

同盘、同一批运行条件的另一次 x-kernel 追踪（`m3-perfetto-startup-xkernel-20260925`，内核为计账修复前的 `31c8f270`）给出同样的 3003 次调用与 **26.550 s** 累计经过时间（对 Linux 0.132 s，201×），其 `thread_dur` 一列为 26.917 s，已大于经过时间本身，**证实该轮 CPU 列不可用**，也说明这条读取异常在修复前后都存在，不是计账修复引入的。

**(2) 同一阶段内同名函数的每次调用 CPU**（各自"起始 → `FirstPaint`"窗口，按 slice 自身时间 = 包含时间减子 slice）：

| 函数（slice 名） | x-kernel 调用/ms 每次 | Linux 调用/ms 每次 | 每次 CPU 倍数 |
| --- | ---: | ---: | ---: |
| `ScopedBlockingCall` | 7663 / 2.414 | 5961 / 0.252 | 9.6× |
| `ThreadPool_RunTask` | 746 / 20.53 | 904 / 0.914 | 22.5× |
| `ThreadController active` | 2410 / 4.937 | 1390 / 0.605 | 8.2× |
| `ThreadControllerImpl::RunTask` | 4055 / 2.637 | 3292 / 0.408 | 6.5× |
| `Receive mojo message` | 1376 / 3.905 | 1283 / 0.769 | 5.1× |
| `EpollEvent` | 1223 / 3.186 | 960 / 0.579 | 5.5× |
| `SimpleWatcher::OnHandleReady` | 1586 / 1.575 | 1274 / 0.249 | 6.3× |
| `KeyedServiceFactory::GetServiceForContext` | 1682 / 0.672 | 8240 / 0.108 | 6.2× |
| `RenderThreadImpl::Init` | 1 次 / 12.660 s | 3 次 / 0.157 s（均值） | 约 80× |

调用次数同量级（相差多在 ±25% 以内），每次调用 CPU 却高 5–22 倍，且跨 Browser、Renderer、utility 与消息循环/阻塞调用/事件分发等互不相关的路径。这说明**放大发生在"每一次操作"的公共成本上**，而不是某个特定业务逻辑。

## 结果 D：计账自检（本次数据能不能用）

对整轮 trace 统计"CPU 时间大于墙钟时间"的越界量（不可能值），作为系统性偏差上界：

| 时长段 | x-kernel：slice 数 / 越界 >1 ms / 越界总量 | Linux：同三项 |
| --- | --- | --- |
| <1 ms | 22411 / 256 / 1.4122 s | 74252 / 2 / 0.3413 s |
| 1–10 ms | 36352 / 79 / 0.8023 s | 10581 / 1 / 0.0162 s |
| 10–100 ms | 10992 / 3 / 0.0212 s | 1318 / 0 / 0 |
| >100 ms | 1045 / 0 / 0.0002 s | 305 / 0 / 0 |

x-kernel 整轮不可能值合计约 **2.24 s，占 171.536 s 的 1.3%**；Linux 约 0.36 s。越界 slice 共 338 条（256+79+3），与 M1-012 记录的同类计数（主区 338、独立区 335）相符。短片段仍有采样误差，因此**不能用单条短 slice 下结论**，但聚合到秒级的预算是可用的，偏差量级不足以解释 6.75 倍差距。

## 结果 E：追踪自身开销（必须扣除的部分）

同一窗口内 `PerfettoTrace` 线程与 TracingService 的 CPU：x-kernel **22.2 s**，Linux **1.0 s**（差约 22 倍）。扣除后仍有 5.59 倍。

这既是一条限制（被追踪运行的数字偏慢，且两侧偏慢程度不同），也是一条独立发现：**追踪机制本身也按同一倍数变慢**，进一步支持"放大是全局的每操作成本"。

## 覆盖限制（决定了以上数字只能当下界）

| 项 | x-kernel | Linux |
| --- | ---: | ---: |
| 浏览器起始 → 该进程首个 trace 事件 | **+46.279 s** | +1.677 s |
| 该空档窗口内已计入的 CPU（其他进程，已包含在结果 A 的 95.651 s 内） | 20.362 s（Renderer/Network/Storage/Tracing） | — |
| 服务接管（TracingService 首个事件） | +38.2 s | +8.3 s |

浏览器事件开始得太晚，与固定版本 `kStartupTracingTimeoutMs = 30 s` 的启动追踪期限一致（见 [Perfetto 说明](../perfetto.md)）。因此：

- 结果 A 中 x-kernel 的 95.651 s **不含浏览器在起始后 46.3 s 内的任何 CPU**；真实值更高，6.75 倍是下界。
- Linux 侧只缺 1.7 s，基本完整。
- 本轮方法**无法覆盖 x-kernel 启动的前 46 秒**；要覆盖这一段只能靠内核侧计量或自编探针，不能靠 Chromium 自带追踪。

## 能支持与不能支持

**能支持**

1. x-kernel 到达首帧前多花的时间主要是**多做 CPU 工作**：容量占用比例与 Linux 接近，CPU 总量至少 6.75 倍。
2. 放大是**全谱系**的每操作成本：同一阶段内跨子系统的每次调用 CPU 高 5–22 倍；完全同工作量的 3003 次读取 CPU 高 50 倍。
3. 计账修复之后，Perfetto 的 `thread_dur` 已可用于秒级预算；本轮的越界偏差上界约 1.3%。
4. 第二个窗口（`FirstPaint` → `NonEmptyPaint3`）x-kernel 接近 CPU 饱和（59.7%），该阶段是纯算力受限。

**不能支持**

- 不能说明 CPU 花在用户态还是内核态、也不能指出哪个内核函数：trace 没有用户/内核切分，也没有调度事件。
- 不能说明额外 CPU 来自指令数、锁自旋、缺页/TLB、页表遍历还是 I/O 等待。
- 不能得出任何性能成绩、加速比或补丁收益：两边各一次、追踪开销不对称、宿主状态未控制。
- 不能说明"Linux 侧没有等待"：Linux 的 `dur - thread_dur` 只是没有归因，不是没有等待。

## 后续最小验证（与上一轮结论一致）

1. 同一次运行里同时采宿主侧 CPU（QEMU utime/stime、VmRSS、VmSwap、PSI）：本轮只能间接说明 guest 的 CPU 秒数，缺少宿主侧交叉核对。
2. 自编 C 探针把每操作成本拆开：顺序 `pread`、缺页、`munmap`、多线程分配竞争、`fork`+`exec`，Linux/x-kernel 同盘对照。
3. 内核侧按系统调用/路径累计 wall 时间、次数与自旋等待，覆盖 Chromium 追踪到不了的前 46 秒。
4. 有明确瓶颈后再按 M5 口径做 before/after（≥5 轮、固定输入、中位数）。

## 复现入口

```sh
# 预算（整轮 + 阶段窗口 + 自检）
python3 scripts/analyze_trace_cpu_budget.py \
  --run artifacts/runs/m1-012-main-chromium-trace \
  --output artifacts/M1-003-cpu-budget-20260926/xkernel-fixed
python3 scripts/analyze_trace_cpu_budget.py \
  --run artifacts/runs/m1-012-main-chromium-trace \
  --window-start-ns 13028376000 --window-end-ns 96477112000 \
  --window-label browser-start-to-first-paint \
  --output artifacts/M1-003-cpu-budget-20260926/xkernel-fixed/first-paint-window

# 对照轨迹把 --run 换成 artifacts/runs/m3-perfetto-startup-linux-20260925，
# 窗口换成 8309269000 → 19930351000。

# 汇总比较（只读已保存结果，不重新跑 Perfetto）
python3 artifacts/M1-003-cpu-budget-20260926/build_comparison.py
```

原图之外的原始分析（每条轨迹一套 SQL/CSV/日志与 `summary.json`）、自检 SQL/CSV、阶段窗口与同窗口函数画像均在 `artifacts/M1-003-cpu-budget-20260926/`；另有计账修复前那次追踪的 `integrity_reads` 交叉核对（目录 `xkernel-prefix-accounting-not-usable/`，仅取调用次数与经过时间）。派生比较为同目录 `compare.json`。本目录不含新运行、不含新内核产物。
