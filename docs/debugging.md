# QEMU/GDB 现场采集

本入口供人和 AI 在相同开发配置下检查 x-kernel 的运行现场。使用宿主 GDB，不依赖 guest 的 ptrace、gdbserver 或新增用户态软件。内核与用户态仍按既有项目约定处理。

## 启动和按需采集

在项目根目录运行；`--run-id` 必须未使用。选用已有的独立工作盘及实际待复现脚本，运行始终使用 QEMU `-snapshot`：

```sh
python3 scripts/run_guest.py --run-id my-debug-01 \
  --disk work/images/chromium-m1-fcntl-unknown-v1.img \
  --guest-commands /绝对路径/复现入口.sh --timeout 240 --gdb
```

复现入口应保持目标工作负载存活，或者运行直至失败；guest 脚本结束后封装会照常退出。另一个终端或 AI 工具调用可以发起快照：

```sh
python3 scripts/gdb_capture.py --run artifacts/runs/my-debug-01 \
  --expression '$pc' --expression '$sp'
```

`--expression` 可重复，按 GDB `print` 的语法查询当前选中 vCPU 上的寄存器或内核变量。使用单引号保护 `$`，Rust 符号用 GDB 能识别的完整名字；release 构建的部分局部变量可能显示 `optimized out`。表达式默认禁止写内存、写寄存器和调用 guest 函数，无法求值时记录错误，其他采集仍继续。不要把未知符号或不可展开的栈帧误判为内核故障。

想立即验证通道，可在 shell 就绪时自动采集一次：

```sh
python3 scripts/run_guest.py --run-id my-debug-smoke-01 \
  --gdb --gdb-snapshot-on-ready --timeout 90
```

`--gdb` 同时启用 guest 运行超时前的自动采集；采集完成后保留原超时失败状态并清理 QEMU。不开启 `--gdb` 时不创建 GDB 端点、不归档额外调试 ELF、不执行 GDB。

## 采集内容和状态

每次调用由运行封装串行处理，先通过 monitor 停住 QEMU，然后运行有时限的 GDB 批处理，以 `disconnect` 保持暂停并核对状态，最后由 monitor 恢复到采集前的运行状态。请求接口避免多个采集器同时抢占 GDB 或 monitor。GDB 使用隔离的初始化设置和私有临时目录下的 Unix socket；不开放 TCP 端口。不要绕过请求入口同时手工连接该 socket。

默认采集：

- `info threads`：QEMU vCPU 列表；这里不是 guest 内核任务或 Chromium 线程列表。
- `thread apply all bt 24`：每个 vCPU 最多 24 层回溯。
- 每个 vCPU 的 `x0`～`x30`、`sp`、`pc`、`cpsr`。
- 每个 vCPU 当前 PC 附近的 12 条指令，以及指定表达式。

每次结果在 `artifacts/runs/<run-id>/debug/<snapshot-id>/`：

| 文件 | 用途 |
| --- | --- |
| `result.json` | 请求、完整命令、起止时间、GDB 退出状态、是否恢复运行、排除的耗时 |
| `commands.json` | 各 GDB 命令的输出和错误，便于 AI 逐项读取 |
| `gdb.log` | 原始 GDB 输出，保留展开栈失败等提示 |
| `capture.gdb`、`command.sh` | 当时实际执行的脚本和参数；socket 随运行退出清理，不能离线直接重放连接 |

结果分为 `passed`（采集命令完成）、`partial`（部分命令失败）、`failed`（采集失败或超时）。这些只表示取证状态，不表示故障原因已证实，也不保证每层栈都能展开。采集 CLI 仅在 `passed` 时返回 0。原运行封装的 PASS/FAIL 仍表示 guest smoke/退出结果；调试失败单独保留在 `metadata.json` 的 `debug.snapshots`，不能只看最后一行 PASS 来验收调试能力。

每次 GDB 默认上限 45 秒，可用 `--timeout` 调整至最多 60 秒。GDB 被杀死或报错后仍尝试恢复 QEMU，并核对 monitor 状态；恢复失败会让运行封装失败退出。采集请求需在运行期间提交；会话结束后拒绝新请求。排队超时不表示请求自动取消，应检查打印的结果目录。

## ELF、证据和计时

- 调试 ELF 必须来自同一 bundle，按独立 reflink/复制归档到当次 `bundle/kernel.debug.elf`，保存 SHA-256。没有调试信息则拒绝开启调试。
- 在启动前比较 boot/debug ELF 的入口、所有分配段对应的 section 地址/尺寸/内容和源码符号数据，仅排除构建工具最后填充的两种 build note。检查结果在 `debug-elf-validation.json`。同尺寸但代码、数据或行号表不匹配也拒绝。
- 普通运行的镜像哈希、Build ID、配置、串口、monitor、命令、退出原因继续保存。不会把当前源代码提交号当成完整产物校验。
- 从暂停请求到恢复确认的整段宿主耗时单独记录，并从封装的 guest 运行超时预算扣除。这是保守的诊断耗时，不等于精确的 guest 停机时间；附加调试器可能改变 guest 的时序和定时器行为。
- 所有 `--gdb` 运行标记 `performance_sample_valid=false`，且拒绝与 `--sample-every-second` 同时使用。性能对照必须另跑未附加调试器的样本。

## AI 的使用顺序

先读串口/应用日志，明确首个异常和待回答的问题，再选择一次现场采集。卡住时结合各 CPU 的 PC/栈判断下一处检查边界；需要时用新的请求查询已知内核数据。QEMU GDB stub 不自动理解 guest 的任务列表、等待队列或 Chromium 多进程地址空间，这些仍需按当前内核数据结构分析。

panic 已使 QEMU 退出时不能再采集现场，应先用当次调试 ELF 符号化原始回溯。超时自动采集只在 QEMU 仍存活时执行。本工具不自动设置业务断点、不生成根因结论、不修改内核。疑似内核缺陷仍按项目流程查上游并验证候选补丁。

参考上游 [基础诊断工具](https://gitee.com/openkylin/x-kernel/blob/main/docs/ai/skills/problem-diagnosis/references/basic-tools.md)、[构建与符号化](https://gitee.com/openkylin/x-kernel/blob/main/docs/ai/skills/build-workflow/SKILL.md) 和 [QEMU GDB 文档](https://www.qemu.org/docs/master/system/gdb.html)。当前基础文档提到的 `QEMU_LOG=y` 在已核对的启动实现中没有对应处理；如需 QEMU 内部诊断，应显式设置 `-d`/`-D`，不与串口日志混淆。

验证记录见 [M0-007](tasks/M0-007.md)。
