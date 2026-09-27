# GPU channel 等待与 trap 返回成本定位（2026-09-27）

目标是定位当前新热点清单的前两个问题。起点集成 `6f6fc88e`、内核 `855a8c3c`，AArch64/cortex-a76、TCG、4 vCPU、2 GiB。GPU worker分析原始同盘两份trace/serial及版本匹配官方源码；diagnostic worker在准确冻结内核的独立worktree临时插桩。生产内核没有优化改动。准确完整提交、配置与交付保存在 `artifacts/work/hotspot-localize-20260927/leader-freeze.json`。

## GPU 同步等待

原页 renderer PID105 的 `EstablishGpuChannelSync` 从69.671642到89.747106，共20.075464秒，自身CPU0.011574秒。原始事件与日志说明它跨越多次GPU初始化失败、Browser host/channel重试和模式回退，最终得到GPU通道回复。版本匹配Chromium 142.0.7444.59的同步函数用无期限 `event.Wait()`；该调用没有固定20秒超时。

| trace秒 | 边界 | 至上一边界的elapsed秒 |
|---:|---|---:|
|69.671642|原页开始同步等待|—|
|76.490182|GPU PID163初始化失败退出|6.818540|
|77.542919|Browser再次LaunchGpuProcess|1.052737|
|85.496284|GPU PID180初始化失败退出|7.953365|
|86.212416|Browser再次LaunchGpuProcess|0.716132|
|87.926844|GPU PID196初始化失败，日志有SwANGLE/Vulkan extension错误|1.714428|
|88.224966|Browser再次LaunchGpuProcess|0.298122|
|89.639697|原页IO线程收到通道reply|1.414731|
|89.747106|原页主线程恢复|0.107409|

连续区间合计20.075464秒；它们是时序分段，包含启动、初始化、调度和IPC，并非纯GPU CPU。失败PID自身的viz日志证明GPU角色；Launch到下个PID的绑定只是时间顺序推断，没有spawn事件PID参数。最终PID203有process_type=Gpu和初始化/channel事件。

Browser为相同client_id反复EstablishGpuChannel；该ID不能直接当OS PID。官方 `GpuClient::OnEstablishGpuChannel` 在kGpuHostInvalid时保留callback并重新请求，解释renderer的一次同步wait如何跨多个失败尝试。具体每次status未写入trace，失败日志和源码机制支持这一解释，不能声称直接捕获全部status。

同盘Linux也有GPU失败回退链，原页对应等待0.710077秒，最终reply到返回约0.011秒。X GPU163/180的DRM警告到失败分别约6.683/7.014秒，Linux所处失败链对应警告到失败约0.569/0.130秒。失败进程缺少初始化thread slices；内部耗时尚不能归因于用户态GL、文件加载、内核或特定等待。最终继续运行的GPU203初始化0.233秒不能代替前面失败尝试。

时钟使用同snapshot的REALTIME−MONOTONIC桥对齐guest日志与trace；X四个offset极差0.233ms。没有混用host时间。重复输出的日志按完整行去重；缺失flow/status/PID参数如实保留。

最窄下一验证是失败GPU初始化期间的用户态调用栈、同PID累计CPU及阻塞状态，重点覆盖约7秒失败段。当前已定位到失败初始化／重试链，尚未取得失败初始化内部栈。

官方来源：[同步等待](https://chromium.googlesource.com/chromium/src/+/142.0.7444.59/services/viz/public/cpp/gpu/gpu.cc#328)、[重试机制](https://chromium.googlesource.com/chromium/src/+/142.0.7444.59/components/viz/host/gpu_client.cc#110)、[初始化失败分支](https://chromium.googlesource.com/chromium/src/+/142.0.7444.59/components/viz/service/main/viz_main_impl.cc#188)。原始SQL、日志对齐、输入hash及源码hash见 `artifacts/work/hotspot-localize-20260927/gpu/`。

## Trap 返回阶段

定位链路为 `posix/process/src/runtime.rs:161` → `process/kprocess/src/timer_delivery.rs:58` → `process/runtime_access.rs:509,698`。当前内核在检查timer状态之前，先取得所属进程全部线程的快照Vec，并逐线程采样CPU时间；没有活动CPUtimer时也执行。所属进程线程数T、POSIXtimer数M带来O(T+M)结构成本；该返回阶段位于现有syscall_profile的handler计时之外。

最终临时诊断仅在独立worktree，patch SHA-256为 `50f45d84e1a5b9dc6b96e1926bb7b3e28a7552986cda72515f90aa5b08dfd509`。统计默认关闭，全量计数、1/64确定性CPU/wall采样；whole poll包含process_cpu_times聚合，二者不能相加。两次微测使用不同采样offset，仍保留alias风险。

| 最终插桩口径 | 1总线程 | 33总线程 |
|---|---:|---:|
| 聚合阶段采样CPU均值 |7.095–8.875μs|72.540–73.088μs|
| warm batch、诊断off外层CPU |85.626ms|291.641ms|
| warm batch、诊断on外层CPU |165.136ms|359.800ms|
| warm clock-only、诊断off外层CPU |199.219ms|974.128ms|
| warm clock-only、诊断on外层CPU |486.781ms|1309.886ms|

全部16条探针结果valid，8个快照完整，线程ready/blocked及聚合线程单位分别为1/33；所有探针poll都没有活动CPUtimer。read字节数和原资源后读FNV校验通过。首个off-batch包含首次I/O/调度，表只列warm repeat，不声称控制冷缓存。

原页最终guest统计窗口87.071335秒，365519次完整poll，5796个配对计时样本。**原始采样**whole poll CPU为1.615942秒，嵌套聚合0.858843秒，占53.15%；所有观察到的timer状态均无armed legacy CPUtimer、无POSIXtimer。正向CPUtimer回归另实际检测1301次armed poll，排除状态统计恒零。

这些结果支持“无活动CPUtimer仍全线程聚合，且被观测的聚合阶段成本随线程数增大”。**绝对数值侵入明显**：0-helper warm clock-only on/off增加144.35%，计数锁、owner检查和时钟采样都有成本；不能将当前采样当成无插桩生产成本。均值×调用数的102.041676/54.316856秒仅未经校正的并发诊断外推，不能称墙钟、优化收益或可节省首帧时间。

停止边界显式保留：startup PID29/shard7 child completed2031、parent2030，timed均32；总体2个parent/1个child未完成，steady没有差异。stop不排空整棵在途poll，独立复核确认这是一项未采样child先完成的停止边界；计时样本配对，原始比例可用于阶段定位，count-based外推仍有边界限制。

最终失败GPU PID60/128/151/167的退出槽已保留，由各自viz错误日志确认角色。但数据覆盖各PID整个窗口生命周期，没有GPU-init阶段时间线，不能把它们关联旧trace某个7秒片段，或认定CPU聚合造成GPU初始化失败。

最窄下一动作是独立隔离验证“没有活动CPUtimer时免除聚合”的最小gate，重点核对真实CPUtimer信号语义。有限实时上游元数据查询命中 [!813](https://gitee.com/openkylin/x-kernel/pulls/813) 和 [!869](https://gitee.com/openkylin/x-kernel/pulls/869)，只取得元数据；本轮未获取、适配、验证或合入补丁。!869捆绑fd duplication与节流，不能据标题作为本项目最小修复。

## 实际验证、输入及历史缺口

- 最终fmt check、make build、make clippy、diff check通过；早期clippy两处风格失败和修复日志保留。
- 完整parent token传递epoch/owner/shard/slot，修复首轮并发stop→start把旧child计入新epoch的诊断缺陷；共享实际scalar gate的两个host回归通过。它们不是完整多线程race系统测试。
- 既有CPU-accounting回归off/on各13 checks、0 failures，包含真实process CPUtimer SIGUSR1投递与sleep排除。thread CPUtimer仍为EINVAL unsupported，没有列为通过。
- 两个最终guest/QEMU/runner均0，snapshot盘前后hash相同，QEMU采样VmSwap均0。原页QEMU monitor hash为5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287，正常退出。
- 三个输入盘在 `work/m5-006-trap-diagnostic/input-{synthetic-final,page-final,page-superseded}.img`，独立inode、只读reflink，完整SHA均为f8d04652853b07e235b03073d4e5cdae03abdb3922b96e25ec4b89f88f40dbc1。字节相同、路径独立；host guest-commands分别执行synthetic和page入口，四项实际payload独立核验通过。
- 首轮synthetic的11543ba0输入已被工作盘入口更新替代，未找到精确旧盘或可逆差分。首轮结果与审查保留为superseded，未伪称恢复；最终通过不抹去该历史缺口。
- 首轮审查核验GPU桥、去重、连续区间和官方Wait/retry；最终审查只核对修订后的诊断与数据，不重复已通过GPU查询。两轮均保留原始输出。

复现/检查入口（实际已执行）：

```sh
bash artifacts/work/hotspot-localize-20260927/timer-final/build-check.sh
artifacts/work/hotspot-localize-20260927/timer-final/epoch_gate_test
python artifacts/work/hotspot-localize-20260927/timer-final/run_diagnostic.py --run-id m5-006-trap-synthetic-final-20260927 --disk work/m5-006-trap-diagnostic/input-synthetic-final.img --bundle work/m5-006-trap-diagnostic/target/xkmake/kplat-aarch64/release --guest-commands artifacts/work/hotspot-localize-20260927/timer-final/launch.sh --sample-every-second --timeout 240 --vcpus 4 --memory 2g
python artifacts/work/hotspot-localize-20260927/timer-final/run_diagnostic.py --timer-page --profile-mode on --run-id m5-006-trap-page-final-20260927 --disk work/m5-006-trap-diagnostic/input-page-final.img --bundle work/m5-006-trap-diagnostic/target/xkmake/kplat-aarch64/release --timeout 300 --vcpus 4 --memory 2g
python artifacts/work/hotspot-localize-20260927/review/final-verify.py
```

最终数据/补丁/hash/完整命令在 `artifacts/work/hotspot-localize-20260927/timer-final/`，独立审查在 `review/final-verdict.md`。两次最终完整运行保存在 `artifacts/runs/m5-006-trap-{synthetic,page}-final-20260927/`。诊断只用于定位，没有生产优化或正式五轮收益验收。


归档 `artifacts/backups/M5-006-hotspot-localization-20260927.tar.zst`，662820691字节，SHA-256 `bac4fedf46647986478d4e92e3358ce90bd2be6b18f8e801a4eba2b85d1561ca`。586文件逐项流式解压大小/hash通过；包含四轮完整诊断、准确debug bundles、最终patch、原始GPU trace/serial/metadata、官方源码快照、审查和一份三输入同内容的canonical盘。alias关系与恢复命令见 `artifacts/work/hotspot-localize-20260927/archive/restore-notes.md`；现有三个只读输入盘均保留。旧synthetic缺口没有因归档而消除。

Leader收尾：`python3 scripts/check_project.py --verify-assets`、最终诊断Python AST、修改文档本地链接及 `git diff --check` 通过。没有重跑已经通过的GPU查询或扩大为无关内核测试。
