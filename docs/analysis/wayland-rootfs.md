# M0-003：固定包增量与 Wayland 会话准备

2026-09-24。原始 Assets 只读；最初使用独立 `work/images/wayland-prepared.img`，最终从原 Assets 重新生成 `work/images/wayland-m0.img`，没有改 `xkernel-run.img`。本轮已实际补包、部署原页并完成第二盘重建。**版本/链接探测是在 Linux host 的显式 QEMU user 中执行；不代表 x-kernel 的 Weston/DRM 或浏览器显示已经通过。** x-kernel 实际探测中 BusyBox/musl/Weston/seatd 已执行成功；Chromium `--version` 触发 VMA 断言，详情单列在任务卡和 `artifacts/runs/m0-wayland-probes/`，不能声明 guest Chromium 已可运行。

## 输入、求解与签名

原包数据库 236 包，增量后 252 包；原有 Chromium 142.0.7444.59-r0、musl 1.2.5-r10、Mesa 25.1.9-r0、libdrm/libinput/Wayland/字体等版本全部保持。没有 upgrade、删除或重新安装原包。原盘 SHA256 是 `441b4504cfa7fef53f8458f28b4a80eb71f4fd08104f9f260627993fce47fe84`；原始压缩包和三页压缩包仍按 [baseline.json](../../config/baseline.json) 核验。

[锁文件](../../config/rootfs-wayland.lock.json) 保存官方 Alpine v3.22/aarch64 的 main/community 索引 SHA256/签名名、16 个 APK 的精确 URL/版本/SHA256/APK checksum、依赖/provides、aport commit、签名 key 和 scriptlets。来源为 `https://dl-cdn.alpinelinux.org/alpine/v3.22/{main,community}/aarch64/`。原 rootfs 信任的 `alpine-devel@lists.alpinelinux.org-616ae350.rsa.pub` SHA256=`d11f6b21c61b4274e182eb888883a8ba8acdbf820dcc7a6d82a7d9fc2fd2836d`，重建时也检查。

实际 APK 2.14.9 求解选择如下，16 个包均经 `apk verify` 验签成功，再从锁定本地 APK 离线安装：

| 新包 | 精确版本 |
| --- | --- |
| weston、libweston、weston-backend-drm、weston-clients | 14.0.2-r1 |
| seatd、libseat | 0.9.1-r0 |
| mesa-gles | 25.1.9-r0 |
| libxv | 1.0.13-r0 |
| cdparanoia-libs | 10.2-r14 |
| graphene | 1.10.8-r5 |
| gstreamer、gstreamer-ptp-helper、gst-plugins-base | 1.26.3-r0 |
| libcap2 | 2.78-r0 |
| libdisplay-info | 0.2.0-r0 |
| libelogind | 252.24-r1 |

这些是发行版 Weston 打包依赖；并未因首版用 pixman 而假定 EGL/GLES、GStreamer、PipeWire 等链接依赖可以删除。`libseat` 构建链接 libelogind，但新增入口用 `LIBSEAT_BACKEND=seatd`，没有启动 elogind/DBus 服务。

全部安装固定 `--root <挂载目录> --repositories-file /dev/null --no-network --no-scripts --no-commit-hooks`。已有 script/trigger 没有执行；新包内只发现 seatd/weston 的创建组步骤以及 seatd 的提示脚本。重建脚本明确补入 `seat:x:101:kiosk` 与 `weston-launch:x:102:`，遇已有名称/GID 冲突即失败；不执行跨架构 shell scriptlet，不安装 seatd-launch SUID helper。没有谎称跳过的 trigger 已运行。

## 可重建入口与保护

```sh
# 必须指定不存在的输出盘与新的证据目录；已下载包会按 hash 复用。
python3 scripts/rootfs_prepare.py \
  --output work/images/wayland-rebuild.img \
  --evidence artifacts/M0-003-rebuild-new

# 仅从官方包生成原始工作盘，可供串口基线使用；无需 sudo 或 qemu-user。
python3 scripts/rootfs_prepare.py --base-only \
  --output work/images/base-new.img \
  --evidence artifacts/M0-002-base-new
```

实际 host 安装了 `qemu-user-static 11.1.1-4`；这是主机依赖变化，不是 QEMU system 的设备/TCG 参数变化。重建要求 Python 3、xz 支持、readelf、可用的 `sudo -n`、mount/umount 及 `/usr/bin/qemu-aarch64-static`。不能以 root 运行整个脚本以免证据归属混乱，按上述普通用户入口执行。

流程为：校验输入 → 独占创建新盘 → 挂载新盘 → 校验缓存 APK/key → 验签/离线模拟 → 安装 → 比较原包 → 配置/原页 → QEMU user 版本探测 → 递归 ELF 检查 → finally 卸载 → 镜像 SHA256。退出码与命令写入证据，超时保存已有输出和 timeout 标志；非空证据目录、已有输出盘均拒绝覆盖。中途失败保留工作盘供诊断，不能把它当作已完成盘。

**不注册、修改或依赖 host binfmt。** APK 显式调用 QEMU user，并指定 guest 两个库目录的 `LD_LIBRARY_PATH`；只用 `-L` 时 guest musl 可能回落读到 host `/lib`，此前观察到 unsupported relocation，已修正此调用方式。程序能力探测则临时复制静态 QEMU 至 guest `/tmp/xkernel-qemu-user-probe`，通过 host `chroot` 显式执行；结束删除 helper。没有向 guest 绑定 host `/dev`、`/proc`，因此也没有意外访问 host DRM 设备。

重建保留包 URL 和缓存完整性；若官方仓库以后移除某精确版本，必须使用保留的 `work/wayland-cache/` 原 APK，脚本不会擅自选择新版。原始两个索引保留在缓存；重建安装只依赖锁定 APK，不依赖一个未来会变化的索引。

第二独立盘 `work/images/wayland-rebuild-verify.img` 完整流程通过。两次结果的包增量/版本相同，15 个配置、会话、原页和保留来源文件逐字节相同，见 `artifacts/M0-003/rebuild-file-comparison.json`。独立镜像的 ext4 修改时间等元数据不同，故不声称位级可复现。

## 实际二进制与模块证据

- 显式 QEMU user：BusyBox 1.37.0 帮助、Weston 14.0.2 版本/帮助、seatd 0.9.1 版本/帮助、Chromium 142.0.7444.59 版本均 exit 0。musl 1.2.5 无参数输出版本/用法后 exit 1，这是预期帮助行为。
- `/usr/lib/libweston-14/drm-backend.so` 已安装；`libweston-14.so.0` 有 `pixman_renderer_init` 符号，pixman 不需要另装一个凭空假设的 renderer 包。
- `/usr/lib/weston/kiosk-shell.so` 已安装；默认 desktop-shell 模块不在这组包内，所以入口明确使用 kiosk-shell。`--renderer=pixman`、`--backend=drm` 由这一实际 Weston 内置帮助确认。
- [递归审计脚本](../../scripts/rootfs_audit.py) 读取 8 个入口及递归依赖，总共 154 个 AArch64 ELF，`DT_NEEDED` 缺库为 0。解析绝对 symlink 保持在 guest root，记录 RPATH/RUNPATH；`dlopen` 后续模块/符号和系统调用仍需真实运行验证。
- Chromium 二进制留有 68 条 Wayland/Ozone 路径/类名字符串，包括 `ui/ozone/platform/wayland`，说明该二进制含原生 Wayland 实现；真正连接 compositor/创建 surface/显示原页仍属于 M3。
- 诊断负结果保留：`weston-simple-shm --help` 在没有 XDG runtime/socket 的环境中会尝试连接并 SIGABRT（-6）；Chromium ELF `--help` 尝试 `execlp` 外部 man，当前隔离环境中找不到它，SIGTRAP（-5）。这两条不是 x-kernel 失败证据，也不拿它们替代版本或图形运行通过。

最终证据：`artifacts/M0-003-final/`；历史执行证据：`artifacts/M0-003/` 与 `artifacts/M0-003-rebuild/`。最终盘 SHA256=`3b97a851dbb672a241ce1921625adc5aed9f806ccb297bc5f39be04fb3b3f32d`，卸载后 `e2fsck -fn` exit 0；QEMU runner 使用 `-snapshot` 并校验前后基盘 hash 不变。含 `solver.txt`、`apk-verify.txt`、`apk-install.txt`、`installed.before/after`、`package-changes.json`、`user-probe-results.json`、各程序 txt、`elf-dependencies.json`、`weston-modules.json`、`pixman-symbols.txt`、`chromium-wayland-strings.txt`、`pages.json`。原始日志不进 Git，必须和镜像/缓存一起备份。

## 串口分层会话

[guest 配置](../../guest/) 部署后，原 `inittab`、`kiosk-boot`、`x11-session` 备份到 `/etc/ict-wayland/source/`，原 `/usr/local/bin/x11-session` 和原欢迎页仍存在；新的 inittab 不自动启动 X11、Weston 或 Chromium，不运行 DHCP/NTP。x-kernel 当前 PID 1 仍直接提供 shell，Linux BusyBox init 的入口也只准备基础目录并提供串口 getty；Linux 整体启动的验证属于 M1-001。

下面前三类准备/版本/权限命令已在 M0 验证；seatd/Weston/client/browser 是后续 M2/M3 的分层入口。当前 QEMU 只配置一个串口，示意中的 B/C 控制通道尚未建立，需要 M2 先验证会话组织，不能假定现在已有多个串口。

```sh
# root 串口：当前 M0 已通过的准备/版本入口。
/usr/local/bin/wayland-prepare
weston --version
seatd -v
stat -c '%a %u %g' /run/user/1000
sha256sum /opt/ict-testpages/*.html

# root 串口 A，前台保留 seatd 日志与退出状态。
/usr/local/bin/wayland-seat

# 独立串口 B：原 kiosk 的 login shell 是 nologin，必须显式选 /bin/sh。
su -s /bin/sh kiosk -c /usr/local/bin/wayland-session

# 独立串口 C：先最小 wl_shm 客户端，通过后再浏览器。
su -s /bin/sh kiosk -c /usr/local/bin/wayland-client
su -s /bin/sh kiosk -c /usr/local/bin/wayland-browser
```

`wayland-prepare` 保留已挂载的 `/proc`、`/sys`、`/dev`，仅对缺少的必需挂载尝试 Linux 类型；错误直接退出，不掩盖未实现能力。初次 x-kernel 准备在额外的 devpts 挂载上返回 No such device（exit 111），而当前串口/Wayland 会话不需要 PTY，因此将 devpts 移出最小会话准备；没有改内核或谎称 devpts 已支持。原失败证据留在 `artifacts/runs/m0-wayland-inventory/`。它准备 `/dev/shm` tmpfs，1777；`/run/user/1000` 为 kiosk:kiosk，0700。seatd 以 root 运行并创建组 `seat` 可访问的 `/run/seatd.sock`；Weston 与 Chromium 都使用 uid/gid 1000，`XDG_RUNTIME_DIR=/run/user/1000`、`WAYLAND_DISPLAY=wayland-0`。kiosk 原已有 video/input 组，新补 seat 组。

关键链是 **Weston DRM backend → libseat seatd backend → seatd 的设备打开/权限管理 → DRM/input fd**；设备发现还依赖 libudev/sysfs 和 libinput。未通过设备枚举就不能预设 VT/KD 不需要，入口没有设置绕过 VT 的开关。QEMU 设备和 `/dev/dri`、`/dev/input`、`/dev/tty*`、`/sys`、`/proc` 的实际情况由 `wayland-probe` 与 runner 记录。

会话均使用 `exec` 前台运行，没有 compositor/browser respawn；未设 `--no-sandbox`、`--disable-gpu` 或原 X11 ANGLE/GLX 参数。需要参数调整时必须先记录真实阻塞和实际调用命令。Weston kiosk shell 适合首窗验证，多窗口/焦点功能在后续任务按需求验证。

## 原页与学习实验

官方入口固定为 `file:///opt/ict-testpages/index.html`，三个文件从原压缩包按字节复制：

| 页 | SHA256 |
| --- | --- |
| index.html | 831cf28f3748940b450d1da325aa39a2dcaea2fee15b0532bf4111144d176b38 |
| interaction.html | 58130daa4a2f426fd16b8bde93d964713f20e147da8af1537ab2d8b506d2fd58 |
| layout.html | a1b04f9aba81b4b7818be2e7a858d482d471bd0d625dee257f605b2129b8fc4c |

原欢迎页 `/usr/share/kiosk/index.html` 的 hash 仍为 `d34337bee3341cfd1feb775d68dc5c360e862b63d6fe126d2434a01754734f67`。浏览器入口不会使用它。

亲手实验：在 root 串口先运行 `wayland-prepare`、`weston --version` 和 `seatd -v`，比较版本成功与 `/run/seatd.sock` 尚不存在；再前台启动 seatd，观察 socket 的属组和权限。随后以 kiosk 启动 Weston，分别记录“ELF 能执行”“seat/socket 能连接”“QEMU monitor 能看到像素”三个阶段的第一条成功或失败日志。这能区分新增用户态文件与内核跨层接口的责任边界；M0 不把第三阶段提前标为通过。

## 最终 x-kernel 探测与当前边界

`artifacts/runs/m0-wayland-final/` 使用最终盘严格检查 prepare、runtime 权限、shm 权限、Weston/seatd 版本、设备、三份原页及 musl/Chromium 文件 hash，全部通过并正常关机，基盘哈希不变。`card0`、`event0`、`mice` 可列出，但尚未验证 libinput 枚举/事件、seatd socket、Weston DRM 输出或 wl_shm 协议。

完整 `wayland-probe` 包含 Chromium `--version`，当前内核会因此 panic，用户态 timeout 无法隔离内核崩溃；暂时只把这个入口用于 [M1-002](../tasks/M1-002.md) 的有界故障复现。无 Weston 增量的原始工作盘也复现相同 ELF 装载回溯。日常 M0 学习使用上面的已通过命令，图形会话序列是后续 M2 的执行入口，不能当作本轮已经运行通过。

所有运行及失败记录的持久索引见 [m0-evidence-index.json](m0-evidence-index.json)，索引包含原始文件 SHA256；大镜像、APK 缓存及原日志另行备份。
