# 验收与证据矩阵

依据赛题 PDF、原始三份测试页和本轮源码/镜像调查建立。M0-002 的构建、两次 guest shell/关机与 monitor 已验证；M1-002 的 ELF 装载缺口已修复并回归（原盘 Chromium `--version` 在 x-kernel 内返回 `Chromium 142.0.7444.59`），但图形/浏览器验收仍未完成，静态分析不自动关闭验收项。功能通过、证据齐全和用户学习情况分别记录。评分原文页码与完整拆分见 [需求核查](analysis/requirements-and-testpages.md)。

| 编号 | 场景 | 条件与证据 | 状态 |
| --- | --- | --- | --- |
| ENV-01 | 开发环境 | AArch64、TCG、2 GiB、4 vCPU；系统 QEMU 11.1.1；版本、完整命令与设备清单；两次 guest 启动与退出 | [已验证](tasks/M0-002.md)：m0-serial-04/05、m0-monitor-stop |
| ROOTFS-01 | 赛方输入与工作盘 | 从原始 rootfs 增量补齐 Weston/依赖与 Wayland 配置，记录包版本/来源/哈希；工作盘可重建；三份原页独立部署核对 hash/URL，不能用 kiosk 欢迎页 | [已验证](tasks/M0-003.md)：16包增量、重建、3页host/guest哈希一致 |
| WAYLAND-01 | 本队 Wayland 路线 | STREAM SCM_RIGHTS/shared mmap 探针；wl_shm 客户端→Weston DRM后端→guest virtio-gpu→screendump；Chromium实际 Ozone/socket/协议链有证据，优先原生 Wayland | [M1-004](tasks/M1-004.md) 的 STREAM/shared mmap/seals 接口探针通过；wl_shm、Weston DRM 和 Chromium 显示链仍未验证 |
| GUI-01 | 图形会话 | Weston 在 guest DRM 输出上启动成功，连续 10 分钟无退出；screendump、PID/时间线与日志，不用 respawn 拼接时长 | 未验证 |
| WEB-01 | 初次 Chromium | 真实窗口正确显示原始 index.html，中文、内联 SVG、表格和表单视觉正确；screendump、命令和日志 | 未验证 |
| BASE-01 | 首个可运行存档 | 首次满足 WEB-01 立即保存成功现场和 baseline/first-runnable；两个仓库提交、镜像/配置/页面/参数、证据 hash；未测项显式说明 | 未建立 |
| PROC-01 | 多进程 | 可观察 renderer 等进程，记录进程结构 | 未验证 |
| INPUT-01 | 键鼠 | QEMU 虚拟设备输入 `hello x-kernel` 并回显、鼠标点击计数和页面跳转；记录输入与窗口焦点，不能用 JS 派发事件替代 | 未验证 |
| WEB-02 | 多页面 | 至少两个标签页或窗口并可切换 | 未验证 |
| JS-01 | interaction.html | 单次触发核心 6 项，通过后留图/日志；T4 实际判据为 700 ≤ dt < 10000 ms；重跑刷新避免遗留 timer 混入；localStorage F1 另记不计核心数 | 未验证 |
| CSS-01 | layout.html | 6 项自检通过并逐区/滚动保存 screendump 与视觉比对；computed style/几何断言不能代替像素正确，截图记录视口/分辨率 | 未验证 |
| STAB-01 | 决赛稳定性 | 30 分钟无崩溃/OOM；每 2 分钟加载测试页，共 10 次，无卡死 | 未验证 |
| PERF-01 | 性能方法 | 启动首帧、加载、renderer 创建、峰值内存至少四类；明确时钟/对象/单位/失败样本；各至少 5 次，中位数与 min/max；先校准内核占位字段 | 未验证 |
| MEM-01 | 决赛资格线 | 指定页加载且峰值内存不超过 1.5G；统计对象/单位/采样以官方脚本为准；不通过则整个性能与资源消耗 20 分计 0；不以 guest RAM 或 host RSS 替代 | 未验证，正式口径待核对 |
| COMPAT-01 | 接口缺口 | 真实缺口、最小复现、Linux 对照、修复及回归；不人为制造缺口 | [M1-002](tasks/M1-002.md)：解释器地址冲突 + 四处非法 ELF 断言已修复，双盘复现、Linux system 对照、errno/信号逐项对照与回归齐全；`bad-filesz` 语义差异记为独立候选；[M1-004](tasks/M1-004.md) 新增 STREAM SCM_RIGHTS 与 memfd seal 实际执行缺口的同盘对照、修复和独立验证 |
| UPSTREAM-01 | 上游贡献 | 区分可提交/已提交/已合并；初赛最多 3 个合并 patch 计 12 分，决赛最多 3 个计 9 分；外发需用户授权，合并依赖上游 | 未提交；M1-002 的 kexec/ELF 修复是可提交候选 |
| OPT-01 | 优化效果 | 对比不可变首版 tag 的原始 before/after；同环境/工作量各 ≥5 次并功能回归；初赛 5%/15%/30% 对应 1/2/3 分，决赛每项 0.5/1/2 分最多 3 项（原文另含方法突破） | 未验证 |
| RESOURCE-01 | 团队资源回归 | DRM buffer 至少 100 次创建/映射/present/销毁及应用退出后的回收；此为团队阈值，可并行且不延迟 BASE-01 | 未验证 |
| TOOL-01 | 可观测性 | 能定位崩溃、卡顿、泄漏，工具可复用并有一键入口和文档 | 未验证 |
| DELIVERY-01 | 交付 | 代码、配置、脚本、日志、截图、设计报告、演示视频及复现步骤齐全 | 未验证 |

每项结果关联运行编号、集成与内核提交、输入哈希、命令、环境、原始证据路径及失败记录。JS/DOM 检查和主机录像是补充，不能代替规定的 QEMU monitor screendump。RAW PPM/PNG、日志、样本要保存，不能只保留报告里的缩略图和平均值。

ENV-01 的“两次启动”和 RESOURCE-01 的“100 次”是本队回归要求；GUI-01 的 10 分钟、STAB-01 的 30 分钟/10 次循环来自赛题。BASE-01 不等待 GUI-01、INPUT-01、STAB-01、PERF-01 全部通过。三份页面无外网依赖，联网成功不是 WEB-01 的前置条件。

WAYLAND-01 是用户确定的技术路线，赛题原文允许多种协议，不能写成官方只接受 Wayland。headless/nested 后端不证明本队 guest DRM 显示链成功；如将来采用 Xwayland 桥接，须明确实际链路，不能标为 Chromium 原生 Wayland。

首次能运行即开始记录可测指标。初赛 15 分评审测量方法和相对改善，不比较绝对性能；决赛采用统一主机/脚本排名。官方单独的启动/计量脚本目前不在附件中，发布后须复核本地定义和现场流程。
