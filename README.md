# fluxiaRSS —— 个人 RSS 智读流

> 个人 playground 子项目（不参与招聘准备）。域名 `fluxia.top` 取自现有服务器。
> 更新：2026-08-27 · 状态：设计阶段（设计已定稿，待 P0 落地）

## 一、一句话定位

每天凌晨自动采集你关注话题的文章，用 AI 做**概述 + 综合排序**，推送到 **Obsidian（Windows + 安卓）**；你看完**打分 / comment**，反馈回流进**长期记忆**，持续影响下一轮的采集与排序；同时内置**防信息茧房调度**，保证越用越懂你、却不会越推越窄。

## 二、整体架构

```
[RSS 源]  （你关注的话题 / 站点）
   │  cron 每天 05:00
   ▼
[新加坡服务器 2C2G]  ── 数据与逻辑中心
   ├─ 采集器    抓取 + 去重
   ├─ AI 层     概述 + 综合排序（Anthropic provider 适配优先）
   ├─ 记忆层    自托管 Honcho（deductive/inductive + dialectic）
   ├─ 防茧房    探索调度器
   └─ API       GET /digest · POST /rating · GET /profile
        │  HTTPS（走 Caddy 反代：fluxia.top/rssapi）
        ▼
[Obsidian 插件（Win + 安卓同款）]
   ├─ Digest 视图   今日 Top N（标题+概述+链接+排序理由）
   ├─ 评分/评论命令   打分 + comment
   └─ 已读/稍后读     落成 vault 笔记
        │  评分回流
        ▼
   长期记忆 → 更新画像 → 影响下一轮采集/排序/探索
```

三部分：**服务端管道 + 记忆系统**（核心）、**Obsidian 插件**（客户端）、**轻量通知**（可选提醒）。

## 三、技术栈

**服务端（新加坡 2C2G）**
- 语言/框架：Python + FastAPI
- 定时：系统 cron（或 APScheduler）
- 采集：`feedparser` + `httpx`
- 存储：SQLite（事件/文章/结论）+ `sqlite-vec`；Honcho 用 Postgres + pgvector
- 记忆：**自托管 Honcho**（Docker，AGPL 个人可用；4 容器：api/deriver/database(pgvector)/redis）
- LLM：**DeepSeek**（`deepseek-chat`，需打 json_schema→json_object 补丁）；**embedding 用豆包（火山方舟，OpenAI 兼容，云端）**——海外直连、免本地模型，省 ~900MB

**客户端**
- Obsidian 插件（TypeScript）：自定义 `ItemView` 视图 + 命令；REST 调服务端
- 一套代码同时跑 Windows 与安卓（Obsidian Mobile 支持自定义视图）

**部署**
- 服务端 Docker + Caddy 反代（复用 `fluxia.top` 证书与 443，路由 `/rssapi`）
- 与现有 VPN（Hysteria2 UDP443 / REALITY TCP8443）互不冲突，独立端口/路径

## 四、核心流程（每日 Pipeline）

1. **采集**：凌晨按话题配置抓 RSS → 解析 → 去重（FTS/链接/标题/向量语义）
2. **向量化**：每篇新文章算 embedding，入库
3. **概述**：LLM 生成 3-5 条要点 + 一句话
4. **排序**：综合评分 = 相关性 + 偏好相似度 + 来源信任 + 新颖度 − 已知重叠（见下）
5. **探索调度**：预留 10~20% 槽位给「低置信试探 + 新方向」
6. **落库**：生成今日 digest，记录每篇**排序理由**（可审计）
7. **注入**：Obsidian 后台异步拉取 `/digest` 展示（不等、超时、结构化报错）
8. **反馈**：用户评分/comment → 存事件 → 触发记忆更新

## 五、关键实现细节

### 1. 采集与去重
- 每个话题一组 RSS 源（`config.yaml`：topic → feeds）
- 去重三层：URL 指纹 → 标题归一化相似 → 向量语义去重（防改标题重发）
- 只处理新增，增量入库

### 2. 排序公式
```
score = w1·(LLM 与画像主题相关度)
      + w2·(cosine(文章, 你喜欢的原型) − cosine(文章, 你讨厌的原型))
      + w3·(来源信任分，来自画像)
      + w4·(新颖度/时效加成)
      − δ·(与已读/低分概念重叠)
```

### 3. 记忆与自成长（Honcho 模式）
- **事件层**：每条 `(article, score, comment, read/skip, time)` 原子事件，永不删
- **结论层**：`deductive（事实）` vs `inductive（推断/假设，带置信度，可推翻）`
- **dialectic 自纠正**：新证据与旧推断冲突 → **重写结论**（非叠权重）
- **Representation 快照**：缓存好的「当前对用户认知」，每天维护后刷新
- **子模型 (observer, observed)**：还建「用户对主题 X / 来源 Y 的认知」——防茧房抓手
- 快循环（每天微调排序）+ 慢循环（每周/每 N 条，后台 Reason 归纳修订结论）

### 4. 防信息茧房
- **已知覆盖建模**：deductive 记录每主题「已读/被推次数 + 低分主题」
- **新颖度 = 距离已知覆盖**：排序给距已知区域远的文章加新颖度
- **探索由画像驱动**：预留槽位给 ① 低置信但可能对口的主题（试探），② 距已知远的新方向（真探索）
- **刻意反差**：偶尔塞一篇与主偏好相悖的角度，测画像准确度 + 破茧房

### 5. 客户端拉取设计（重要约束）
- **纯拉取**，但必须**后台异步**执行：打开 Obsidian 后**立即显示缓存/加载态，不阻塞等待拉取完成**
- 每次拉取设**超时**（默认 ~10s）；失败返回**结构化错误**（错误码 + 可读信息），视图内友好回显，不白屏、不静默
- 拉取成功/失败都更新本地缓存与「上次更新时间」

### 6. API 契约（v1）
- `GET  /digest?date=` → 当日 Top N（含排序理由）
- `POST /rating` `{article_id, score, comment, action}` → 反馈入库
- `GET  /profile` → 当前画像（可读可改）
- `GET  /sources` / `PUT /config` → 话题与 RSS 源管理

### 7. 数据模型（SQLite 核心表）
- `articles(id, url_hash, title, summary, embedding, fetched_at)`
- `digests(id, date, article_id, rank, score, reason)`
- `ratings(article_id, score, comment, action, time)`
- `conclusions(id, kind[deductive|inductive], topic, statement, confidence, evidence, updated_at)`
- `coverage(topic, read_count, served_count, last_seen)`
- `config(topics, feeds, weights, exploration_budget)`

## 六、首轮话题与 RSS 源（Agent 主题）

首轮话题：**agent 框架 / agent 开发 / 新模型出现**。初始源清单（`agent` 话题）：

**Agent 框架与开发**
- Simon Willison — `https://simonwillison.net/atom/everything/` （✅ 已验证，hands-on LLM/agent 开发记录）
- Latent Space — `https://www.latent.space/feed` （✅ 已验证，agent 从业者深度内容）
- Lilian Weng — `https://lilianweng.github.io/index.xml` （✅ 已验证，agent 技术长文）
- Hugging Face Blog — `https://huggingface.co/blog/feed.xml` （⚠️ 本地被墙，服务器端可用；smolagents/agent 教程）

**新模型与产业动态**
- arXiv `cs.AI` — `http://export.arxiv.org/rss/cs.AI`（⚠️ 本地偶发 502，源有效；agent 研究论文）
- arXiv `cs.LG` — `http://export.arxiv.org/rss/cs.LG`（✅ 已验证；新模型/方法论文）
- Ben's Bites — `https://www.bensbites.com/feed.xml`（✅ 已验证，每日 AI 新闻）
- VentureBeat AI — `https://venturebeat.com/category/ai/feed/`（✅ 已验证，agent 应用/产业新闻）
- The Verge — `https://www.theverge.com/rss/index.xml`（✅ 已验证，大厂动态）
- OpenAI / Anthropic / Google DeepMind 官方博客 RSS（⚠️ 本地被墙，服务器端可用；新模型发布一手来源）

> ✅ = 本地已核验返回 XML；⚠️ = 本地网络被墙/抖动，但源有效且服务器（海外）可抓取。
> **服务器端实测（2026-08-27）**：9 个源全部可达；`export.arxiv.org`（301）与 `venturebeat.com`（308）为重定向，采集器须跟随重定向，arxiv 已改用 `https://` 直连省一跳。
> 抓取实际在**新加坡服务器**执行，不受本地网络限制；本地测试仅用于确认 URL 有效性。

### 初始 config.yaml 示意
```yaml
topics:
  agent:
    feeds:
      - https://simonwillison.net/atom/everything/
      - https://www.latent.space/feed
      - https://lilianweng.github.io/index.xml
      - http://export.arxiv.org/rss/cs.AI
      - http://export.arxiv.org/rss/cs.LG
      - https://www.bensbites.com/feed.xml
      - https://venturebeat.com/category/ai/feed/
    keywords: [agent, ai agent, agent framework, LLM agent, model release]
exploration_budget: 0.15
digest_size: 8
```

## 七、部署要点

- Docker compose：`api`（FastAPI）+ `db`（SQLite 或 Postgres）+ `honcho`
- Caddy 加反代：`fluxia.top/rssapi → api`
- 定时任务独立容器或 host cron
- 与 VPN 端口隔离；证书沿用 certbot/Let's Encrypt

## 八、期望效果

- **P0 后**：每日 Top 5-10 篇可看，概述准，能打分，画像初见雏形
- **2-4 周**：排序贴合口味，探索引入新方向
- **长期**：越用越懂你、不形成茧房；Win/安卓随时看随手评；自托管数据私有
- **可量化**：打开率、平均评分、评分与排序相关度、画像结论命中率

## 九、分阶段路线

- **P0 竖切打通**：服务端采集→概述排序→digest→插件后台异步拉取显示→打分回传
- **P1**：话题/源配置化 + 排序调优
- **P2**：记忆闭环（Honcho）生效，画像自成长
- **P3**：防茧房调度 + 已读/稍后读落 vault + 可选轻量通知

## 十、待定项 / 已定决策

**已定**
1. 记忆层：**自托管 Honcho** ✅
2. LLM/Embedding：**LLM 用 DeepSeek（json_schema 补丁）+ embedding 用豆包（火山方舟，云端）**；Anthropic 有 key 后可在统一接口接入 ✅
3. 推送：**纯拉取**，且客户端**后台异步拉取 + 超时 + 结构化错误回显** ✅
4. 插件目录：`fluxiaRSS/plugin/` ✅
5. 首轮话题：**agent**（框架/开发/新模型），源清单见第六节，**已在服务器端验证全部可达** ✅

**待办（P0 前确认）**
- Anthropic API key
- 确认 arXiv / 官方博客在服务器端抓取是否稳定
- 插件是否直接装进 Obsidian vault（`fluxiaRSS/plugin` 开发，装进 vault 用）



