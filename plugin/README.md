# fluxiaRSS 插件（Obsidian）

> 客户端插件，放在 `fluxiaRSS/plugin/` 独立开发；开发完成后可装进 Obsidian vault 的 `.obsidian/plugins/`。

## 定位

Obsidian 插件（Win + 安卓同款），负责：
- 「今日 Digest」自定义视图：标题、概述、链接、排序理由、评分按钮
- 评分/评论命令：打分 + comment，POST 回服务端
- 已读/稍后读：落成 vault 笔记

## 关键约束

- **纯拉取 + 后台异步**：打开 Obsidian 立即显示缓存/加载态，不阻塞等待拉取完成
- 每次拉取设超时（~10s），失败返回结构化错误并友好回显，不白屏、不静默
- 更新本地缓存与「上次更新时间」

## 状态

- 占位（待服务端 API 稳定后开始开发）
