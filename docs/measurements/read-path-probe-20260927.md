# Resource read 最小复现（2026-09-27）

本轮定位读取和观测成本，不修改内核或第三方，不启动 Chromium/Perfetto，不作为首帧原因或优化收益证明。集成 `9021a2df`、干净内核 `855a8c3c`；AArch64 cortex-a76、TCG、4 vCPU、2 GiB。复用 M5-004 准确 bundle，派生同一只加入项目探针的工作盘。

## 输入与读取语义

固定 Chromium 142 的 `chrome_browser_resources_integrity.cc` 使用 `GetPageSize()` 缓冲、ReadAtCurrentPos 和 SHA256，顺序检查 resources.pak、chrome_100_percent.pak、chrome_200_percent.pak。固定 file_posix.cc 的 wrapper 尝试填满 buffer 或遇 EOF/error；因此 wrapper 次数与 syscall 次数不同。探针使用相同 fill loop，分别记录两者。

| 资源 | 字节 | 4 KiB wrapper 含 EOF | 原始 read 尝试 |
| --- | ---: | ---: | ---: |
| resources.pak | 12292246 | 3003 | 3004 |
| chrome_100_percent.pak | 723342 | 178 | 179 |
| chrome_200_percent.pak | 1211284 | 297 | 298 |

此尺寸与 M5-004 的 3003/178/297 wrapper 组匹配，并与源码顺序一致；仅为文件映射的强线索，trace 未携带文件路径，不能冒称逐次已确定。输入 SHA-256/FNV、源码/二进制哈希和读回证据见 `preparation-wrapper/resources.json` / `manifest.json`。

## 方法

每文件有序运行：4 KiB batch、4 KiB per-call、同次数 clock-only、64 KiB batch、4 KiB prefault batch；各重复两次。batch 仅外层 wall/threadCPU 计时；per-call 每 wrapper 前后读取 MONOTONIC 和 THREAD_CPUTIME，共四次时钟调用；clock-only 执行同种/同次数时钟，不执行测量区 read。计时区不打印或哈希。文件 reopen/lseek、buffer分配和prefault在计时区外。

每轮计时停止后另作完整文件 FNV64 校验，并单列其 CPU；它是独立完整读取验证，不证明每一次测量区返回的字节均逐个比对。测量区检查字节总数、wrapper/syscall 次数、EOF、errno、短读与 EINTR。guest shell在任何测量前先对三个资源执行sha256sum，明确预读全部资源；因此batch首轮也已经预读，不能作为首次文件读取。两轮间的校验会继续暖缓存；first-use 仅为该模式首轮，不是冷缓存。prefault触碰实际目标buffer，64KiB组读取相同字节。

## 主资源包结果

单位 ms；first / warm 为各模式有序两轮，且前置sha256sum已经预读三个文件；非首次文件读取或正式性能重复。

| 模式 | x-kernel CPU first / warm | Linux CPU first / warm | x-kernel wall first / warm | Linux wall first / warm |
| --- | ---: | ---: | ---: | ---: |
| 4KiB batch | 80.827 / 91.824 | 14.550 / 13.824 | 80.915 / 91.873 | 15.682 / 14.251 |
| 4KiB per-call | 256.229 / 245.340 | 49.531 / 49.589 | 256.301 / 245.371 | 51.154 / 50.912 |
| 4KiB clock-only | 169.674 / 175.410 | 33.882 / 32.969 | 169.740 / 175.440 | 34.921 / 33.322 |
| 64KiB batch | 34.816 / 26.258 | 4.636 / 4.224 | 34.934 / 26.292 | 4.981 / 4.379 |
| 4KiB prefault batch | 77.224 / 76.482 | 14.340 / 13.500 | 77.299 / 76.641 | 15.080 / 13.771 |

观察：纯读取存在约 6 倍单线程 CPU 差异；逐调用计时增加的成本与同次数 clock-only 接近，不能把带时钟的 wrapper 时间全部归入 read。增大粒度减少调用后两侧耗时下降；prefault组没有消除 x-kernel 差异。仅凭这些对照不能定位具体 VFS、复制、锁或 trap 记账代码。

## 验证与证据

宿主严格 C11/O2/Wall/Wextra/Werror 编译；fixture覆盖5种模式、尾部短读、空文件EOF，并检查不存在文件、目录非regular、错误hash返回失败，见 `host/selfcheck-final.json`。AArch64静态严格编译、debugfs写入读回哈希、e2fsck -fn 和base不变均通过。旧 `preparation/` 是 wrapper语义核对前未运行的试制；真实输入来自 `preparation-wrapper/`。

两侧各30个JSON结果全部valid；guest/runner/QEMU均0，x-kernel正常PID1同步关机，Linux poweroff；输入盘两侧前后hash一致。源码与运行命令冻结在 `measurement-freeze.json`。

- x-kernel：`artifacts/runs/m5-005-read-xkernel-20260927`
- Linux：`artifacts/runs/m5-005-read-linux-20260927`
- 原始JSON、宿主检查、制备记录：`artifacts/work/m5-005-read-path-20260927/`

## 边界与独立审查

初轮单线程，不覆盖 Chromium 多线程进程中每次 trap 后 CPU timer扫描等可能成本。C不包含Chromium ScopedBlockingCall/observer/追踪开销，也没有尝试drop_caches、冷缓存控制、SHA256算法成本或核内插桩，不把微测推为首帧关键路径。初轮数据已冻结并交独立reviewer；初轮独立审查已通过，实际命令和限制见review-result.md；任务卡由leader同步。

## 有界追加：阻塞辅助线程数

leader批准在同一范围追加0/8/32个pthread辅助线程，仅主资源包4KiB batch和clock-only，各两轮、每侧一个新guest。辅助线程在同一mutex保护下报告ready后进入无超时条件变量等待；主线程的ready协议通过。它并非逐线程内核park状态的独立验证（unlock到futex及spurious wake仍有窗口）。无忙等、无定时唤醒、无并发读取/CPU忙任务。create、ready同步、proc任务计数及stop/broadcast/join均在计时外。默认参数0保留原模式；实际两侧proc任务数分别1/9/33且ready=0/8/32。

单位ms，CPU first / warm；资源字节和wrapper计数保持相同。

| 辅助线程 | x-kernel batch | Linux batch | x-kernel clock-only | Linux clock-only |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 78.765 / 78.247 | 15.702 / 14.435 | 183.386 / 176.716 | 35.909 / 35.621 |
| 8 | 133.708 / 132.668 | 18.262 / 15.160 | 394.392 / 397.126 | 35.673 / 34.808 |
| 32 | 281.972 / 276.597 | 15.501 / 14.964 | 973.735 / 959.565 | 35.399 / 34.763 |

观察：x-kernel纯读取和两种时钟组成的clock-only随辅助线程数增长，Linux本轮没有同样明显增长。这支持调查read与clock共享的进程线程数相关路径，不能仅归为文件读取实现；没有核内事件，所以尚未证明poll_cpu_timers、具体锁、扫描或记账函数的贡献。clock-only并非单独的CLOCK_THREAD_CPUTIME_ID基准，不用相减获得精确read耗时。仍是两次有序暖样本，不能外推秒级Perfetto热点或首帧因果。

严格pthread宿主编译、0/8/32两种模式和1/-1/33/bad非法参数自检通过，随后独立reviewer在guest前复核；源码/宿主产物哈希在threadscale/host-build.json和source-freeze.json。旧源码与制备脚本保存在threadscale/baseline-source，初轮原始数据、旧制备及冻结不覆盖。新增准确制备是preparation-threadscale，run是 `m5-005-read-threads-xkernel-20260927` / `m5-005-read-threads-linux-20260927`。两侧各12行全部valid；读取3003 wrapper/3004 read，主包字节/FNV一致；clock测量区read/bytes/EOF均0。guest/runner/QEMU均0且输入盘前后hash一致。

初轮独立审查与线程追加预审已通过；追加guest终审亦通过，无阻断项；完整review命令与限制转录在 `artifacts/work/m5-005-read-path-20260927/review-result.md`。

## 当前定位与上游候选

本轮将下一验证边界从单纯 ext4 读取缩小到 **read 与时钟调用共同承担的线程数相关成本**。在固定 `855a8c3c` 的 `posix/process/src/runtime.rs:161`，每次 trap 后调用 `poll_cpu_timers`；`process/kprocess/src/process/runtime_access.rs:509` 在没有 armed gate 的情况下先计算进程 CPU 时间，`:698` 遍历各线程并采样。这个路径不在已有 syscall profiler 的 dispatch 正文区间内，因此此前 syscall 排名不能排除它。以上是源码事实；线程数对照尚未单独测到这段函数的贡献。

静态 ext4 命中路径已经由缓存 folio 直接复制到用户缓冲；不能把 FileOperations 默认 scratch fallback 或块设备读取视作每次 4 KiB 热读的必经成本。完整调用链、26份当前源码快照、固定 Chromium 来源及上游访问证据见 [source-review/read-path.md](../../artifacts/work/m5-005-read-path-20260927/source-review/read-path.md)。

有限上游检索覆盖最新 100 PR / 100 issue，包含关闭和合并记录，API 成功，非穷尽历史。发现 [!813](https://gitee.com/openkylin/x-kernel/pulls/813) 提供未启用 CPU timer 时跳过轮询的 gate，元数据 head 为 `69ee74d0`；[!869](https://gitee.com/openkylin/x-kernel/pulls/869) 包含轮询节流和其他不相关改动，元数据 head 为 `648269fe`。本轮仅保存元数据，未获取补丁或改动内核；merged 状态不代表本项目通过。

下一项最小验证是取得 !813 候选，在当前准确冻结状态的隔离工作区独立验证：原版复现本线程数斜率，最小 backport 后同配置对照，同时验证 CPU timer 启用/停用/到期行为和原 Chromium 场景。不能直接引入 !869 全部内容或仅凭微测声称首帧收益。本轮到此收束。

## 重跑入口

以下使用新名字，拒绝覆盖已有工作盘和证据；不同时运行两台 QEMU。去掉 `--threadscale` 可准备默认五模式三资源矩阵，最终版带辅助线程支持，与初轮二进制的准确区别已存档。

```sh
python3 scripts/prepare_read_tests.py --threadscale \
  --base work/images/m1-012-combined-trace.img \
  --output work/images/read-threads-recheck.img \
  --evidence artifacts/work/read-threads-recheck-preparation
python3 scripts/run_guest.py --run-id read-threads-recheck-xkernel \
  --bundle artifacts/work/m5-004-perfetto-20260927/capture/bundle \
  --disk work/images/read-threads-recheck.img \
  --guest-commands tests/fs/read-path-cost.sh --timeout 180
python3 scripts/run_linux_guest.py --run-id read-threads-recheck-linux \
  --disk work/images/read-threads-recheck.img \
  --guest-commands tests/fs/read-path-cost.sh --timeout 180
```

上例 bundle 固定对应 `855a8c3c`，内核变化时不能冒称测试新 HEAD。原始实际命令见 `measurement-freeze.json` 与 `threadscale/run-probes.py`，实际 QEMU 参数、输入及退出见各 run 的 `metadata.json` 和 `command.sh`。
