# Buddy 物理页释放优化：五轮首帧对照

**修复已合入，功能复核通过；整体性能收益尚不稳定。** 独立五轮的中位观测上界下降约 9%，但后续主工作区单轮为 113.020–114.023 秒。两组分别保留，不能把独立组收益直接当作稳定结论。2026-09-26 用户要求不再复跑，第二轮主区测量未启动。

冻结内核 `31c8f270`，候选 `f9259cd5`；QEMU 11.1.1/cortex-a76/TCG/2 GiB/4 vCPU、原盘/页面、Wayland、no-sandbox、IPC120 和日志不变。盘独立 reflink + snapshot；每轮先由 runner 全盘 SHA256 读取预热，不清理 host cache；恢复相同 guest profile 初始状态。串行，无 GDB/trace、无并发编译。

完整原页以 monitor PPM 哈希 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287` 判定。相同一秒采样器记录实际时间区间；部分正文和窗口壳不是完整页面。

| run-id | 完整原页观测区间 (秒) | runner 退出码 |
| --- | --- | ---: |
| m5-buddy-baseline-01 | 92.019–93.020 | 1 |
| m5-buddy-baseline-02 | 87.006–88.029 | 1 |
| m5-buddy-baseline-03 | 88.009–89.011 | 1 |
| m5-buddy-baseline-04 | 90.014–91.013 | 1 |
| m5-buddy-baseline-05 | 88.016–89.018 | 1 |
| m5-buddy-candidate-01 | 79.020–80.024 | 0 |
| m5-buddy-candidate-02 | 79.009–80.031 | 0 |
| m5-buddy-candidate-03 | 80.020–81.021 | 0 |
| m5-buddy-candidate-04 | 85.009–86.009 | 0 |
| m5-buddy-candidate-05 | 86.007–87.026 | 0 |

中位区间：基线 88.016–89.018 秒，候选 80.020–81.021 秒；观测上界范围分别 88.029–93.020、80.024–87.026 秒。上界中位数约减少 7.997 秒（8.98%）。各 5/5 达到原页完整哈希，151 帧/轮且无漏槽位。基线五轮清理超时，候选五轮 Chromium/Weston/seatd 退出码均 0，PID1 正常关机。

这是本次同条件五轮样本，旧的单次 79 秒未纳入；不外推为所有负载的收益，不证明剩余延迟的根因。精确 IKHSF8 x86_64 fs_racer 场景仍缺少环境。详细版本/适配/失败日志/限制见 `artifacts/M5-001-buddy-20260925/independent/REPORT.md`，逐帧时间和统计为 `performance-results.json` 及 `runs/*/metadata.json`。

复跑使用 `scripts/run_guest.py --run-id <新名> --disk <work/images 独立盘> --bundle <对应当前内核 HEAD 的 bundle> --guest-commands tests/chromium/measure-first-frame.sh --sample-every-second --timeout 240`。自编压力探针源码为 `tests/allocator/pressure.c`；通过 `pressure-build.json` 和 `inject-pressure.debugfs` 中的命令部署到新工作副本后运行 `tests/allocator/regression.sh`，不改性能盘或原 Assets。

## 2026-09-26 主工作区合入与复核

主 Agent 复核位图边界、链表所有权、SlabHeap 适配、容量回归、十轮原始 metadata/首个完整帧及前一帧哈希后，以 `cherry-pick -x` 引入为 `1b8b98a10aa4897819528b0991225967ad96b5b9`。与独立受测提交的 tree 均为 `46e7ba88f43179500a1c1a5a81113fe32e8a13ce`；没有冲突或额外源码变化。主区重新执行 `scripts/build_kernel.sh`，配置与冻结配置字节一致，构建 manifest 标明新提交且 clean；准确 ELF/BIN/debug ELF 保存到 `integration/main-bundle/`。

用独立 reflink 盘、`-snapshot` 串行执行以下复核，三个运行的 guest/runner/QEMU 退出码均为 0，输入盘前后哈希一致：

| run-id | 实际结果 |
| --- | --- |
| `m5-buddy-main-regression-20260926` | 4 进程内存压力及 M1/M2 接口回归通过，出现 `BUDDY_PRESSURE_OK` 和 `M5_BUDDY_M1_M2_REGRESSION_OK` |
| `m5-buddy-main-weston-20260926` | 两张 monitor 原图均已目视确认，图案动态变化，会话正常退出 |
| `m5-buddy-main-chromium-20260926` | 151 张 monitor 原图；完整原页哈希匹配，观测区间 **113.020–114.023 秒**；Chromium/Weston/seatd 退出码均 0，无 forced-kill |

113 秒的前一帧已经有大部分正文，但下拉框文字和按钮尚未齐全；114 秒首张匹配完整原页。仍沿用严格完整帧口径，不把更早的不完整页面当成本轮终点。该单轮明显慢于独立区五轮，不能丢弃，也不能仅凭它认定补丁导致回退。源码树、内核配置、输入盘、脚本哈希及 QEMU 设备/运行参数一致；已保留主机状态，尚未确定差异来自宿主负载、执行时序或其它因素。按用户指示停止追加测量，不继续修改其它路径。

主区原始命令、metadata、全部原图、日志与内容校验位于 `artifacts/M5-001-buddy-20260925/integration/`；`run_main_validation.py` 只记录已经完成的三轮，`main-validation.json` 保存结果。独立区 `backup.tar.zst` 已逐项解压核验 1848 个文件；主区另有同名备份及校验记录，通过 `independent-evidence-reference.json` 关联已归档输入盘。备份在同一主机，不声称异地容灾。
