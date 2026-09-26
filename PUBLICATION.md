# xkernel-wayland：公开仓库与协作入口

本仓库记录比赛项目中基于 Wayland 的 Chromium 运行支持、内核兼容性修改、测试与性能证据摘要。

- 项目仓库：https://github.com/origami-suki/xkernel-wayland
- 内核衍生仓库：https://github.com/origami-suki/x-kernel
- 内核原始上游：https://gitee.com/openkylin/x-kernel
- 起点：`v0.2.0`，`c548c427af64bc4fcb7ba9d486d77e006be69b4f`。
- 本项目是参赛开发版本，非 openKylin 官方仓库，不代表官方背书。

## 获取代码

```sh
git clone --recurse-submodules https://github.com/origami-suki/xkernel-wayland.git
cd xkernel-wayland
git config --local core.hooksPath .githooks
```

已克隆的工作区更新 `.gitmodules` 后运行 `git submodule sync`。子模块提交由主仓库固定，不使用 `git submodule update --remote`。

## PPT 材料入口

优先阅读 `docs/first-runnable.md`、`docs/roadmap.md`、`docs/acceptance.md` 和 `docs/measurements/`。已验证结果、局部结果和待验收项目应分别描述。仓库中的历史文档可能使用旧项目名称 XKernelGUISupport。

公开仓库只包含提交并推送过的版本。首次发布未包含 syscall 计量和内存对照；后续已按任务分别整理提交，具体内容以所检出的提交为准。M1-013 计量工具的未完成验收见任务卡，不因源码入库而改为通过。

## 来源与授权边界

内核保留上游 Git 历史、LICENSE、NOTICE 以及源码中的版权和第三方声明。其顶层许可证为 Apache-2.0；具体组件存在单独声明时以其声明为准。项目内核变更可用 `git log v0.2.0..HEAD` 与 `git diff v0.2.0..HEAD` 核对。

内核许可证不自动适用于赛题 PDF、官方测试页、rootfs 或主仓库的独立材料。本次不上传赛方 Assets、工作盘、构建产物及完整运行归档；运行所需 Assets 须经合法渠道取得，并按 `config/baseline.json` 校验。本文不授予第三方材料的新许可。

`baseline/first-runnable` 为不可变历史存档，保留当时原始配置；历史 tag 的子模块 URL 仍指向 Gitee 上游。检出历史 tag 后，如需下载本项目内核提交，先将本地 `submodule.sources/x-kernel.url` 配置为上述 GitHub 内核仓库，再初始化子模块。
