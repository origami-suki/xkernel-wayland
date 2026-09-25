# M1-003：Chromium 长时间空白的 GDB 定位

2026-09-25，用户要求使用新接入的调试能力调查约 79 秒首帧。结果：**重现慢启动，捕获内存释放锁竞争、逐页解除映射与进程 CPU 时间统计现场；尚未确认整段延迟的主因，没有合入候选补丁。**

此前[原基线](first-frame-1s-20260925.md)的正文首见为 78.004–79.027 秒，[同盘 Linux](linux-first-frame-1s-20260925.md)为 10.017–11.028 秒。本次隔离基线为 92.009–93.032 秒；79 秒是一次测量结果，不能作为固定超时常数。

## 冻结状态与方法

- 集成起点 `0cc052c3dda5dc8a9a645e87a496755cc8eebdf7`，内核 `31c8f27023ce483ff327386597f6e0a640d705f9`（clean）。用户既有 26 份文档改动保留，冻结 diff 单独归档。
- QEMU 11.1.1，AArch64 cortex-a76，TCG multithread，4 vCPU、2 GiB；原 Wayland 会话、原始 index、no-sandbox、IPC connection timeout 120 秒。
- 工作盘 `work/images/chromium-m1-fcntl-unknown-v1.img`，SHA256 `9a122bcadc8efe19100f3e43cc72d3bc77796573b3572705658e2c6e74f59941`。调试使用 `-snapshot`；独立验证另外复制工作盘，输入前后哈希相同。
- 调试入口 `artifacts/M1-003-render-delay-debug-20260925/capture_run.py`，调用既有 `run_guest.py --gdb` 与 `gdb_capture.py`。浏览器参数不变，仅把静默观察改为每 10 秒的 monitor 截图标记。
- 调试运行 `artifacts/runs/m3-render-delay-gdb-20260925/`，四次计划采集约在宿主收到启动标记后 12、30、50、70 秒。另外两次手动表达式查询及一次超时自动采集，共 7 次，6 passed / 1 partial，均恢复运行；保守排除暂停耗时共 5.172 秒。`performance_sample_valid=false`，不能用这轮给出性能结论。
- 调试第 11 张 monitor 原图完整显示原页，SHA256 与 first-runnable 相同：`5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`。最终清理仍超时，runner=1、QEMU=0，显示与退出分别记录。

## 直接观察

关键原始文件均在上述运行的 `debug/<snapshot>/commands.json` 和 `gdb.log`。

| 采集 | 现场 | 能说明什么 |
| --- | --- | --- |
| 12 秒，`d51d42f9aa3646b2ba34682224c36c5b` | 多个 CPU 在 idle、PMR 中断屏蔽或中断入口 | 当时没有四核持续执行用户态渲染的证据；不是调度器故障证明 |
| 30 秒，`015a3f3fb995472d84d5a1075db1782a` | idle 与 `ProcessRuntimeState::clone_mm_user` 等路径，PC 多为 PMR 访问 | 采样仍分散；PC 命中不等于耗时占比 |
| 50 秒，`e2bbf81b8a8f41d7a4db2281de7c5c9b` | CPU 0 在 `BuddyAllocator::deallocate_pages`；CPU 2、3 自旋等待同一 `GLOBAL_ALLOCATOR.palloc` 锁；调用来自 `MADV_DONTNEED`、进程退出的映射清理；CPU 1 逐页 unmap | 直接证实内存释放及全局分配器锁竞争参与慢阶段 |
| 70 秒，`e55e677706e24399af6ebc5fdbb0aefe` | 两核仍在 `unmap_private_object_range`；另一核栈包含 `Process::process_cpu_times` / `Thread::sample_cpu_time`，还有任务引用容器释放 | 解除映射及 CPU 时间采样是下一步需要量化的路径；不能证明与 50 秒为同一次操作 |

代码对应：`sources/x-kernel/mm/alloc-engine/src/buddy_alloc.rs` 的旧释放算法在线性链表中查找伙伴；`mm/memspace/src/backend/private.rs` 的 `unmap_private_object_range` 按页遍历传入范围。尚未读取到这些现场的可靠 range 大小、任务身份及单次起止时间，不能断言某个巨型稀疏映射占满全部延迟。release 栈中的 `optimized out`、局部变量地址 0、栈展开停止也不能直接作为内核损坏证据。

原有两轮日志还提供了启动阶段差异。以下仅是同一 browser Wayland 日志内部、从首次 `wl_display.get_registry` 起的相对时间，**不是浏览器启动耗时或正式呈现时间**：

| 协议事件 | 原 x-kernel 样本 | 原 Linux 样本 |
| --- | ---: | ---: |
| 首次 create_surface | 16.365 秒 | 3.415 秒 |
| Untitled 标题 | 22.910 秒 | 4.654 秒 |
| 原页中文标题 | 58.864 秒 | 7.321 秒 |
| 主 surface 首次 attach buffer | 61.216 秒 | 7.316 秒 |

可见长延迟在应用开始提交主 surface buffer 之前已经存在，不能只归到 Weston 最后显示的一步。两边日志都存在 GPU 初始化失败/回退，不能仅凭这些报错解释差异，也未证明 IPC 握手本身耗时 79 秒。

## 上游查找和独立对照

先查询当前 Gitee 上游，包含 merged PR 和 closed issue；网页工具访问列表失败后，官方 API 成功取得 PR 内容/提交和第一页 100 个 `state=all` issue。完整原文保存在证据目录，检索范围不声称覆盖所有历史 issue。找到相关候选后，仅获取到独立 refs，冻结主区并委派独立 Agent；主 Agent 阻塞等待，没有并行排查或修改主代码。

| 隔离状态（每项 N=1） | 正文可见区间 | 判断 |
| --- | --- | --- |
| 原内核 `31c8f270` | 完整正文 92.009–93.032 秒 | 重现长时间空白；清理超时 |
| 仅 [!813 CPU timer gate](https://gitee.com/openkylin/x-kernel/pulls/813) | 完整正文 103.762–106.110 秒 | 改善预检未通过；不认定补丁语义错误或统计性回退 |
| 仅 [!821 buddy bitmap](https://gitee.com/openkylin/x-kernel/pulls/821) | 部分正文 75.002–76.023 秒；完整正文 77.006–78.030 秒 | 仍为原报告量级；资源回归风险阻塞合入 |

!813 上游提交 `69ee74d0f7071e1afe8b7c6e3736f24400428863` 直接应用于冻结基线，实际 diff SHA256 `18390cf6d4faee2fb4599838e981bb13f8f46df708dc7269028160d71f684bb5`，构建通过。此轮 monitor 采集最长停顿 8.659 秒并缺 9 个槽位；区间按实际时间，不能使用帧编号充当时间。未继续 fmt/clippy、模块单测及完整回归，不是可合入验证。

!821 原始提交 `14b503b6ca37e1c325708920c0505ddae413d6c8`、`f099018417f4321a536603e436791f6050ffc389`、`315075ab56fccc054df72f2786172c3b1db99198`，首次构建因缺 workspace 依赖失败；仅补上游相同 `intrusive-collections =0.10.2` 声明/lock entry 后，隔离 backport `c4305697160ac32184d1f5cba175b869e365e136` 通过 build、hook fmt/clippy、allocator host 9/9 测试及本轮显示/正常关机。没有引入 !813。但上游 [IKHSF8](https://gitee.com/openkylin/x-kernel/issues/IKHSF8) 报告此补丁在 x86_64 / 1 GiB / fs_racer 的 OOM 回归，当前 AArch64 试验不能消除该风险；不扩大修补，也不合入。

三轮均在独立 worktree、独立输出与独立盘上顺序执行，未附加 GDB。没有达到可合入预检条件，故没有继续五次 before/after、guest 模块单测及完整 M1/M2 回归。单次差异不能给出加速百分比、稳定中位数或完整因果归属。

## 证据、检查与后续边界

- 完整独立报告：`artifacts/M1-003-render-delay-debug-20260925/independent/REPORT.md`；三轮归档：同目录 `runs/`；实际命令、源码状态、输入/输出哈希、缺帧和退出状态见各 `metadata.json`。
- `frozen-state.json` / `frozen-integration.diff` 与 `independent/main-state-after.json` 保存主区未改变的核对结果；主内核仍为 `31c8f270`，不更新 submodule 指针。
- 主 Agent 复核三轮元数据与首个完整正文 PPM 的内容哈希，阅读关键栈、单测/构建报告并核对退出边界。只提交本测量记录，不混入用户既有文档修改。
- 备份入口：`artifacts/backups/render-delay-debug-20260925.tar.zst`；清单及读回验证见同目录 `render-delay-debug-20260925-verification.json`。包含本轮原始运行、候选/准确 ELF、冻结状态、输入工作盘与相关 Git bundle，不删除原始证据。

**本轮停止于定位证据，问题未修复。** 下一项最小验证应保持原内核，给 exec→首次 surface→原页标题→首次 buffer 建立同一时钟的阶段点，在慢区间读取任务身份与 unmap 范围，并记录单次开始/完成时间，区分解除映射、页分配锁等待与 IPC 等待的实际占比；调试样本继续与无调试计量分离。不继续盲目叠加补丁。
