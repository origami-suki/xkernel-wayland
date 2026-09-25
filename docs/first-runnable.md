# 首个可运行 Chromium 基线

2026-09-25，`m3-ipc-timeout120-20260925` 的第 7 张 QEMU monitor screendump 正确显示赛方原始 `index.html`：标题、四色块、表格、列表、SVG、表单均可见。实际链路是 Chromium 原生 Wayland → Weston DRM/pixman → virtio-gpu。首次内容帧已立即复制到 `artifacts/baseline/first-runnable/`，准确版本/哈希见 `config/first-runnable.json`。

内核为干净 `31c8f270`，包含本轮 F_DUPFD 最小编号修复 `ec9df016` 与未知 fcntl 错误返回修复 `31c8f270`。相同内核的默认 15 秒 IPC 连接期限仍白屏；本次唯一浏览器参数变化为 `--ipc-connection-timeout=120`。未修改 Chromium、Weston、系统库或原始页面。原先子进程在 `ChildThreadImpl::EnsureConnected` 超时退出，延长期限后观察到内容显示；这说明该运行的连接期限阻断了页面进展，尚未定位慢初始化/调度的底层原因。

```sh
python3 scripts/run_guest.py --run-id <新的运行名> \
  --disk work/images/chromium-m1-fcntl-unknown-v1.img \
  --bundle artifacts/M1-011-fcntl-unknown-20260925/clean-bundle \
  --guest-commands tests/chromium/first-runnable.sh --timeout 420
```

配置保持 AArch64、QEMU 11.1.1、cortex-a76、TCG、2 GiB、4 vCPU。仍使用 `--no-sandbox`；本记录只验收原页可见，不代表键鼠、页面自检、完整沙箱、10/30 分钟稳定性、正常退出或内存评分通过。首次发现时观察和清理尚在运行，结束状态后续另记，不移动标签。

集成运行起点为 `f9f9e58`，当时有此前遗留的未提交文档修改；完整 patch/status 已随首帧存档。标签提交纳入与本次受测版本逐字节相同的启动脚本，内核与其运行 ELF 独立固定，不把未提交文档工作区描述为干净现场。

`baseline/first-runnable` 在集成和内核仓库中均为不可移动的 annotated tag。最早截图、当时日志快照和完整最终 run 分开保存；后续复跑不能替代最早现场。

## 运行结束与备份核验

最终采样：第1–5帧黑屏，第6帧提示栏加空白，第7–12帧为原页，内容帧SHA256均为 `5954354a033f30af4fa4861a5a4616b6a78bb34b2d9d72497fa3202fab6ce287`。第6帧到第7帧的host采集区间跨度14.507秒，是内容出现的采样界限；第7到第12帧跨度68.544秒。没有guest绘制完成时间戳，不能把第7帧采集时刻等同于真实首帧时刻；可信峰值内存等仍未测。

清理请求Chromium及Weston强制KILL；guest/runner=1、QEMU=0，最终正常关机，不能宣称应用正常退出。最终原始日志与metadata在原run目录，首次发现时的快照另存，均未覆盖。

同机完整备份 `artifacts/backups/first-runnable-20260925.tar.zst`，546556806字节，SHA256 `bf771386f4f6b8010941460b16ba4bbe1003f37f7a42a711accda1167e16d18f`。从解压流核对全部60个清单文件，包含完整rootfs工作盘、内核ELF/BIN、首帧/最终run、原测试页压缩包和两份Git bundle；两份源码在临时目录实际恢复到标签目标并通过git fsck。记录见同目录 `first-runnable-20260925-verification.json`。这是同机备份，不是异地备份。
