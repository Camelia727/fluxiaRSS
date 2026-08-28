# fluxiaRSS 插件（Obsidian）

> 客户端插件，位于 `plugin/` 独立开发。安装：把 `main.js` + `manifest.json` + `styles.css`
> 拷进 vault 的 `.obsidian/plugins/fluxiars-digest/`，在 Obsidian「设置 → 社区插件 → 已安装插件」启用。

## 形态（用户视角）

每天在 vault 指定目录（默认 `FluxiaRSS/`）自动生成一篇 `YYYY-MM-DD.md`，内含今日 digest：

```
# 📰 今日智读 · 2026-08-27

> 生成于 06:30 · 对文章打分/跳过，反馈进入长期记忆，影响明天排序。

[卡片1] ① Agent 公司全景图 · 排序：评分 9.2 · 画像命中
        三条要点概述…
        [👍 高] [⭐ 中] [👎 低] [🕒 稍后读] [⏭ 跳过]
[卡片2] ② LLM 综述 · …
```

- 笔记内嵌一个 `fluxiars` 代码块（JSON 为 digest 数据），阅读视图中被插件渲染成卡片列表
- 五档按钮一次点击即提交：👍高=9/read · ⭐中=5/read · 👎低=1/read · 🕒=later · ⏭=skip
- 提交后按钮行变为 `✓ 已评 …`，下方展开可选评论输入框（Enter 提交，可跳过）
- 状态（含评论）存插件 data（跨笔记、跨重启保持），已有评论直接显示
- 标题点击在新标签页打开原文

## 命令

| 命令 | 行为 |
|---|---|
| 打开今日智读 | 今日笔记不存在则生成，然后打开 |
| 刷新今日智读 | 强制重新拉取并覆盖今日笔记 |
| 立即采集并刷新 | POST /collect 触发服务端采集，然后刷新 |

## 设置

- **API 地址**：fluxiaRSS 服务端，如 `http://192.168.1.5:8000`（默认 `http://localhost:8000`）
- **API 令牌**：服务端 `.env` 的 `FLUXIARSS_API_TOKEN`（留空则本机免鉴权）
- **digest 目录**：每日笔记目录（vault 内相对路径）
- **每日篇数（Top-K）**：每日智读精选条数，拉取时带 `?top=`（1-30）
- **自动生成时间**：过了该小时且今日笔记不存在时自动生成（每小时检查一次）
- **RSS 源**：设置页可增删自定义源（名称可选，默认域名）；内置源带「内置」徽章、自定义源带「自定义」徽章，改动后跑「立即采集并刷新」生效，列表持久化在服务端

## 关键约束

- 纯拉取 + 后台异步，打开 Obsidian 不阻塞；请求统一走 `fetchWithTimeout`（10s / 采集 120s 超时）
- 失败结构化报错并 Notice 回显，不白屏、不静默
- 已有今日笔记时自动刷新不覆盖（避免破坏用户标注），手动刷新才重写

## 开发

```bash
npm install
npm run build      # tsc 类型检查 + esbuild 打包 → main.js
npm run dev        # watch 模式
```

## 与后端契约

- 鉴权：服务端配置了 `FLUXIARSS_API_TOKEN` 时，所有请求带 `X-Fluxia-Token` 头；插件设置里填入同一令牌
- `GET {api}/api/v1/digest?top=N` → `{date, items:[{article_id, rank, title, summary, url, reason, source}]}`，`top` 缺省用服务端 `DIGEST_SIZE`（钳制 1-50）
- `POST {api}/api/v1/rating` → body `{article_id, score?, comment?, action}`，`score` 0-10（可省略），`action ∈ read|skip|later|comment`；`action=comment` 时给该文章最近一条评分补评论
- `POST {api}/api/v1/collect` → 触发采集，返回 `{fetched, new_added}`
- `GET {api}/api/v1/sources` → `[{name, url, topic, custom}]`（内置+自定义）
- `POST {api}/api/v1/sources` → body `{url, name?}`，新增/更新自定义源，返回该源
- `DELETE {api}/api/v1/sources?url=<url>` → 删除一个源
