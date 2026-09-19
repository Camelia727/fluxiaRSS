# 002 - Zone 分区方案

> 目标：fluxiaRSS 支持完全自由的分区，每个区独立配置 RSS 源/关键词/Honcho/digest 参数。
> 状态：已实现（服务端 + Obsidian 插件）
> 日期：2026-09-17（方案） / 2026-09-19（实现）

---

## 一、问题

fluxiaRSS 目前是一套全局配置（全局 FEEDS/全局 KEYWORDS/全局 Honcho workspace），
无法同时服务两个内容方向（如 agent 技术 + 小黑盒创作灵感）。两个方向共用同一池文章、
同一份关键词、同一份 Honcho 画像，互相干扰。

## 二、方案：Zone 是一等公民

### 2.1 数据模型

Zone 本身是 REST 资源，存 DB，通过 API 自由增删改。每个 Zone 自带全套配置。

zones 表：
- id: TEXT PK           -- 区标识，如 default / agent / creator
- display: TEXT         -- 展示名
- feeds: JSON TEXT      -- RSS 源列表，与当前 FEEDS 格式一致
- keywords: JSON TEXT   -- 关键词列表
- config: JSON TEXT     -- digest_size / fresh_window_hours / honcho 等
- created_at: TEXT

config JSON 结构示例（键均可选，缺省回退全局 config）：
{
  "digest_size": 8,
  "fresh_window_hours": 24,
  "max_per_source": 3,
  "research_max": 2,
  "per_feed_cap": 20,
  "max_age_days": 2,
  "source_max_age_days": {"Cloudflare AI": 30},
  "summarize_topic": "该区概述的主题定位",
  "honcho_workspace": "fluxiars_agent",
  "honcho_session": "reading"
}

### 2.2 API 设计

Zone 资源：
  GET    /api/v1/zones
  POST   /api/v1/zones
  GET    /api/v1/zones/{zone}
  PUT    /api/v1/zones/{zone}
  DELETE /api/v1/zones/{zone}

区内操作：
  GET    /api/v1/zones/{zone}/digest
  POST   /api/v1/zones/{zone}/collect
  POST   /api/v1/zones/{zone}/rating
  GET    /api/v1/zones/{zone}/profile
  GET    /api/v1/zones/{zone}/sources
  POST   /api/v1/zones/{zone}/sources
  DELETE /api/v1/zones/{zone}/sources?url=

向后兼容：现有 /api/v1/{资源} 自动等价于 /api/v1/zones/default/{资源}

### 2.3 DB 迁移

首次迁移逻辑（init_db() 内）：
1. zones 表不存在则创建
2. zones 表为空且旧 sources 表有数据 -> 打包为 default zone 的 feeds
3. zones 为空且 sources 表也无数据 -> 创建空 default zone
4. 旧 sources 表不再写入

---

## 三、改动清单

### 3.1 config.py
- 新增 ZONES_SEED 环境变量（JSON）
- 保留现有全局常量，作为 default zone 初始化依据

### 3.2 db.py
改动函数：init_db, list_sources, add_source, remove_source, insert_article,
          list_articles, preference_tokens, source_stats, source_trust
新增函数：get_zone, list_zones, create_zone, update_zone, delete_zone, get_zone_keywords
废弃：sync_builtin_sources

### 3.3 relevance.py
relevant(title, desc, keywords=None) 可注入关键词

### 3.4 collector.py + pipeline.py
collect_candidates(zone) 透传 zone
pipeline.run_pipeline(zone) 透传 zone

### 3.5 ranking.py
rank_articles(articles, top_n, zone) 新增 zone 参数
使用 zone 获取该区关键词 + Honcho 画像

### 3.6 honcho_client.py
enabled(zone), record_rating(zone), get_profile(zone) 新增 zone 参数
workspace 从 zones.config.honcho_workspace 读取

### 3.7 main.py
新增 7 个 zone CRUD 端点 + 7 个 per-zone 操作端点
旧端点做别名

### 3.8 scheduler.py
_job() 遍历 list_zones()，串行 run_pipeline(zone=zid)

### 3.9 schemas.py
新增 ZoneIn（含必填 id，正则 ^[a-z0-9_-]{1,32}$）/ ZoneOut / ZoneDetail 模型

---

## 四、实现顺序

P1: db.py      -> zones 表 + 迁移 + zone 查询函数
P2: config.py  -> ZONES_SEED
P3: relevance.py -> relevant() 可注入 keywords
P4: collector + pipeline -> zone 透传
P5: ranking.py -> zone 感知排名
P6: honcho_client.py -> zone 感知
P7: main.py    -> zone CRUD + per-zone 端点
P8: scheduler.py -> 遍历 zone
P9: schemas.py -> Zone 模型

---

## 五、向后兼容

- 旧端点 /api/v1/digest 等价于 /api/v1/zones/default/digest
- 旧 sources 表保留但不再写入
- 旧 DB 原地升级，不丢数据
- Plugin 无需修改

---

## 六、边界情况

- default 被删除：API 禁止（除非只剩它一个）
- 不存在 zone：返回 404
- 跨 zone 重名 RSS 源：不同 zone 独立存储
- 空区采集：feeds=[] -> 返回空
- 并发采集：串行执行


---

## 七、实现记录（2026-09-19）

实现与原方案的差异：

- 新增 `db.zone_params(zone)` 作为区内参数的唯一出口：digest_size / fresh_window_hours /
  max_per_source / research_max / per_feed_cap / max_age_days / source_max_age_days /
  summarize_topic / honcho_workspace / honcho_session。未提供的键回退全局 config，
  因此 default 区行为与升级前一致。
- **原方案遗漏**：zone.config 只写不读；API 建区把 display 当 id；Honcho 的 workspace
  get-or-create 与 session 仍写死全局值。三处均已修。
- 区标识 id 由创建时指定且不可变；display 仅为展示名。
- 删除语义：最后一个区不可删（否则 API 无从下手）；default 无额外保护。
- 插件新增分区选择器与 `分区 · <展示名>` 徽标；非 default 区的笔记落到
  `{digestDir}/{zone}/{date}.md`，default 区沿用原路径。
- 自动化验证：`server/zone_selftest.py`（31 项断言，临时 DB，不触碰真实数据）。
