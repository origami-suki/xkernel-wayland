# Syscall 聚合统计（M1-013）

当前入口统计 dispatcher 内完成的调用，按 PID/系统调用输出次数、错误、CPU 与经过时间总量和最大值。默认关闭。`/proc/syscall_profile` 为 0600，控制与读取还检查当前 euid，继承 fd 不绕过检查。

```sh
echo start >/proc/syscall_profile
# 执行目标工作量
echo stop >/proc/syscall_profile
cat /proc/syscall_profile
```

- start 重置上一份已停止数据；活跃时再次 start 或读快照返回 EBUSY；stop 幂等。
- CPU 使用现有线程内核 CPU 计账，不计睡眠/就绪等待，沿用其 IRQ 计账边界；经过时间含调度与阻塞。
- 16 个按 TID 分片的预分配表，每表 1024 行、最多 32 次探测；热路径不分配、不打印。满表以 dropped 报告，不能忽略。
- 只累计同窗口内开始并完成的调用。started-completed 是未完成/跨边界/不返回调用，不是零耗时样本。窗口之前已经阻塞的调用不在本窗口中；稳定窗口尤其要保留此限制。
- 不覆盖 syscall 之外的用户缺页、入口/返回、后续信号、内核后台工作或用户态计算；不能用排行总和冒充整体 CPU。
- 同 PID 的线程合并；PID 重用未区分。仅采集 PID，不自动根据继承的 zygote cmdline 推定 renderer 角色。
- 逐次短时测量有自身成本；必须用开关对照校准。CPU 超过 elapsed 会计数而不被静默截断。

真实 Chromium 入口：

```sh
python3 scripts/prepare_syscall_profile.py --base work/images/m1-012-combined-trace.img \
  --output work/images/NEW.img --evidence artifacts/NEW-preparation
python3 scripts/run_syscall_profile.py --run-id NEW-run --bundle PATH/TO/EXACT/BUNDLE \
  --disk work/images/NEW.img --profile-mode on --timeout 300
python3 scripts/analyze_syscall_profile.py --run artifacts/runs/NEW-run
```

适配器复用现有 run_guest 的校验/启动/关机，只注入截图采样子类和串口首帧握手；原 runner 无需修改。默认期望哈希来自不可变 first-runnable 原页 monitor PPM。只有看到真实匹配的帧且 guest 已发出 WAIT_FRAME，才发送固定文本 first-frame。启动窗口由浏览器启动前开始，到 guest 收到通知并 stop；截图采样区间、发送时刻、guest stop 时间与宿主收到停止标记分别保存，时钟未对齐，不宣称精确首帧时长。停止后保存 startup 快照并枚举进程，再开启单独 30 秒稳定窗口；导出发生在统计关闭后。`--profile-mode off` 运行相同会话/截图/握手用于开销对照，省略统计与导出。

主区真实校准已通过，首轮结果见 [热点记录](measurements/syscall-hotspots-20260926.md)，实现/验证状态见 [M1-013](tasks/M1-013.md)。按用户要求，剩余独立验证和五开五关实验已停止；当前排行用于选择优化路径，不作为已校准开销的性能成绩。下一步见 [逐项优化顺序](optimization-plan.md)。
