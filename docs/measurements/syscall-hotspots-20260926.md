# 首轮 syscall 热点扫描（2026-09-26）

**首要优化候选为 `munmap`：1026 次调用消耗 38.121649 秒 CPU，占本轮已统计 syscall CPU 的 54.26%。** 这是已完成的单轮启动诊断；根因尚未定位，也没有实现或宣称优化收益。用户决定停止剩余统计工具验证，整理证据后逐项优化，下一步见 [M5-002](../tasks/M5-002.md)。

## 输入与窗口

- 集成起点 `af1e05db6264242cb9dc6a97ec3572c60ed6e390`，内核起点 `604d846e655cd409c7c3eba8b76db821840b7a51`，叠加本次尚未提交的 profiler。准确源码冻结、逐文件哈希在 `artifacts/M1-013-syscall-profile-20260926/freeze/manifest.json` 及两个源码归档中，不能仅用 base 提交号重现。
- 普通内核：`artifacts/M1-013-syscall-profile-20260926/bundle-v1`，含准确 ELF/BIN/debug ELF；工作盘：`work/images/m1-013-profile-v2.img`，来源和读回校验在 `preparation-v2/manifest.json`。后续校准盘 `m1-013-profile-final.img` 只补充测试，不冒充本轮实际输入。
- QEMU 11.1.1、AArch64 cortex-a76、TCG thread=multi、2 GiB、4 vCPU；原 Chromium、Weston、运行库及原始 index；不启用 Chromium tracing。
- 启动窗口从浏览器启动前开始（Weston 已 ready），覆盖所有进程当时的 syscall；到宿主首次匹配原页 monitor PPM 后通知 guest 停止。它不包含 Weston 自身初始化。
- 首帧宿主观察区间 **80.019–81.011 秒**；guest 采集窗口约 **81.046 秒**。两种时钟未对齐，窗口含通知/控制延迟，不能把 guest stop 当精确呈现时刻。
- 停止后保存快照并枚举进程，再开启约 30 秒稳定窗口；两份快照的串口传输均通过 SHA-256 校验。
- 原页 monitor SHA-256 为 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`；Chromium/Weston/seatd 退出均 0，guest/QEMU/runner 均 0，正常关机。

## 启动 CPU 排行

百分比的分母是 **70.262473 秒已记录 syscall CPU**，不是总启动经过时间、整个 guest CPU 或首帧可缩短比例。

| syscall | 完成次数 | CPU 秒 | syscall CPU 占比 | 返回错误次数 |
| --- | ---: | ---: | ---: | ---: |
| `munmap` | 1026 | 38.121649 | 54.26% | 0 |
| `openat` | 11614 | 7.538648 | 10.73% | 6576 |
| `read` | 5131 | 2.927934 | 4.17% | 2 |
| `clone` | 138 | 2.329409 | 3.32% | 0 |
| `execve` | 17 | 2.045553 | 2.91% | 3 |
| `epoll_pwait` | 5388 | 1.751360 | 2.49% | 11 |
| `futex` | 9191 | 1.590866 | 2.26% | 968 |
| `fdatasync` | 312 | 1.373735 | 1.96% | 0 |
| `mprotect` | 5565 | 1.186197 | 1.69% | 0 |
| `fstatat` | 1222 | 0.932250 | 1.33% | 414 |
| `close` | 5459 | 0.902534 | 1.28% | 0 |
| `clock_gettime` | 107191 | 0.887164 | 1.26% | 0 |
| `sendto` | 2700 | 0.813030 | 1.16% | 67 |
| `mmap` | 2906 | 0.782121 | 1.11% | 0 |
| `ftruncate` | 207 | 0.751703 | 1.07% | 0 |
| `mkdirat` | 157 | 0.743077 | 1.06% | 65 |
| `ppoll` | 1404 | 0.728466 | 1.04% | 4 |
| `pwrite64` | 1127 | 0.572581 | 0.81% | 0 |
| `getdents64` | 575 | 0.572085 | 0.81% | 0 |
| `faccessat` | 701 | 0.555025 | 0.79% | 417 |

错误计数包含 ENOENT、EAGAIN 等预期分支，不能直接当作兼容性故障。`clock_gettime` 调用最多但仅占 1.26% CPU，因此不按调用量优先优化。

## munmap 进程分布

| PID | 完成次数 | munmap CPU 秒 | 当时可确认的信息 |
| --- | ---: | ---: | --- |
| 114 | 271 | 11.808572 | cmdline 仍显示 zygote；具体角色待定位 |
| 96 | 271 | 11.537651 | cmdline 仍显示 zygote；具体角色待定位 |
| 113 | 269 | 11.460207 | cmdline 仍显示 zygote；具体角色待定位 |
| 95 | 77 | 3.258861 | 停止后进程枚举未见，不据此猜测角色 |
| 29 | 105 | 0.046096 | 主 browser，原页 URL 与启动 PID 一致 |
| 26 | 22 | 0.006159 | Weston |

开销主要集中在几个子进程，主 browser 的同名调用开销很小。下一步应取这些长调用的映射范围、页数、映射类型和内部阶段计时；不能把继承的 zygote cmdline 当作 renderer/GPU 身份证明，也不能仅凭本表指定页表、TLB 或分配器为根因。

## 经过时间与稳定窗口

- 启动 `futex` 经过时间累计约 1500.55 秒，CPU 仅 1.59 秒；`epoll_pwait` 经过时间累计约 633.98 秒，CPU 仅 1.75 秒。这些是多个线程重叠的调用区间，不是首帧等待了这些秒数。
- 启动 started=185682、completed=185598、unfinished=84；稳定窗口 started=34298、completed=34258、unfinished=40。两窗口均 dropped=0、CPU>elapsed 记录=0。
- 稳定窗口 syscall CPU 总计 1.138302 秒，`munmap` 仅 23 次/0.015593 秒 CPU；本轮候选优先针对启动。
- 未完成、不返回、跨窗口调用不计入耗时；稳定窗口开始前已阻塞的调用也未覆盖。syscall 外的用户缺页、中断独立工作、内核线程、用户态计算均不能由此表完整解释。

## 验证状态与用户收束

- 主区普通 build、clippy/hygiene、固定 nightly fmt、34 项宿主测试通过。最终 guest profiler 校准 **14/14**、旧 CPU 回归 **13/13** 通过，含并发、错误、权限、睡眠/阻塞和真实跨窗口旧调用返回；thread CPU timer 仍明确 UNSUPPORTED。
- 原计划的独立完整验收、五轮开启/五轮关闭统计对照已按用户要求停止。独立冻结/盘 hash、普通 build、34 项 host 测试、脚本语法、C 编译部署一致性通过；clippy 中断，独立 fmt/内核单测/guest 回归未执行，**0 on / 0 off**。停止记录见 `artifacts/M1-013-syscall-profile-20260926/independent/STOPPED.md`。
- **统计开销未完成定量对照，热点的重复稳定性未测。** 宿主未隔离，本轮 QEMU VmSwap 采样为 0–57588 KiB。当前数据用于选择第一条优化路径，不作性能成绩或补丁收益。
- 组委会启动同样在一分钟多的反馈来自用户；配置/口径未在本轮核对，不当作严格等价基准。

## 复查与证据

```sh
python3 scripts/analyze_syscall_profile.py \
  --run artifacts/runs/m1-013-chromium-on-v1 \
  --output artifacts/M1-013-syscall-profile-20260926/reanalysis-NEW
```

- 原始完整运行：`artifacts/runs/m1-013-chromium-on-v1/`（命令、metadata、serial、monitor、逐秒 PPM、host 样本、内核产物引用）。
- 首轮解析：同目录 `syscall-analysis/`，含原始聚合表、完整 syscall/PID CSV、CPU/经过时间/调用量 Top 20 和 `summary.json`。
- 主区最终校准：`artifacts/runs/m1-013-calibration-final/`；首次包装失败保留在 `m1-013-calibration-v1/`，补齐后通过在 `m1-013-calibration-v2/`。
- 源码、配置、制备、build/clippy/fmt/host 日志：`artifacts/M1-013-syscall-profile-20260926/`。
- 操作入口与统计语义：[syscall-profile](../syscall-profile.md)；实现状态：[M1-013](../tasks/M1-013.md)。
