# M1-003：历史启动 CPU trace 及口径纠正

2026-09-26 的分析只复用已有 trace，没有运行新 guest。**2026-09-27 复核撤回了“6.75 倍是总 CPU 下界”“多出的首帧时间主要是 CPU”“全谱系每操作成本放大”“纯算力受限”等结论。** 除覆盖和追踪扰动外，原分析器还存在窗口跨界、同名进程覆盖及追踪片段嵌套累计问题。原始 trace、SQL、CSV、JSON 均保留，不改写历史产物。

可保留的观察是：部分相同名称的 Chromium 任务及资源读取 scope 在旧 x-kernel trace 中明显更贵；这提示需要验证公共成本，但未证明所有 syscall 慢，也未建立首帧依赖链。当前版本的下一步是[启动关键路径预算](../analysis/startup-critical-path-20260927.md)，暂缓按 syscall CPU 排名继续优化。

## 输入身份与覆盖

| 项 | x-kernel | Linux 对照 |
| --- | --- | --- |
| trace | `artifacts/runs/m1-012-main-chromium-trace/chromium-startup.pftrace` | `artifacts/runs/m3-perfetto-startup-linux-20260925/chromium-startup.pftrace` |
| SHA-256 | `850570b52050076bcdc3c60bfd74c88561808fd78bac8539475f7f63e1248317` | `d2fd56a37a37341896d4404ed49c6cf3fc55d2ffa9a814e592d0f81ae6c3c01f` |
| 内核 | `604d846e655cd409c7c3eba8b76db821840b7a51`，已修复 CPU 计账、尚未优化 munmap | 原盘 Linux `6.12.110-0-lts` |
| 工作盘 | `work/images/m1-012-combined-trace.img` | `work/images/chromium-startup-trace-v1.img` |
| 工作盘 SHA-256 | `f6893e01c5f736a0dc90d35e7fc521504598aa802d6fe7b53719f27b3b264f3d` | 同左 |
| 调度片段 `sched_slices` | 0 | 0 |
| 所有 slice / 带 CPU 字段 | 72908 / 70800 | 88410 / 86456 |
| depth-0 slice / 带 CPU 字段 | 10817 / 9654 | 12066 / 11168 |

双方 QEMU 11.1.1、cortex-a76、TCG thread=multi、2 GiB/4 vCPU、原始 index、WAYLAND_DEBUG/stderr 条件相同，snapshot 盘前后 hash 不变，均正常关机。但日期、宿主状态不同、各一次、没有交错，宿主内存/swap 状态未在当次采样。这是诊断，不是正式性能基准；也不能与后来的 76 秒 profiler-on 运行拼成同一条时间线。

工具为官方 `trace_processor` v58.2（`work/tools/perfetto/trace_processor`），来源与 hash 见 `artifacts/M1-003-perfetto-20260925/perfetto-tool.json`。CPU 字段是 Chromium 上报的 `thread_dur`，不是用户/内核细分或完整调度记录。CPU 计账修复使长任务可区别运行与睡眠；短片段仍有越界，不能承诺逐事件精度。

## 仍可使用的阶段与资源读取线索

各自 Chromium startup 基准为零，单位秒；内部里程碑不等于 monitor 原页首帧：

| 指标 | x-kernel | Linux |
| --- | ---: | ---: |
| MainNavigationStart | 32.694 | 6.954 |
| MainNavigationFinished | 74.605 | 10.622 |
| BrowserWindow.FirstPaint | 83.449 | 11.621 |
| FirstWebContents.NonEmptyPaint3 | 102.193 | 13.919 |

原始证据是 `artifacts/M1-003-cpu-budget-20260926/` 两侧的 `startup_milestones.csv` 及 `compare.json` 中里程碑字段。Browser 普通事件首条相对 startup 基准晚约 46.279 秒，Linux 约 1.7 秒；缺口不表示线程此前没有执行。追踪服务晚接管与固定版本 30 秒 startup tracing 期限相符，但没有直接记录 timeout 回调，因果仍是推断，见 [Perfetto 历史记录](perfetto-startup-20260925.md)。

资源完整性检查的一组直接子 scope：

| ReadAtCurrentPos scope | x-kernel | Linux |
| --- | ---: | ---: |
| 次数 | 3003 | 3003 |
| 累计经过秒 | 16.023529 | 0.132293 |
| 累计上报线程 CPU 秒 | 6.108436 | 0.121108 |
| 最长单次经过 / CPU 毫秒 | 92.867 / 12.539 | 4.133 / 3.979 |

精确来源为两侧 `integrity_reads.csv` 的 task 6483 / 44950，scope 名为 `ReadAtCurrentPos`。同镜像、源代码位置和次数支持可比工作线索，但现有 SQL 没记录实际路径、读取字节、返回值或缓存状态，不能称为完全受控工作量；这些 scope 也不等同纯 read syscall 内核成本。

x-kernel 该任务约在 guest 67.365–95.395 秒，Browser FirstPaint 在 96.477112 秒；Linux 任务约 22.411–22.791 秒，已晚于 FirstPaint 19.930351 秒及 NonEmptyPaint 22.228693 秒。归档 Chromium 源码 `artifacts/M1-003-perfetto-20260925/chromium-source/chrome_browser_resources_integrity.cc` 显示它是异步 BEST_EFFORT 检查，完成回调记录 histogram。**没有前台等待它的证据，不能把它当作首帧必经阶段。** 经过时间减 CPU 只说明 scope 中存在未运行时间，不能区分 runnable、I/O、锁或定时器。

计账修复前 `31c8f270` 的另一轮同组读取为 26.549788 秒经过、约 26.917 秒上报 CPU；该版本的睡眠计账已由 C 探针证伪，CPU 列不能使用。它只补充说明读取区间变长在计账修复前就已存在。

## 原预算算法的具体问题

1. **起点筛选不等于窗口剪裁。** `scripts/analyze_trace_cpu_budget.py` 的 `window_process_cpu`、`window_thread_cpu` 按 `s.ts` 入窗后累计整个 `thread_dur`。x-kernel FirstPaint 窗口止于 96.477112 秒，已存 Browser CSV 却延伸到 103.433 秒；Linux 窗口止于 19.930351 秒，Browser/Renderer 延伸到 23.451/22.071 秒。窗口前开始的交叠片段还会漏计，不能把结果视为阶段 CPU 下界。
2. **同名进程被覆盖。** `per_process_window[process_name] = cpu` 使用名称作唯一键，多个 Renderer 只留下最后一项。Linux FirstPaint CSV 中 Renderer 为 2.718 + 0.308 秒，原 summary 只留 0.308；x-kernel nonempty 漏掉 12.690，Linux nonempty 漏掉 0.818。应按进程生命周期 ID/PID 分开后再求和；显示名称只用于标签。
3. **追踪开销口径重复。** `window_tracing_overhead` 没有 depth-0 限制；Browser PerfettoTrace 累计 3.588 秒，对应 depth-0 仅 1.794 秒。一个 TracingService 主线程的含嵌套累计 11.177 秒，大于整个服务 depth-0 的 6.423 秒。不能从 depth-0 预算中扣它；即使统一口径，专用线程也不包含所有事件记录、缓存和调度扰动。
4. **10 秒桶不是利用率。** `cpu_timeline` 把整个 slice CPU 放到起始所在桶；39.79 CPU 秒 / 40 秒容量不能证明那十秒接近饱和。
5. **层级与缺失不保证下界。** depth-0 只在各 track 内去掉嵌套，未核实同线程跨 track 的互斥性；缺失字段不表示零 CPU。两边部分观测值的比值也不能作为总 CPU 比值下界。
6. **异常计数不是误差上界。** `cpu_over_wall_bias.sql` 统计所有嵌套层的 `thread_dur > dur` 超额；它既可能重复，又看不到仍小于 wall 的虚高。x-kernel 约 2.24 秒的正超额总量、338 个 CPU 超过 wall 1 ms 以上的片段只能作为异常记录，不能除以 depth-0 总量后宣称系统性误差最多 1.3%。

为定位旧引用，保留原数值及纠正状态：

| 原算法/原解释 | 2026-09-27 复核 |
| --- | --- |
| FirstPaint CPU 95.651 / 14.172 秒，至少 6.75 倍 | 同名进程恢复后起点入窗完整片段为 95.651 / 16.890 秒；仍未剪裁，不能作为修正后的窗口 CPU |
| FirstPaint→NonEmptyPaint CPU 44.783 / 2.611 秒 | 同名进程恢复后为 57.473 / 3.429 秒；同样仍未剪裁 |
| 容量占用 28.7% / 30.5%，第二段 59.7% / 28.4% | 不是真实区间利用率，不能判断首帧是否算力受限 |
| 扣除 tracing 后 73.467 / 13.154 秒、5.59 倍 | 嵌套口径不一致，且不能消除追踪扰动；撤回 |
| 跨子系统同名任务每次高 5–22 倍 ⇒ 全局每操作成本放大 | 任务工作内容、覆盖及阶段不同；只能作为公共成本假设 |

上述问题不撤销 [M5-002](munmap-sparse-20260926.md) 独立保存的 syscall profiler 与 monitor 对照；两套分析工具、输入和终点不同。

## 当前使用边界与后续

旧 trace 可用于寻找长任务、应用内里程碑及进一步观测位置；不能解释当前 76 秒首帧的完整 CPU/等待比例。既没有线程调度状态，也没有完整 IPC/等待依赖，不能根据较高累计 CPU 排除等待关键路径。BrowserWindow FirstPaint 更不能替代原始页面呈现。

当前代码中的分析器本轮未修复。以下入口只用于重现原算法，输出必须注明上述缺陷：

```sh
python3 scripts/analyze_trace_cpu_budget.py \
  --run artifacts/runs/m1-012-main-chromium-trace \
  --output <新的分析目录>
```

原始 SQL/CSV/log/summary、`compare.json`、`build_comparison.py`、校准异常及计账修复前交叉证据仍在 `artifacts/M1-003-cpu-budget-20260926/`。本轮只核对这些文件与脚本，未重新查询 pftrace、重跑 guest 或重新评估追踪开销。下一任务见 [M5-004](../tasks/M5-004.md)：用当前版本串行 Linux/x-kernel 对照，把启动里程碑、运行/就绪/阻塞状态及依赖边关联，未知部分明确保留。
