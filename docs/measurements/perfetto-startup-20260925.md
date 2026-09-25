# M1-003：Perfetto 启动追踪接入与首轮发现

**接入通过，慢启动尚未修复。** 已从当前 x-kernel 和同盘 Linux 分别导出 Chromium 原生 protobuf trace，核对传输大小与双层 SHA-256，使用官方 Perfetto Trace Processor v58.2 完成 SQL 分析，并在 Perfetto UI v58.3 实际打开 x-kernel 文件。发现明确的长读取路径，以及使线程 CPU 时间不可用的计账问题；早期事件覆盖仍有缺口。

入口和解释规则见 [使用说明](../perfetto.md)。本轮遵循 `docs/tasks/M1-003.md` 的计量校准范围，不修改内核、第三方用户态或原测试页，也没有将任何上游候选合入。

## 输入与原始证据

- 主内核 `31c8f27023ce483ff327386597f6e0a640d705f9`，bundle 为 `artifacts/M1-011-fcntl-unknown-20260925/clean-bundle`。集成起点 `0385151da77fe076678846e4128ba8fe05e5e4fb`；用户既有 26 份文档改动原样保留。
- QEMU 11.1.1、cortex-a76、TCG multithread、4 vCPU、2 GiB，原始 index，既有原生 Wayland / no-sandbox / IPC 120 秒参数。Linux 为同盘原配 6.12.110，既定 `SEATD_VTBOUND=1` 差异单独保留。
- `work/images/chromium-startup-trace-v1.img` 从 `chromium-m1-fcntl-unknown-v1.img` reflink 创建，只新增项目脚本 `trace-startup.sh`、`trace-export.sh`。制备清单：`artifacts/M1-003-perfetto-20260925/preparation/manifest.json`。
- 两轮使用同一工作盘、类别、缓冲大小、追踪时长及 30 秒间隔 monitor 截图。均采用 `-snapshot`，盘输入前后哈希相同，实际 QEMU 命令和机器状态保存在每轮 `metadata.json`。
- 追踪本身改变工作负载；仅各运行一次，不是正式五轮性能比较，不把 trace 的启动指标与此前无追踪的约 79 秒直接相减来计算开销。

| 运行 | trace 原始字节 / SHA-256 | 显示及退出 |
| --- | --- | --- |
| `m3-perfetto-startup-xkernel-20260925` | 8,027,388 / `ed0f208ff208313beffcebadcfbb2a7676f95d941e047664f7cd38646bd14d64` | 第 5 张 monitor 原图为完整原页；最终清理超时，runner=1、QEMU=0 |
| `m3-perfetto-startup-linux-20260925` | 11,435,781 / `d2fd56a37a37341896d4404ed49c6cf3fc55d2ffa9a814e592d0f81ae6c3c01f` | 第 1–5 张原图均匹配完整原页；guest/runner/QEMU=0 |

两个 `.pftrace` 均位于 `artifacts/runs/<run-id>/chromium-startup.pftrace`。完整页面原图 SHA-256 为 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`。x-kernel 的前 4 张为空白；截图只是粗阶段观察，不用 30 秒的帧序号冒充精确首帧时间。

## 时间线的新证据

下表是 trace 内同名 slice 的经过时间，**含等待/抢占、可能嵌套或并发，不能相加成 CPU 用时，也不代表独立重复测量**。

| 阶段/任务 | x-kernel | Linux |
| --- | ---: | ---: |
| 原始 `file:///opt/ict-testpages/index.html` 导航 slice | 60.981 秒 | 3.726 秒 |
| `Startup.FirstWebContents.NonEmptyPaint3` 内部指标 | 136.541 秒 | 13.919 秒 |
| 第一个资源完整性检查 worker 任务 | 46.275 秒 | 0.380 秒 |
| 该任务下 `ReadAtCurrentPos` 次数 | 3003 | 3003 |
| 这些读取区间的经过时间合计 | 26.549788 秒 | 0.132293 秒 |
| 其中最长一次读取区间 | 0.415987 秒 | 0.004133 秒 |

x-kernel 关键任务为 Browser PID 29 / TID 59 / slice 3037；Linux 为 Browser PID 835 / TID 864 / slice 44950。事件参数给出 `chrome/browser/resources_integrity.cc` / `CheckResourceIntegrity`，内部读取区间参数给出 `base/files/file_posix.cc` / `ReadAtCurrentPos`。

只读核对固定 Chromium 142 源码：该任务按页读取资源包并计算 SHA-256，是 `MayBlock + BEST_EFFORT` 的后台检查。因此：

- 可以确认同一类实际读取工作在本轮 x-kernel 下显著拖长，值得把 syscall/read、文件缓存/块 I/O、调度等待作为下一处测量边界。
- **不能认定后台检查就是首帧依赖或唯一根因**。Linux 的该检查在内部 NonEmptyPaint 指标之后开始，而 x-kernel 的检查与首帧之前的工作重叠；尚无证据证明前台在直接等待它完成。
- 另外观察到原页 Renderer PID 106 的 GPU channel 同步建立约 6.66 秒、HTML 解析结束相关任务约 7.30 秒、布局更新约 5.65 秒。它们同样需要拆分执行与等待，不能据此宣称 GPU 或 HTML 算法有缺陷。

可复查结果：x-kernel `perfetto-analysis-v2/`、Linux `perfetto-analysis/` 下的 `startup_and_navigation.csv`、`integrity_tasks.csv`、`integrity_reads.csv`；人工限定查询及来源源码在 `artifacts/M1-003-perfetto-20260925/`，对应只读 URL 和 SHA-256 在 `chromium-source/sources.json`。

## 覆盖与质量限制

两个 trace 均解析到 8 个有线程 slice 的进程，含 Browser、原页 Renderer、GPU、NetworkService、StorageService 和 TracingService。全部 11 组查询执行成功；SQL 导入成功不等于完整采集通过。

两边均有 `config_write_into_file_discard[0/1]` 和 `config_write_into_file_no_flush` 提示，来源为 Chromium 142 的默认追踪配置。Linux 另有 `flow_duplicate_id=1`；x-kernel 有 `track_event_parser_errors=11`、`traced_chunks_discarded=140`、`traced_patches_discarded=94` 等非零信息计数。原始 stats 完整保留，不将配置警告数量当作确切丢失事件数，也不宣称无损事件覆盖。

x-kernel 的 `tracing_started_ns` 约为 guest monotonic 67.862 秒，而回溯启动指标起点为 13.604 秒；Browser 的普通线程 slice 最早约 78.260 秒。Linux 服务约 16.625 秒开始，回溯启动指标起点为 8.309 秒。固定版本 `trace_startup.h` 将 `kStartupTracingTimeoutMs` 定义为 30 秒，`trace_startup.cc` 把它传给 startup tracing。服务接管晚于这个期限与 x-kernel 早期记录缺口相符，但本轮没有直接采集 startup session timeout 回调，因果仍标为推断。回溯记录的长 startup 指标不能填补早期函数/线程记录。

Trace 配置的 120 秒期限从服务会话阶段生效，x-kernel 包含后补指标的总时间范围约 176.67 秒，Linux 约 128.40 秒；不能用总 span 判断 120 秒采集失败。记录已包含 `all_data_source_flushed_ns`，但它也不能证明此前超时、退出进程或丢失数据已恢复。

## 线程 CPU 时间校准：当前不可用

Perfetto 显示 x-kernel 46.275 秒任务的 `thread_dur` 也约 46.277 秒。为避免据此误判全程占用 CPU，新增自编 C 探针 `tests/observe/thread-clock.c`：分别采样 `CLOCK_MONOTONIC` 和 `CLOCK_THREAD_CPUTIME_ID`，中间 `nanosleep(1s)`，打印三次原始差值。静态编译，部署到另一份 reflink 工作盘，不改变第三方程序。

| 样本 | x-kernel：wall / thread CPU | Linux：wall / thread CPU |
| --- | --- | --- |
| 0 | 1.000723088 / 1.000591392 秒 | 1.002635984 / 0.000365472 秒 |
| 1 | 1.000978928 / 1.000856240 秒 | 1.001038304 / 0.000153568 秒 |
| 2 | 1.000888336 / 1.000814800 秒 | 1.000683328 / 0.000161168 秒 |

结果确认：当前内核把睡眠计入线程 CPU 时间。探针的 `cpu > wall/2` 只是宽松诊断阈值，不是 POSIX 规定的精度界限。x-kernel 三次均触发、guest/runner=1、QEMU=0且正常关机；Linux 三次均未触发、三者=0。原始运行分别为 `m1-perfetto-clock-xkernel-20260925`、`m1-perfetto-clock-linux-20260925`，制备/编译证据在 `clock-calibration/`。

本轮因此**不使用 `thread_dur` 或 `dur-thread_dur` 判断 CPU / off-CPU 比例**。trace 原始值不篡改；分析器和使用说明明确警示。

先查当前上游后发现 [!828](https://gitee.com/openkylin/x-kernel/pulls/828)，关联 [IKHHF1](https://gitee.com/openkylin/x-kernel/issues/IKHHF1)，描述的正是睡眠误计 CPU 时间。API 返回 head `2d28af4ce9bd6d8407a77d55e6c3a94952fe7a00`、状态 merged。本轮只保存并核对元数据，**未获取候选代码、未适配、未独立验证或合入**；不能以其上游结果替代当前固定基线验证。另一个 IKIIZF 讨论并发退出快照，已与本故障区分。计账修复作为后续独立任务，不扩大本轮追踪接入。

## 验证和后续

- 宿主 29 项测试通过，含 4 个新 transfer 测试（原始二进制/无关串口输出、截断/丢片、损坏/重复/超限、旧证据不覆盖）；Python 编译检查、shell 语法、实际生成的会话脚本检查通过。
- x-kernel、Linux 真实 guest 导出均通过双层长度/哈希校验；各 11 组 Perfetto SQL 查询成功，网页实际显示原页 Renderer 的轨道。
- 未执行内核构建或内核功能回归，因为没有修改内核。没有进行 5 次性能比较，没有把追踪后的 136.541 秒当作此前 79 秒问题的新正式成绩。
- 完整备份：`artifacts/backups/perfetto-startup-20260925.tar.zst`；逐文件读回校验见 `perfetto-startup-20260925-verification.json`。原始输入盘、准确内核/debug ELF、两个 trace、串口/monitor 原图、校准 probe、SQL、工具/来源与 Git bundle 均保留。

下一处最小内核观测应围绕 **Browser 资源读取期间的 read 系统调用和调度切换**，记录进入/离开、阻塞/唤醒、实际运行区间，并与 unmap/分配器锁等待关联。先补这几个边界，修复/校准 CPU 时间后再使用 Perfetto 的 CPU 占用结论；不因一个长后台任务直接修改 Chromium 或关闭其完整性检查。
