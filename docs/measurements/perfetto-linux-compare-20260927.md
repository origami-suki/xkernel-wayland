# 当前 x-kernel / Linux 启动对照（2026-09-27，M5-004）

本次新跑 x-kernel、Linux 各一次，两侧原始 `index.html` 均实际显示，正常清理并退出。相同 Chromium startup 指标中，x-kernel 的 `FirstPaint` 为 **78.351 s**，Linux 为 **9.739 s**；`NonEmptyPaint3` 为 **92.650 s / 11.424 s**。原页 navigation 本身为 **33.655 s / 2.876 s**。慢处覆盖导航开始前、原页加载与导航后绘制，不能由单个长函数解释全部差额。

最明确、可做小范围内核验证的候选是资源完整性检查的文件读取路径：两侧对应任务各有 **3003 次 `ReadAtCurrentPos`**，已记录 CPU 为 **5.782 / 0.135 s**，经过时间之和为 **11.298 / 0.156 s**。但是 x-kernel 中该任务在绘制前运行，Linux 中在 `NonEmptyPaint3` 后运行；此差异支持它是诊断目标，不能证明它阻塞首帧。另一个显著线索是原页 Renderer 的一段活动跨度 **48.000 s，CPU 10.218 s**；其余 37.781 s 不能在没有调度事件时归因到具体锁或 I/O。

本轮未实施优化；单次带追踪运行是诊断，全部倍数均仅描述这两个样本的同名指标，不是稳定性能或优化收益。

## 输入和实际运行

- 分支：`task/M5-004-perfetto-linux-compare`；冻结集成 `a0e2b5d1`、内核 `855a8c3c`。内核干净，准确版本新建 release/warn bundle；既有用户 `AGENTS.md` 修改保留。
- 宿主 AMD Ryzen 7 7840H，Arch Linux 7.2.6-arch2-1；QEMU **11.1.1**。AArch64 `virt,gic-version=3`、`cortex-a76`、`tcg,thread=multi`、4 vCPU、2 GiB。未用 `-icount`，时间包含 TCG 与宿主执行效应，不能当目标硬件周期。
- 同一 `work/images/m1-012-combined-trace.img`，均 `-snapshot`；两次运行前后 SHA-256 均为 `f6893e01c5f736a0dc90d35e7fc521504598aa802d6fe7b53719f27b3b264f3d`。复用原页、Wayland、no-sandbox、IPC 120 s、相同追踪类别与 64 MiB 主缓冲；实际脚本及哈希保存在 capture 目录。
- Linux **6.12.110-0-lts** 及 initramfs 从相同工作盘 `/boot` 提取；诊断 initramfs 的 switch_root 目标为 `/bin/sh`。两侧会话均记录 `SEATD_VTBOUND=0`。x-kernel 另有启动前临时文件读写 smoke，Linux 核对 online vCPU；不比较 boot-to-page，也不宣称冷缓存/宿主负载完全一致。
- 两次串行采集，全程独占 QEMU，运行时不构建。x-kernel UTC 00:07:04–00:11:22，Linux 00:11:22–00:14:47；实际命令见各 run 的 `command.sh`，输入/产物/进程信息见 `metadata.json`。

| 材料 | x-kernel | Linux |
| --- | --- | --- |
| 新 run | `artifacts/runs/m5-004-perfetto-xkernel-20260927/` | `artifacts/runs/m5-004-perfetto-linux-20260927/` |
| trace 字节数 | 10,198,174 | 11,228,700 |
| trace SHA-256 | `2ecdf7fc9503bc68d0ccaa64c44d0062355270ff769a13217acbd11c715fef0e` | `d27eb4c87182d49785a668abea0b09d7d1159390107f470d4324b4efc1aee6ee` |
| gzip/解压长度与哈希校验 | 通过 | 通过 |
| Chromium / Weston / seatd 清理 | 0 / 0 / 0 | 0 / 0 / 0 |
| guest / QEMU / runner 退出码 | 0 / 0 / 0 | 0 / 0 / 0 |
| 退出原因 | pid1-exit-for-kernel-sync-poweroff | guest-poweroff-f |

退出、字节完整性、Perfetto 可解析、页面显示分别核对，彼此不替代。宿主逐秒 QEMU CPU/RSS/Swap/PSI 原始记录见 `capture/*-host-samples.jsonl`：QEMU RSS 峰值约 1.94 / 1.39 GiB，VmSwap 峰值 2700 / 0 KiB。这是宿主 QEMU 指标，含模拟内存及 TCG/运行器阶段，不是 guest Chromium 内存；不能据此排除宿主干扰或证明 guest CPU 饱和。

## monitor 原图与首次完整原页观测

直接读取 monitor PPM 的无损 PNG 副本核图，转换前后逐像素一致，原 PPM 保留。x-kernel 第 1、2 帧黑屏；第 3 帧已有 Chromium no-sandbox 提示但页面主体为空；第 4、5 帧显示原页中文、四色块、表格、列表、圆/三角图及表单。Linux 第 1–5 帧均与 x-kernel 第 4 帧内容哈希一致。

| 系统 | 最后未显示完整原页的采样 | 首次采样到完整原页 | monitor host monotonic 观测区间，ns |
| --- | --- | --- | --- |
| x-kernel | 第 3 帧 | 第 4 帧 | 第 3 帧 `[26639338241199,26639347095600]`；第 4 帧 `[26669525227170,26669533784472]` |
| Linux | 本轮没有更早采样 | 第 1 帧 | `[26837580800308,26837590542299]` |

每 30 秒采样，共 5 帧；x-kernel 只把画面转变限制在第 3、4 帧之间约 30 秒的观测范围，Linux 只给出第 1 帧以前已经显示的上界。没有精确首次呈现时间，也没有将 host monitor 和 guest trace 时钟对齐。不能用本表给出精确首帧倍数，不能把 `FirstPaint` 或 `NonEmptyPaint3` 当 monitor 首帧验收。

## 同名 startup 指标与原页 navigation

只比较两侧都有的同名 startup 指标；其公共起点为 trace 中回溯记录的 Chromium startup 基准，x-kernel `12970727000 ns`，Linux `8033668000 ns`，不称为已测量的 exec 时刻。

| 公共基准至同名指标 | x-kernel s | Linux s | 本轮记录比值 |
| --- | ---: | ---: | ---: |
| MainNavigationStart | 30.054 | 6.197 | 4.85 |
| MainNavigationFinished | 58.887 | 8.968 | 6.57 |
| BrowserMessageLoopFirstIdle | 66.156 | 9.155 | 7.23 |
| BrowserWindow.FirstPaint | 78.351 | 9.739 | 8.05 |
| FirstWebContents.NonEmptyPaint3 | 92.650 | 11.424 | 8.11 |

原页 `Navigation: file:///opt/ict-testpages/index.html` 的 trace id 为 **563 / 7113**，起点 **43.015076 / 14.229615 s**，duration **33.655063 / 2.875959 s**；对应 URL 参数亦核对为原页，扩展后台页另列、不混算。Navigation 的 async 范围与 startup finished 指标不是完全相同边界。

原页 navigation 直属 `LoaderStartToFetchStart` 为 **25.812665 / 2.423743 s**，直属 `LoaderStartToReceiveResponse` 为 **26.311764 / 2.480057 s**；这两个范围相互包含，不相加。另一个带原页 URL 的异步 Loader track 为 **7.225915 / 1.151336 s**（Fetch）及 **13.073647 / 1.514826 s**（Receive），同样单列，不能把不同轨道相加为加载 CPU。原页 navigation 开始至 `NonEmptyPaint3` 为 **62.605888 / 5.227813 s**，显示慢处也延续到导航后渲染。

## CPU 口径及对齐窗口

CPU 使用 OS-reported `thread_dur`，当前内核带 M1-012 已校准的计账修复；仍存在短事件采样误差。本报告另存 SQL，汇总仅取 `depth=0 AND dur>=0 AND thread_dur>=0`，避免父子重复，按 `upid` / `utid` 保留同名进程和线程。CPU 跨多个核可大于墙钟时间。

窗口完全包含的 slice 满足 `ts >= start AND ts+dur <= end`。表中 C 是这些已覆盖 slice 的保守计账量，B 是跨边界且重叠窗口的完整 slice CPU 总量；不能知道 B 有多少实际落在窗口内。就这些完整已覆盖 slice 而言，窗口内计账可在 `[C,C+B]` 内，未覆盖/unfinished slice 和 slice 外 CPU 仍未知。**C+B 不是全部 CPU 上界，C 与 Linux C 的比值不是实际总 CPU 倍数下界**。未按 wall 比例分摊跨窗口 CPU。

| 各侧自身窗口 | 墙钟 x / Linux s | C x / Linux CPU s | B x / Linux CPU s | 跨边界 slice 数 x / Linux |
| --- | ---: | ---: | ---: | ---: |
| startup 基准→FirstPaint | 78.351 / 9.739 | 99.476 / 12.076 | 13.581 / 1.886 | 2 / 5 |
| startup 基准→NonEmptyPaint3 | 92.650 / 11.424 | 154.140 / 16.095 | 5.691 / 0.290 | 7 / 6 |
| 原页 navigation 全范围 | 33.655 / 2.876 | 61.299 / 3.529 | 23.912 / 7.859 | 8 / 9 |
| 原页 navigation 开始→NonEmptyPaint3 | 62.606 / 5.228 | 151.849 / 10.055 | 5.691 / 4.980 | 7 / 8 |

原页 navigation 范围在 Linux 上跨边界不确定量比 C 大，尤其不能拿只按开始时间选 slice 的旧查询认作精确同窗 CPU。全 trace 完整 depth0 CPU 为 **184.613 / 25.016 s**，跨度 **157.672 / 127.217 s**，含不同长度的绘制后追踪，不能直接作启动阶段比较。任何 CPU/4核容量比例都只反映这部分事件覆盖，不能证明纯算力受限或饱和；不使用按 slice 起点归桶的峰值来判断负载。

## 已覆盖成本分布与长任务

以下均为 startup→`NonEmptyPaint3` 完全包含 depth0 slice，线程是其进程的子集，不与进程行相加。

| 进程/线程 | x-kernel CPU s | Linux CPU s |
| --- | ---: | ---: |
| Browser 全部 | 81.967 | 10.169 |
| 原页 Renderer（pid 105 / 905）全部 | 23.384 | 2.574 |
| TracingService 全部 | 11.520 | 0.311 |
| Extension Renderer 全部 | 11.058 | 1.000 |
| GPU Process 全部 | 9.068 | 0.824 |
| NetworkService 全部 | 8.174 | 0.657 |
| 另一 Renderer（pid 128 / 927）全部 | 5.300 | 0.277 |
| StorageService 全部 | 3.669 | 0.281 |
| Browser / CrBrowserMain | 24.965 | 7.394 |
| Browser / Chrome_IOThread | 9.135 | 0.625 |
| 原页 Renderer / CrRendererMain | 12.338 | 1.868 |
| GPU / VizCompositorThread | 6.781 | 0.634 |
| 原页 Renderer / Compositor | 5.912 | 0.313 |

Browser 中 tid 83 的 Worker 活动累计 CPU **13.375 s**，其中一个父范围为 **23.763 s / CPU 13.360 s**。它包含下面的资源检查，不能再把父、子 CPU 相加。原页 Renderer tid 105 的 `ThreadController active` id 2236 在 **52.806749–100.806337 s**，经过 **47.999588 s**，CPU **10.218437 s**；Linux 对应主线程的一段活动 id 14858 为 **3.521064 s / CPU 1.472688 s**。这是线程活动范围，未证明两段包含完全同一工作。

x-kernel 原页 Renderer 内可见 `CreateWebView` **7.066 s / CPU 2.394 s**，`CommitNavigation` **6.112 s / CPU 2.241 s**，`ProxyMain::BeginMainFrame` **5.916 s / CPU 2.320 s**，`UpdateLifecycle` **5.435 s / CPU 2.176 s**；这些范围有嵌套/包含，仅列代表任务，不能累计成独立 CPU。长活动中 wall-CPU 差异支持进一步检查调度、同步及任务间空白，现有 trace 不足以命名具体锁/系统调用。

全 trace 的 self-CPU 推导榜单也保存了（`self_cpu_by_name.csv`），它是父 CPU 减直接子 CPU、负差裁零的事件层级残量，会受子事件缺失与采样影响；不将其称为函数独占 CPU 或完整调用剖析。高项是 ThreadController/RunTask、ThreadPool、ScopedBlockingCall、Mojo 和 Epoll 事件，只能定位事件层级热点，不能据此宣称 epoll 系统调用吃掉这些 CPU。

## 资源完整性检查：匹配调用次数，保留调度差异

`CheckResourceIntegrity` 来自 `chrome/browser/resources_integrity.cc:74`。每侧有 3 个含直接读取子 slice 的 ThreadPool 任务，以及 3 个短主线程回调；下表逐任务配对，未把回调与工作任务混算。读取子事件为 `base/files/file_posix.cc` 的 `ReadAtCurrentPos`；没有记录文件路径/字节数，因此这是相同函数与调用次数的对照，不额外声称逐字节相同的读请求。

| 任务 id x / Linux | read 次数（两侧一致） | 整个任务 wall s x / Linux | 整个任务 CPU s x / Linux | read wall 之和 s x / Linux | read CPU 之和 s x / Linux |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8174 / 43895 | 3003 | 21.139435 / 0.399604 | 11.602215 / 0.340611 | 11.297585 / 0.156376 | 5.782300 / 0.134806 |
| 24898 / 48200 | 178 | 0.512860 / 0.016163 | 0.437905 / 0.014954 | 0.263284 / 0.004405 | 0.215985 / 0.004393 |
| 25238 / 48386 | 297 | 1.395336 / 0.032716 | 1.090992 / 0.027056 | 0.745917 / 0.012405 | 0.562668 / 0.008637 |

3003-read 组的已记录 CPU 约 **42.89 倍**，wall 之和约 **72.25 倍**；其 task CPU 包含读取、完整性算法及其他工作，读取部分不另加到 task CPU。read 最大单次 wall **70.802 / 32.763 ms**、CPU **49.449 / 30.357 ms**。x-kernel 的任务 8174 从 **63.417438 s** 开始、**84.556873 s** 结束；Linux 43895 从 **20.414237 s** 开始，在 Linux `NonEmptyPaint3`（**19.457428 s**）之后。资源读取在 x-kernel 与首绘过程重叠，Linux 较迟运行；没有等待依赖或调度事件证明首帧必须等待检查结果。

## 追踪自身成本与覆盖质量

识别所有 `Perfetto%` 线程及 TracingService 进程，仍只取 complete depth0（进程与线程用 OR，避免重计）。startup→NonEmpty 的可识别成本 **19.458 / 0.443 CPU s**；全 trace **31.162 / 5.197 CPU s**。x-kernel 中 tracing utility main 该窗口 **8.994 s**，Browser PerfettoTrace **3.140 s**。这是带追踪工作负担，未测无追踪对照，不能称为追踪的增量开销，也不能从启动时间简单扣除得到“真实无追踪时间”。一些追踪操作由普通线程执行，当前分类仍不完整。

| 质量指标 | x-kernel | Linux |
| --- | ---: | ---: |
| sched_slice 数 | 0 | 0 |
| 全部 slice | 79,235 | 86,851 |
| 完整且有非负 CPU 的 depth0 slice / 全 depth0 | 10,945 / 12,385 | 11,043 / 11,908 |
| 全 trace unfinished slice | 46 | 56 |
| service 在提交前丢弃的 chunk / patch | 136 / 135 | 0 / 0 |
| 两个 buffer 的 chunk discarded / overwritten / writer packet loss | 均 0 | 均 0 |
| thread_dur > dur 的完整 slice（含所有深度） | 19,620 | 14,274 |
| 上述正差之和，仅 depth0，s | 0.169421 | 0.010310 |
| Browser 普通线程事件首次出现，guest trace s | 56.519880 | 9.502766 |
| 相对公共 startup 基准的早期空白，s | 43.549153 | 1.469098 |
| CrBrowserMain 首次事件，guest trace s | 56.521758 | 9.525131 |

早期 startup 指标是回溯记录，不能用其 ts 当作普通线程轨道完整覆盖。x-kernel Browser 早期约 43.55 s 的空白不表示线程没有执行，也不能把启动前半阶段缺失 CPU当成等待。depth0 有 CPU 的 slice 比例是事件数量比例，不能解释为 CPU 或墙钟覆盖率。CPU>wall 差额只显示采样不一致的可见正差，不是未知计账误差的最大上界。

两侧 `config_write_into_file_discard` 各 2 行、`config_write_into_file_no_flush` 各 1 行。Linux 另有 `flow_duplicate_id=1`；`track_event_parser_errors` 为 **6 / 15**，`track_hierarchy_missing_uuid` 为 **6 / 15**。主 buffer 写入/读出字节为 **9,060,352 / 8,859,648**（x）和 **9,994,240 / 9,793,536**（Linux）。这些数及 buffer/producer 统计保存在 `stats.csv`，包含零值的专项核对见 `discard_stats.csv`。DISCARD/未定期 flush 是配置风险提示；另有 x-kernel **`traced_chunks_discarded=136`、`traced_patches_discarded=135`**，是不同的实际 service 丢弃计数。按[Perfetto 当前官方 TraceStats 字段定义](https://raw.githubusercontent.com/google/perfetto/main/protos/perfetto/common/trace_stats.proto)，chunk/patch 在尝试提交/应用到 buffer 前被丢弃（无效 buffer ID 只是可能例子）；此处参考当前 main 的定义，不声称与 v58.2 完全版本匹配。buffer 内部丢弃、覆盖及 writer packet loss 两侧均为 0，不能将 service 丢弃解释成缓冲区满。未知丢弃时刻、涉及事件数量或原因，不能把这些计数直接归因到3003-read组、早期 Browser 空白或特定进程。解析提示和缺失早期轨道也限制归因，不能只因导入成功声称 trace 全部完整。

## 候选排序和下一项最小验证

| 排序 | 候选 | 本轮证据 | 能区分假设的最小验证 |
| --- | --- | --- | --- |
| 1 | read 路径与资源检查 CPU | 匹配 3003/178/297 次读取均有明显 CPU 差，明确 source/function；不能确定读字节与首帧依赖 | 用自编 C 在同盘工作副本复现资源文件读取粒度，分别测 CPU/wall，再只给 x-kernel read/VFS/缓存/复制增加少量分段计时；不修改 Chromium 或库 |
| 2 | 原页 Renderer 的长活动与 navigation→paint | 48 s 活动只计10.218 s CPU，CreateWebView/Commit/Lifecycle任务也慢 | 为这一条原页 Renderer 主线程对齐少量调度与阻塞/唤醒事件，分清就绪排队、真正阻塞和任务间空白；不先指定锁名 |
| 3 | 早期 Browser 启动未覆盖 | 导航开始前慢、普通轨道晚43.55 s，无法分配该段 CPU | 先补准确 Chromium launch marker 与最少内核进程/线程生命周期记录，核对 startup session→tracing service接管；不要把空白当成已定位热点 |
| 4 | 追踪执行负担及宿主扰动 | NonEmpty窗口可识别 tracing成本19.458 s、host曾有少量swap | 同输入做必要的轻类别/无追踪诊断与宿主CPU/PSI对齐；只用于检查追踪扰动，正式优化另做规定重复次数 |

当前最小工作应先验证 rank 1 的可重现读取差异，同时保留 rank 2–4 的不确定性。不能据本轮宣布某个内核模块是唯一瓶颈；不能用总CPU下界比、线程wall-CPU差或宿主RSS替代原页关键路径证据。

## 复查入口

- [任务卡](../tasks/M5-004.md)、[Perfetto 口径](../perfetto.md)。
- [捕获摘要](../../artifacts/work/m5-004-perfetto-20260927/capture/capture-summary.json)；各 run 的 `metadata.json`、`command.sh`、`serial.log`、`trace-export.json`、原 PPM 为正式运行证据。
- [本报告分析入口](../../artifacts/work/m5-004-perfetto-20260927/analysis/query_report.py)、[比较 JSON](../../artifacts/work/m5-004-perfetto-20260927/analysis/comparison.json)。`analysis/xkernel/`、`analysis/linux/` 保留每条 SQL、CSV、命令及解析日志；同窗 query 每侧按自身里程碑生成。
- `analysis/monitor-png/` 是用于核图的无损衍生物；`analysis/monitor-observations.json` 记录逐帧原始哈希、像素一致性及观测结论。

使用宿主 Perfetto **v58.2**。本次基础查询和新窗口查询均成功，独立 reviewer 重新查询关键指标、读取任务及 CPU 窗口；审查结果见 `analysis/review-result.md`。没有内核源码改动，未运行无关内核测试；没有推送。
