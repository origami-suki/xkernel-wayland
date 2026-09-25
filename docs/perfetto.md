# Chromium 启动时间线（M1-003）

使用 Chromium 142 自带 startup tracing，在宿主用 Perfetto 打开与查询。内核仍为 `31c8f270`；不安装 guest tracer，不修改 Chromium/Weston/运行库或官方测试页。追踪增加进程、事件记录与 I/O，所有此类运行均是诊断样本，不能用于首帧性能排名或补丁收益比较。

## 采集和导出

先创建新的工作盘，只加入本项目的两个 shell 脚本；脚本核对原会话版本、记录全部命令/哈希、检查 ext4，拒绝覆盖已有盘。下例各名字须未使用：

```sh
python3 scripts/prepare_startup_trace.py \
  --output work/images/chromium-startup-trace-v1.img \
  --evidence artifacts/my-trace-preparation

python3 scripts/run_guest.py --run-id my-chromium-trace \
  --bundle artifacts/M1-011-fcntl-unknown-20260925/clean-bundle \
  --disk work/images/chromium-startup-trace-v1.img \
  --guest-commands tests/chromium/run-startup-trace.sh --timeout 360

python3 scripts/extract_startup_trace.py --run artifacts/runs/my-chromium-trace
```

`--bundle` 必须与当前内核 HEAD 对应。工作盘始终用 QEMU `-snapshot`；原始盘和工作盘输入哈希在运行前后核对。正式显示证据为 monitor PPM，当前入口每 30 秒一帧、共 5 帧。

固定类别见 `tests/chromium/trace-startup.sh`，包含 startup/navigation/loading、toplevel/base、renderer、GPU/Viz 和 IPC。使用 64 MiB 主缓冲与 120 秒追踪期限；期限与服务启动相关，不能理解为 guest launch 后恰好 120 秒。观察 150 秒后，额外最多等待约 60 秒检查文件大小稳定，快照复制、gzip、带标记的 base64 串口导出均在观察阶段之后执行。大小稳定只表示观察结果，不证明文件已完整结束。

宿主分别验证 gzip payload 和解压 trace 的长度、SHA-256，解压输出上限 64 MiB；拒绝截断、重复、无效编码、超限与覆盖旧产物。输出为 run 目录的 `chromium-startup.pftrace` 和 `trace-export.json`。脚本不依赖 browser 正常清理才导出；因此 **trace 导出成功、页面显示成功与 runner 正常退出是三个独立结论**。

## Perfetto 查看与查询

官方宿主工具入口为 [trace_processor](https://get.perfetto.dev/trace_processor)。本次使用官方 bootstrap 校验下载的 **v58.2**，工具及二进制来源/哈希保存在 `artifacts/M1-003-perfetto-20260925/perfetto-tool.json`；脚本路径为 `work/tools/perfetto/trace_processor`。它只在宿主执行。

```sh
python3 scripts/analyze_startup_trace.py --run artifacts/runs/my-chromium-trace
```

输出 `perfetto-analysis/` 下的原始 SQL、CSV、解析日志与 `summary.json`，包括进程/线程覆盖、最长 slice、startup/navigation 阶段、资源完整性检查任务及其读取调用、非零错误/数据丢失提示。对同一 trace 执行新版查询时，用 `--output` 指定另一个新目录，不覆盖旧分析。

在 [Perfetto UI](https://ui.perfetto.dev/) 选择 **Open trace file**，打开 `.pftrace`。本轮已实际验证网页加载该文件并显示 Browser、原页 Renderer、GPU、NetworkService 等轨道；文件在本地打开，没有使用分享/上传入口。浏览器本地缓存 URL 不是跨设备分享链接，应交付实际 `.pftrace` 文件。

## 解释边界

- slice 的 `dur` 是经过时间，可能包含抢占、阻塞和等待，不等同 CPU 用时；父子 slice 与并发任务的时长不能直接相加。
- **当前 x-kernel 的 `thread_dur` 不可信。** 自编 `tests/observe/thread-clock.c` 的同盘对照证明，`CLOCK_THREAD_CPUTIME_ID` 把 1 秒睡眠计成约 1 秒 CPU 时间，Linux 仅计约 0.15–0.37 毫秒。本轮不使用该字段区分 CPU 与等待，也不以 `dur-thread_dur` 推断 off-CPU 时间。原始 trace 保持不变，校准证据见本轮记录。
- 默认启动追踪生成的 DISCARD/流式写入、未设周期 flush 配置会被新版 Perfetto 标记为风险。这些提示不能直接解释成“确实丢了几个事件”，也不能忽略；同时查看原始 buffer/producer/解析计数与实际覆盖范围。
- 固定 Chromium 142 源码的 `kStartupTracingTimeoutMs` 为 **30 秒**。x-kernel 下服务接管较晚时，早期 startup session 可能已经结束。本轮 Browser 的普通线程事件明显晚于回溯记录的启动指标，不能把之前的空白轨道解释为线程没有执行，也不声称取得从 exec 开始的完整调用记录。
- Chromium 的 `NonEmptyPaint` 是应用内部指标，不能代替 monitor 原图验证。
- 尚未将 Chromium、guest 内核和宿主截图时钟对齐；不能直接把三个来源的时间戳拼接后推断因果。
- 当前入口仅采集 Chromium 内置事件。x-kernel 的调度、unmap 和锁等待尚未加入同一 trace；后续应优先给已发现的长调用补少量内核阶段事件，并检查记录开销和丢事件情况。

本轮结果与 Linux 对照见 [2026-09-25 启动追踪记录](measurements/perfetto-startup-20260925.md)。

参考：[Perfetto 支持的数据格式](https://perfetto.dev/docs/getting-started/other-formats)、[Chromium 142 追踪参数](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.59/components/tracing/common/tracing_switches.cc)、[30 秒启动追踪期限](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.59/services/tracing/public/cpp/trace_startup.h)。
