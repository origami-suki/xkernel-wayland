# M5-003：openat 路径成本定位

2026-09-27 后续调整：以下是已完成的 CPU 热点定位记录；实际创建路径深挖暂缓，优先 [M5-004 启动预算](../tasks/M5-004.md)。尚无证据把 openat 归为首帧关键路径；下文建议保留为本轮定位结束时的历史结论。

当前结论是 **Nameidata 中间路径遍历与末组件查找/创建需要优先定位**，不是已完成生产优化。两轮隔离诊断都正常显示原始 index、清理并关机；独立 reviewer 从原始日志重算后确认单次观察证据通过。

## 选择依据与固定条件

[优化后五轮复扫](syscall-hotspots-post-munmap-20260927.md) 中 openat 始终第一，CPU 中位 8.068149 秒。原始候选源码与当前内核 `855a8c3c` 逐文件一致；历史样本复算没有启动 QEMU。

两轮新诊断均在独立 `work/m5-003-openat-kernel` 上从该内核添加临时插桩，主内核未修改。使用 AArch64/cortex-a76、QEMU 11.1.1、TCG thread=multi、2 GiB/4 vCPU、WARN、相同原始页面和既有用户态、no-sandbox 与 IPC 120 秒。构建 jobs=2，完成后才运行 QEMU；每轮启动前确认全机没有其他 QEMU。

## 第一轮：外层阶段与成功/失败

| 阶段 | 次数 | CPU 秒 |
| --- | ---: | ---: |
| 外层 openat | 11734 | 7.791547 |
| 用户路径复制 | 11734 | 0.191401 |
| with_fs_at + open_path | 11734 | 7.012876 |
| FD 安装 | 5046 | 0.098933 |
| 未分段余量 | — | 0.488337 |

路径打开占外层约 90.0%。在路径打开阶段，成功调用 CPU 5.042883 秒（71.91%），ENOTDIR 1.044704 秒，ENOENT 0.925289 秒。因此不按错误调用的次数占比优先优化负查找；用户路径复制和 FD 安装也不是此轮主导成本。

成功 `/proc` 类别为 1.720931 秒，成功 `/root` 或 `/home` 为 1.616340 秒，成功 `/usr/share` 为 1.070757 秒。只记录路径类别，未保存完整路径，不能声称某个具体文件为热点。

## 第二轮：VFS 内部阶段

| 阶段 | CPU 秒 |
| --- | ---: |
| 外层 openat | 7.833721 |
| 路径打开（外层） | 7.103529 |
| fs context | 0.208211 |
| 中间路径遍历（含中间权限） | 3.762046 |
| 末组件查找/创建 | 2.317215 |
| 最终权限/末组件检查 | 0.253502 |
| VFS 文件打开 | 0.156698 |
| truncate | 0.021743 |
| 路径打开未分段余量 | 0.384115 |

中间遍历和末组件查找/创建合计占路径打开 **85.58%**。成功 `/proc` 主要落在中间遍历（1.181799 秒）；成功用户目录路径主要落在末组件查找/创建（1.000436 秒）。最终权限、文件打开回调和截断在当前分段中不是主要项。

用户目录成功打开 921 次，其中 669 次请求 `O_CREAT`（72.64%），这些调用占该类别路径打开 CPU 85.30%，并占末组件查找/创建 CPU 97.05%。这仅是 **请求标志**，还没有实际新建数量，不能把带 `O_CREAT` 的调用全部当作新文件创建。

## 质量与证据边界

- 两轮 startup/steady CSV 传输 hash、计数关系、synthetic 行过滤均通过；dropped=0、CPU>elapsed=0。
- 第二轮进一步按 shard/PID/类别/深度/flags/errno 检查内部阶段 CPU 总和不超过外层阶段，不匹配数为 0；错误返回、符号链接重复遍历、O_PATH 覆盖边界写入原始报告。
- 两轮 monitor PPM 实际 SHA256 均为 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`。首帧观察区间分别为 76.003–77.024、76.018–77.014 秒；guest/QEMU/runner 均 0，QEMU VmSwap 峰值均 0。
- 计时是线程 kernel CPU；跨进程累计不是关键路径。额外采样和聚合开销未校准，未分段余量包含诊断成本，两轮不用于计算性能收益。
- synthetic 行复用诊断统计表的编码，必须从真实 syscall/process CPU 总量排除；通用分析器未过滤的 totals 不能直接使用。
- 第一轮临时聚合键碰撞问题在运行前已由 reviewer 发现并修正；最终补丁、失败/中间构建记录分别保留。没有将临时统计合入生产内核。

## 最后一次小 C 探针

停止增加 Chromium 插桩后，使用 `tests/fs/openat-path-cost.c` 每类 20 次，以 kiosk UID 1000 在专有 fixture 上运行。六类共 **120/120 成功**，原始数据和报告经另一 worker 独立复算通过。

| case | 操作 | 路径打开 CPU ms / 20 次 | 末组件 CPU ms / 20 次 | 实际创建数 |
| --- | --- | ---: | ---: | ---: |
| P1 | /proc/self/status | 16.218432 | 3.122544 | 0 |
| P2 | /proc/self/task 目录 | 15.654832 | 3.608896 | 0 |
| H1 | 已有文件 | 6.519024 | 0.591456 | 0 |
| H2 | 已有文件 + O_CREAT | 5.961248 | 0.503456 | 0 |
| H3 | 真正新建文件 | 33.988864 | 26.954688 | 20 |
| H4 | 已有文件 + O_CREAT/O_TRUNC | 8.138032 | 0.513968 | 0 |

H3 的后端查找共 1.788576 ms（19 次）、namespace 获取 0.084640 ms（40 次），未占其末组件 CPU 的主要部分。第一轮 unlink 留有负查找结果，因此后端查询只有 19 次，实际创建仍为 20 次；没有观察到每次重复两次后端查询。此探针不支持“已有文件上的 O_CREAT 标志本身是主因”，而把下一候选收敛到实际文件创建所在路径。

H1/H2/H4 全部观测到 fast-positive，无后端查找；中间权限累计约 0.47–0.53 ms。静态代码确认默认 permission 读取驻留 inode 元数据，没有 filesystem getattr 回调，这属于静态排除，不能写成测得 getattr 为 0。P1/P2 中 `/proc/self` 魔法链的递归采样覆盖不完整，不能由遍历余量认定缓存根因或证明动态节点可安全缓存。

归属和局限：

- 每个 C PID 在 exec 前沿用 su/shell 身份，外层 openat 聚合为 23 次，其中 3 次属于 `/etc`。目标阶段按 printed PID + 路径类别 + raw flags 过滤恰好 20 次；不能把 23 次外层 CPU 当作探针 20 次 CPU。
- fixture 制备在统计窗口之外；H3 unlink 在单次 openat 的 C 计时之外，但仍位于 profiler 窗口，不能说整个窗口只有 openat。
- C 时间含时钟和用户态包装；表中 CPU 来自过滤后的内部阶段。H4 只有第一次截断非空文件，后 19 次为空文件。
- 六窗口传输 hash、计数、CPU 边界均匹配，dropped/时钟异常为 0；guest/QEMU/runner 均 0。该轮只运行小探针，不包含 Chromium。

**本轮定位闭环已完成，尚无生产优化。** Chromium 实际创建次数仍未知；创建 callback 内部权限、其他锁和具体创建步骤未分离，不能把 H3 剩余时间直接当成纯 vfs_create CPU。下一项推荐范围是 VFS/ext4 实际创建路径，先核对真实工作负载中的创建次数，再检查元数据/创建事务/同步生命周期；保留 O_CREAT/O_EXCL、mode/umask、已存在路径和并发原子性。证据不支持直接跳过同步、权限或新增 proc 缓存。

## 复现索引

| 轮次 | 原始运行 | 报告、冻结与解析 |
| --- | --- | --- |
| v1 | `artifacts/runs/m5-003-openat-diagnostic/` | `artifacts/work/m5-003-openat-diagnostic/` |
| v2 | `artifacts/runs/m5-003-openat-diagnostic-v2/` | `artifacts/work/m5-003-openat-diagnostic-v2/` |
| C probe | `artifacts/runs/m5-003-openat-probe/` | `artifacts/work/m5-003-openat-probe/` |

各目录保留准确源码快照、完整补丁 SHA256、配置/输入 hash、ELF/BIN、实际 QEMU argv、构建和运行命令、原始串口/monitor/宿主采样、退出原因及解析脚本。重算入口分别为各诊断目录的 `analyze.py`；运行命令与解释限制见 `REPORT.md`，小探针方案见 v2 的 `NEXT-PROBE-PLAN.md`。

探针制备入口：`python3 scripts/prepare_openat_tests.py --base <既有工作盘> --output work/images/<新工作盘>.img --evidence artifacts/<新制备目录>`。完整实际 guest 命令保存在 C probe 证据目录的 `guest.sh`；临时插桩未提交生产内核，复现内部阶段必须使用同目录冻结源码/补丁，普通内核只能运行 C 层计时。
