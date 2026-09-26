# M5-002：稀疏 munmap 遍历优化

本项将私有映射解除过程从“每个虚拟页重新查表”改为“按层级跳过不存在的子树、在已分配叶表内顺序处理”。独立验收通过：启动窗口 munmap CPU 五轮中位数 **37.654816 → 0.274485 秒（-99.27%）**；原页首帧观察中位区间 **80.018–81.009 → 76.005–77.020 秒**，约提前 4 秒。CPU 总量减少不等于首帧等量缩短，差额原因见下文「CPU 减少远大于首帧减少的原因」一节。

## 来源与准确受测状态

- 内核提交：`238349ca5e2bcc2a0e87a7fa18e3758432172b3f`，基底 `604d846e655cd409c7c3eba8b76db821840b7a51`；两侧均保留冻结的 M1-013 未提交 profiler。该依赖未被混入优化提交，也未把其取消的开关开销验收写成完成。
- 独立受测六文件补丁 SHA256：`eb9ab9e8a41f7b85c7cb0bbc7db62b6624258ab8ea15319bf97c2dc477636adc`。主提交 diff 与该补丁完全一致，全部内核文件与独立冻结源逐字节一致，见 `artifacts/M5-002-munmap-20260926/main-source-equivalence.json`。
- 应用输入：`work/images/m5-002-munmap-v2.img`，SHA256 `3e899ed3a0dd96e7ed74a8c205235f8415ddd3de40386f6a3e47990b96e529b9`；独立运行使用对应 reflink 副本及 snapshot，前后 hash 未变。原始 Assets、Chromium、Weston、运行库和官方页面未修改。
- Linux 参考：官方 v7.0，提交 `028ef9c96e96197026887c0f092424679298aae8` 的 [zap_*_range](https://github.com/torvalds/linux/blob/028ef9c96e96197026887c0f092424679298aae8/mm/memory.c)。在当前 Rust 页表抽象内独立实现，没有移植整套 Linux MM。
- 保留既有地址空间锁、统一 backing 页大小、对象 detach → PTE 清除 → TLB finish → frame release 顺序。空洞可跳过，非法范围和不匹配叶大小报错；中途错误保留已清前缀的 pending flush 与 backing ownership。没有改 VMA retain、对象索引、TLB 阈值、分配器或中间页表回收。

## 根因证据

未优化 guest 探针中，即便完全不触碰映射，munmap CPU 仍随虚拟范围增长。Chromium 的 96 个有界慢调用样本中，backend 占记录到的原路径阶段 CPU 约 99.55%；典型范围接近 4 GiB，VMA metadata 更新通常约 0.1 ms。

第二轮记录的 48 个 ≥1 GiB 私有范围均 `present=0`、`detached_empty=true`、`released=0`。接近 4 GiB 的反复 munmap 仍扫描约百万个虚拟页；共用 helper 还捕获 16–32 GiB 的其他清理，不能把这些全部计入 munmap syscall。两轮均正确显示原页、正常关机。临时诊断包含计时/额外计数/有界打印，已从最终代码移除，其耗时不参与正式比较。

原始诊断：`artifacts/runs/m5-002-chromium-diagnostic-v1/`、`...-v2/`；补丁、准确 ELF/BIN、字段解释和摘要在 `artifacts/M5-002-munmap-20260926/diagnostic-*`。

## 独立功能验收

独立 worktree、构建输出和测试盘从准确冻结状态生成，生产代码只差六文件补丁。

- 普通 build、固定 nightly fmt、clippy（含 deps/header hygiene）通过。
- AArch64 guest 单测 **88/88**：page_table 17、memspace 52、anon 10、filemap 9；全部 7 项新测试实际执行，包含确定性访问数、边界/空洞、dense flush、大页、错误前缀和三层/高地址。
- 前后 C 探针各 **20/20**，含部分/跨空洞解除、边界外数据保留、同址重映射、fork/COW、私有文件、非法参数及旧地址访问 SIGSEGV。
- allocator/M1/M2、MADV_DONTNEED 零页 refault/两端保留/fork COW、Weston 动态显示和正常退出通过。
- 应用十轮均匹配原页 monitor SHA256 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`，全部 guest/QEMU/runner=0；统计 dropped=0、quality_ok=true，没有剔除样本。

报告：`artifacts/M5-002-munmap-20260926/independent/REPORT.md`；数据：同目录 `metrics.json`、`unit-results.json`、`functional-results.json`、`review.md`。

## 同配置五轮结果

按 B1/C1 … B5/C5 串行运行；QEMU 11.1.1、cortex-a76、TCG thread=multi、2 GiB/4 vCPU、WARN、相同 rootfs/原始 index、no-sandbox、IPC 120 秒，两侧均 profiler on，无 GDB/额外 trace，不并发编译或 QEMU。每次从同一盘快照启动，宿主缓存不主动清空；保持同一预热和交替策略。

| 轮次 | 优化前 munmap CPU s | 优化后 munmap CPU s | 优化前首帧区间 s | 优化后首帧区间 s |
| --- | ---: | ---: | --- | --- |
| 1 | 37.262771 | 0.261816 | 83.008–84.020 | 78.008–79.027 |
| 2 | 45.918911 | 0.279257 | 82.003–83.017 | 81.005–82.022 |
| 3 | 37.654816 | 0.312465 | 79.012–80.024 | 75.013–76.029 |
| 4 | 36.700697 | 0.273253 | 78.010–79.025 | 76.005–77.020 |
| 5 | 37.722666 | 0.274485 | 80.018–81.009 | 76.002–77.017 |

- munmap CPU 中位数 [min, max]：37.654816 [36.700697, 45.918911] → 0.274485 [0.261816, 0.312465] 秒。
- 首帧各样本端点总体范围：78.010–84.020 → 75.013–82.022 秒，存在重叠，不声称每轮均明显改善。
- 已统计 syscall 总 CPU 中位数：69.944026 → 33.271652 秒。munmap 完成次数中位数 1027 → 1223；应用执行和进程布局会变化，不能视为逐次同一调用配对。
- 独立微基准（每 case 五次）CPU 中位数：4 GiB 空映射 35.687 → 0.070 ms；4 GiB 固定触碰 64 页 40.282 → 0.918 ms；dense 16 MiB 18.831 → 18.469 ms。所有样本在 `micro-summary.json`，不与宿主 Linux 时间混算。

## CPU 减少远大于首帧减少的原因（五轮后的追补分析）

37.38 秒 CPU 减少只换来约 4 秒首帧，总 CPU 与首帧经过时间不是同一指标，现有数据可描述 CPU 分布，但不足以单独证明关键路径。以下由 `scripts/analyze_munmap_parallelism.py` 从同一批归档样本复算，修正后的产物 `artifacts/commit-cleanup-20260926/munmap-summary.json`（原 `followup-parallelism/summary.json` 保留为历史产物），不改动任何已验收结论。

**开销集中在其他进程，主 browser 自身占比较低。** 按每轮 munmap CPU 的消耗名次取中位数（重负载子进程 PID 每轮漂移，按名次聚合以免串角色），单位秒：

| 变体 | 第 1 | 第 2 | 第 3 | 第 4 | 前四合计占全部 munmap | 主 browser (pid 29) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 优化前 | 11.643 | 11.483 | 11.237 | 3.251 | 99.8% | 0.049 |
| 优化后 | 0.068 | 0.059 | 0.050 | 0.048 | 80.9% | 0.062 |

单次均值从约 42–44 ms 降到约 0.15–0.22 ms。优化前主 browser 只承担全部 munmap CPU 的约 0.13%，且这四个重负载进程在首帧之后仍存活并进入稳定窗口，但存活时间重叠不等于其工作不在 browser 的依赖链上。

**完整采样窗口的平均占用低于四核满载。** 对每轮 `profile-host-samples.jsonl` 取 QEMU 进程宿主 CPU 时间与墙钟之比（TCG 下繁忙 vCPU 约等于占满一个宿主核）：

| 变体 | 宿主 CPU 秒 | 墙钟秒 | 平均繁忙 vCPU | 占 4 vCPU |
| --- | ---: | ---: | ---: | ---: |
| 优化前 | 279.4 | 117.0 | 2.39 | 60% |
| 优化后 | 234.5 | 112.0 | 2.09 | 52% |

宿主为 16 线程、采样时 load 约 4；这些汇总值不能排除短时宿主竞争，也不能证明 guest 启动关键区间始终有空闲。两组宿主 CPU 中位数相差约 44.9 秒，与 munmap CPU 减少方向一致，但统计范围不同，不能据此进行精确因果归因。

**容量归一化与时延。** 37.38 ÷ 4 vCPU = 9.35 秒仅表示按四核满载折算的 CPU 容量时间，不是首帧收益上限或预期值。实测首帧中位提前约 4.013 秒；五对配对全部改善（+5.0、+1.0、+4.0、+2.0、+4.0 秒，中位 +4.0），方向一致，但两组区间重叠，仍不声称每轮明显改善。确认差额原因还需要覆盖启动关键区间的调度和跨进程依赖证据。

**边界。** 主 browser 取 pid 29：该 PID 在十轮中九轮为总 CPU 最高进程（baseline-2 被一个 munmap 重负载子进程超过），且与 M1-013 热点表中带原始页 URL 的 PID 一致，`--browser-pid` 可改。宿主 CPU 比值含 QEMU 的非 vCPU 线程，是利用率近似而非精确占用。该结论与首帧约一秒采样的分辨率限制叠加，不据此把 4 秒当精确值。

## 测量边界

首帧是从浏览器启动前标记到首次匹配 monitor PPM 的宿主观察区间，约一秒采样，不是 guest 精确呈现时间。CPU 是窗口内完成的 dispatcher 调用，不含全部用户态/缺页/后台工作，多个线程的 CPU 总和不是关键路径经过时间；上一节给出该差额在这组样本中的实际大小。

C2/C3 的 QEMU VmSwap 峰值分别 324/1376 KiB，其余八轮为 0；所有样本保留。QEMU RSS 峰值中位数 1,977,244 → 2,012,920 KiB，不能当作 guest 浏览器内存。宿主后台噪声、swap 对延迟的影响未单独量化，因此约 4 秒是这组样本的结果，不是无噪声保证。

这是相同 profiler 开启条件下的 before/after，未重做用户取消的统计器五开五关校准。原分析器的 `performance_sample_valid=false` 保留，不将本组结果称为无统计器、官方评分或完整性能验收。实际运行架构只有 AArch64，三层页表只是模拟 metadata 单测。

## 复现与保留

提交后，主工作区的 probe/M1/M2/Weston 和原页显示/正常退出再次通过。为满足集成 hook 的干净内核要求，同时保留主区既有未提交 profiler，另在干净 worktree 构建 `238349ca`（`git-dirty=false`），相同功能回归及 profiler off 的原页会话均通过。后者只是交付版功能复核，不加入上述五轮数据，也不算统计器开销校准；证据为 `main-validation.json`、`clean-validation.json` 及对应 `artifacts/runs/m5-002-{main,clean}-*`。

准备新的工作盘：

```sh
python3 scripts/prepare_munmap_tests.py --base work/images/m1-013-profile-final.img \
  --output work/images/NEW-munmap.img --evidence artifacts/NEW-munmap-preparation
```

用既有 `run_guest.py` 执行 `/opt/ict-tests/munmap-sparse check` 和 `bench`；用冻结的 `run_syscall_profile.py --profile-mode on` 跑两侧应用，再以 `analyze_syscall_profile.py` 解析。M1-013 工具仍为既有未提交依赖，准确版本已随 `final-freeze/integration-source.tar` 保存，不能只凭本项 Git commit 重建统计环境。

复算 CPU 与首帧差额（只读归档，不构建、不启动）：

```sh
python3 scripts/analyze_munmap_parallelism.py \
  --output artifacts/M5-002-munmap-20260926/followup-parallelism/summary.json
```

额外 MADV_DONTNEED 用例保存在 `tests/mm/madvise-neighbours.c`；受测编译、部署和完整回归命令见 `independent/scripts/`、`application-commands.json`，其 C 源码与独立受测版本一致。

源状态/配置/输入 hash：`freeze/`、`final-freeze/`、`preparation-v2/`；独立全部应用命令、原始串口、逐秒 monitor PPM、宿主采样和准确内核产物在 `independent/runs/` 及 `*-bundle/`。主提交后复核与备份结果索引见 [M5-002 任务卡](../tasks/M5-002.md)。

## 整理提交复核

2026-09-26：修正追补脚本将容量归一化误作首帧收益上限的推断；从原始十轮归档复算，输出保存在 `artifacts/commit-cleanup-20260926/munmap-summary.json` 和 `munmap-analysis.log`。原始实验、历史汇总与五轮验收结论未覆盖。
