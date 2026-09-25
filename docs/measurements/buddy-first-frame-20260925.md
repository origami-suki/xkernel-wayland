# Buddy 物理页释放优化：五轮首帧对照

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
