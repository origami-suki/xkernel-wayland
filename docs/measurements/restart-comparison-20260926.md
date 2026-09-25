# M1-003：首次启动与同虚机再次启动，两对探索性实验

2026-09-26，按用户要求每组先做两次；单核对照已由用户中止，转做本实验。**两对首次启动均正确显示原页；两对再次启动均发生同址 Weston 段错误，首帧性能对照被功能故障阻断。** 没有改动内核、Chromium、Weston、运行库或原始测试页，没有附加 GDB/startup trace。

## 结果

| 独立虚机 / run-id | 首次完整原页观测区间 | 同虚机再次启动 | 退出结果 |
| --- | ---: | --- | --- |
| `restart-v3-20260926-pair-01` | 82.019–83.027 秒 | 150.099 秒观察窗内未显示完整原页；Weston 段错误，Chromium 因显示连接断开退出 | Chromium 1 / Weston 139 / seatd 0；guest/runner 1，QEMU 0 |
| `restart-v3-20260926-pair-02` | 72.000–73.027 秒 | 150.104 秒观察窗内未显示完整原页；同址故障 | Chromium 1 / Weston 139 / seatd 0；guest/runner 1，QEMU 0 |

两轮内核原始日志均为：

```text
segfault at VA:0xffffffffffffff80 ip 0x160a8c0 sp 0x7ffeffffe8a0
in /usr/bin/weston pid=24 outcome=Unmapped flags=READ | USER
```

精确日志位于 `artifacts/runs/restart-v3-20260926-pair-{01,02}/serial.log`；第二次浏览器日志末尾均在提交窗口 buffer 后出现显示连接断开。两轮观察末帧均为全黑，PPM SHA-256 为 `d4e96a65fd4f8e97bc1d762fc90cf2593bc2efb53a3125a72502fdae0f09395c`。这是用户态访问异常证据，**尚未证明根因属于内核还是 Weston，也未证明残留进程处理与故障之间的因果关系**。

已有 Wayland 日志提供以下补充阶段点。每列独立以该次浏览器的第一次 `wl_display.get_registry` 为零点，单位秒；不是浏览器 launch 到呈现时间，也不是文件读取阶段计时。

| 协议事件 | 第 1 对首次 | 第 1 对再次 | 第 2 对首次 | 第 2 对再次 |
| --- | ---: | ---: | ---: | ---: |
| 首次 create_surface | 19.292 | 12.708 | 16.968 | 12.173 |
| 原始页面中文标题 | 61.448 | 39.673 | 53.440 | 37.147 |
| 主 surface 首次 attach buffer | 63.044 | 41.449 | 54.213 | 39.099 |

第二次在这些应用阶段更早推进，但没有形成可见的正确首帧。不能把协议阶段缩短算作完整启动加速，不能据此认定页缓存或文件读取是主因；两次样本也不支持稳定收益或显著性结论。

## 控制条件与退出检查

- 集成起点 `ebc230e`；内核固定 `1b8b98a10aa4897819528b0991225967ad96b5b9`，源码 clean，两对使用同一 bundle、BIN/ELF、配置和磁盘哈希。
- QEMU 11.1.1、AArch64 cortex-a76、`-accel tcg,thread=multi`、2 GiB、4 vCPU、同一设备列表与 1280×800 显示。每对重启 QEMU；一对内部保留同一 QEMU、Weston PID 24 和 seatd PID 21。
- Chromium 及库来自原工作盘，参数保持 `--ozone-platform=wayland`、`--no-sandbox`、`--enable-logging=stderr`、`--ipc-connection-timeout=120` 及既有 wrapper 参数，`WAYLAND_DEBUG=1` 与此前相同；仍加载原始 `file:///opt/ict-testpages/index.html`。页面哈希在 guest 中再次打印核对。
- 两对均由相同磁盘 `work/images/restart-20260926-v3.img` 的 `-snapshot` 启动，原盘未改写；仅在新工作副本中预置本项目的测量脚本。脚本写入后的读取内容与源码逐字节一致。runner 每次在启动前读取全盘做 SHA-256；未清除 host cache，不能称为完全冷盘。
- 首次 browser PID 31 正常 TERM/wait，返回 0。随后对记录下来的 Chromium/zygote/crashpad PID 发 TERM；10 次等待后仍残留的 PID 发 KILL，再等待消失。第 1 对 KILL PID 37/39/97；第 2 对 KILL PID 100/117/37。因此这是**浏览器主进程正常退出、残留辅助进程额外清理后的再次启动**，不是全部组件自然退出。
- 检查所有记录 PID 的 `/proc` 路径和 `kill -0` 均不存在，并再次遍历 `/proc/*/cmdline`，确认没有 Chromium/crashpad 进程，才发出 relaunch 标记。`pair-*-after-exit.log` 均为空；再次 browser PID 分别为 207、213，排除了复用旧 browser 进程。
- 用户配置目录始终为 `/home/kiosk/.config/chromium-wayland`；首次前不存在，首次结束后已有 `Default`、`Local State`、缓存及其它配置内容，再次启动沿用这些内容，没有清空或换目录。目录名相同不意味着内容相同。
- 配置目录清单/大小/时间在测量窗外收集；只列元数据，没有为此读取全部 profile 数据做哈希。目录扫描本身也会影响元数据缓存。Crashpad 使用的另一个路径 `/home/kiosk/.config/chromium/Crash Reports` 在原始进程参数中记录；本实验未隔离其影响。
- 首次与再次各静默观察 150 秒。计时为宿主收到启动前标记起，持续每秒 monitor screendump；再次启动单独记录标记接收时间，沿用原截图时钟网格。串口时延未校准，报告实际相邻观测区间。
- 两对共 315 / 320 帧，无漏采槽位；全部原图哈希已重算核对，首次完整原页哈希均与不可变 first-runnable 相同：`5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`。每 5 秒记录宿主负载和 QEMU/cargo/rustc 进程，未检测到其它此类重负载并发；不声称宿主完全无后台负载。

## 测量入口与验证

新增 `tests/chromium/measure-restart-session.sh`，部署到独立工作盘的 `/opt/ict-tests/chromium/measure-restart-session.sh`。`artifacts/restart-20260926/inject-session-v3.debugfs` 保留实际部署命令，`short-launch.sh` 仅调用已部署脚本。

```sh
python3 scripts/run_guest.py --run-id <新名称> \
  --disk work/images/restart-20260926-v3.img \
  --bundle artifacts/restart-20260926/input-bundle \
  --vcpus 4 --serial-byte-delay-ms 10 \
  --guest-commands artifacts/restart-20260926/short-launch.sh \
  --sample-every-second --capture-limit 601 --timeout 600
```

`run_guest.py` 增加受限的 1/4 vCPU 选择（默认仍 4）、可选串口输入节流及有界截图数量。正常的四核默认条件不变。前景脚本结束并返回 shell 提示符后才发送完成状态命令，避免长时间排队的串口命令丢空格。`frame_capture.py` 记录 FIRST_END/RELAUNCH/SECOND_END 的首次宿主接收时间，不重置截图采样网格。此变更不在计时中暂停 QEMU。

- `python3 -m unittest discover -s tests -p 'test_*.py'`：30 项通过，包含重启标记首次接收、不重复更新原点与连续采样网格回归。
- `sh -n tests/chromium/measure-restart-session.sh`：通过。
- `python3 scripts/check_project.py`、差异空白检查：通过；提交 hook 另核对原始 Assets。
- `python3 artifacts/restart-20260926/validate_results.py`：进程/版本/参数/两轮输入一致性、所有截图哈希、无漏槽、退出状态和重复故障证据检查通过。**这不是再次启动功能通过**。
- 未改内核，未重新构建或运行内核模块测试；没有声称独立 Agent 验证。

## 预检、取消项与后续边界

单核实验已按用户指令停止，不能补成两组各两次的完整结论。`artifacts/cpu-count-20260926/` 和相关 run 目录保留输入传输失败、四核准备轮、单核 150 秒未显示原页及被用户中止的运行。单核曾暴露长串口输入丢字符，故后续改为工作盘预置脚本和节流输入；未修改内核串口实现。

热启动预检 `restart-20260926-pair-01` 在主进程退出后发现子进程残留，拒绝 relaunch；`restart-v2-20260926-pair-01` 在 TERM 后仍有 crashpad 残留，也拒绝 relaunch。预检不混入最终两对结果。串口完成标记丢空格的原始记录和最终提示符握手修复均保留；最终两对准确取得 guest 失败码并正常关机。

按项目约定检索了当前 Gitee 上游：网页关键词搜索未得到与本现场直接匹配的结果；issues/pulls `state=all` 列表 API 两次请求均返回 HTTP 403，原文位于 `artifacts/restart-20260926/upstream/`。这是访问受限，不能称为已经全面检索且不存在相关修复。本轮没有取得或引入候选补丁。

下一项最小诊断应另开**非计时**复现，定位第二次窗口 buffer 提交附近 Weston `ip=0x160a8c0` 的访存现场，区分应用对象生命周期、共享缓冲区和内核映射问题；需要时与同盘 Linux 比较。没有在本任务继续猜测性修补，也没有为了生成热启动数字重启 Weston、清空 profile 或改 Chromium 参数。

原始命令、负载、进程/配置清单、解析日志、输入哈希、`results.json` 与 `validation.json` 位于 `artifacts/restart-20260926/`；实际 PPM/串口/monitor/metadata 和运行时脚本位于 `artifacts/runs/restart-v3-20260926-pair-{01,02}/`。完整备份及恢复哈希清单位于 `artifacts/backups/startup-comparisons-20260926/`。
