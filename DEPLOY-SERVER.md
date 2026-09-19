# 服务器更新文档

> 用途：fluxiaRSS 服务端部署与更新指引
> 适用：Ubuntu 服务器 @ fluxia.top
> 更新：2026-09-17

---

## 一、快速更新

```bash
# 1. 拉取最新代码
cd /opt/fluxiaRSS
git pull origin master

# 2. 激活虚拟环境
source .venv/bin/activate

# 3. 安装依赖（如果有新增）
pip install -r server/requirements.txt

# 4. 重启 API 服务
sudo systemctl restart fluxiars-api

# 5. 检查状态
sudo systemctl status fluxiars-api --no-pager
curl -s http://localhost:8000/health | python3 -m json.tool
```

---

## 二、首次部署

### 2.1 环境要求
- Python 3.10+
- pip / venv

### 2.2 目录结构
```
/opt/fluxiaRSS/
├── server/
│   ├── .env              # 服务端配置（密钥，不入库）
│   ├── .env.example      # 配置模板
│   ├── app/              # Python 源码
│   └── fluxiars.db       # SQLite 数据库（自动创建）
├── plugin/               # Obsidian 插件
├── scripts/              # 运维脚本
└── README.md
```

### 2.3 部署步骤
```bash
# 克隆仓库
git clone <仓库地址> /opt/fluxiaRSS
cd /opt/fluxiaRSS

# Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt

# 配置
cp server/.env.example server/.env
# 编辑 .env 填入 DEEPSEEK_API_KEY 等密钥

# 启动 API（测试）
cd server
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 注册 systemd 服务
sudo tee /etc/systemd/system/fluxiars-api.service << 'SERVICE'
[Unit]
Description=fluxiaRSS API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fluxiaRSS/server
ExecStart=/opt/fluxiaRSS/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=/opt/fluxiaRSS/server/.env

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable --now fluxiars-api
```

---

## 三、Zone 分区配置

### 3.1 通过环境变量播种预置区（推荐）

编辑 `/opt/fluxiaRSS/server/.env`，添加：

```bash
FLUXIARSS_ZONES={"creator":{"display":"创作灵感·海外见闻","feeds":[{"name":"Waxy.org Links","url":"https://waxy.org/category/links/feed/","topic":"creator"},{"name":"Kottke.org","url":"https://feeds.kottke.org/main","topic":"creator"},{"name":"Marginal Revolution","url":"https://marginalrevolution.com/feed","topic":"creator"}],"keywords":[],"config":{"digest_size":5,"fresh_window_hours":48,"max_age_days":30,"honcho_workspace":"fluxiars_creator","honcho_session":"reading","summarize_topic":"海外互联网见闻与创作灵感；只做轻量信息差，不做技术深度。"}}}
```

重启后首次启动会自动创建名为 `creator` 的分区。
如果已有数据库（已有 `default` 区），新建分区不会影响现有数据。

### 3.2 通过 API 创建区

也可以启动后通过 API 创建：

```bash
curl -X POST http://localhost:8000/api/v1/zones \
  -H "Content-Type: application/json" \
  -d '{
    "id": "creator",
    "display": "创作灵感·海外见闻",
    "feeds": [
      {"name": "Waxy.org Links", "url": "https://waxy.org/category/links/feed/"},
      {"name": "Kottke.org", "url": "https://feeds.kottke.org/main"},
      {"name": "Marginal Revolution", "url": "https://marginalrevolution.com/feed"}
    ],
    "keywords": [],
    "config": {
      "digest_size": 5,
      "fresh_window_hours": 48,
      "max_age_days": 30,
      "honcho_workspace": "fluxiars_creator",
      "summarize_topic": "海外互联网见闻与创作灵感；只做轻量信息差，不做技术深度。"
    }
  }'
```

`id` 是 URL 路径里的区标识，只能用小写字母/数字/下划线/短横线（1-32 位），
必填且创建后不可改；`display` 是展示名，可用中文。缺 `id` 或 id 非法返回 422，
id 重复返回 409。

### 3.3 使用 API 验证分区

```bash
# 列出分区
curl -s http://localhost:8000/api/v1/zones | python3 -m json.tool

# 查看 creator 分区详情
curl -s http://localhost:8000/api/v1/zones/creator | python3 -m json.tool

# 单独采集 creator 分区
curl -X POST http://localhost:8000/api/v1/zones/creator/collect

# 获取 creator 分区的 digest
curl -s http://localhost:8000/api/v1/zones/creator/digest | python3 -m json.tool

# 查看 creator 分区的 RSS 源列表
curl -s http://localhost:8000/api/v1/zones/creator/sources | python3 -m json.tool
```

### 3.4 向已有分区添加 RSS 源

```bash
curl -X POST http://localhost:8000/api/v1/zones/creator/sources \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/feed.xml", "name": "My Feed"}'
```

### 3.5 区内可配置项

`config` 里的键都是可选的，**未提供就回退全局默认**，因此 default 区行为与升级前一致。
常用键：

| 键 | 作用 | 缺省回退 |
|----|------|---------|
| `digest_size` | 该区每日 digest 篇数（`?top=` 可临时覆盖） | `DIGEST_SIZE` |
| `fresh_window_hours` | 「今日新鲜池」窗口小时数 | `FRESH_WINDOW_HOURS` |
| `max_per_source` | digest 每源最多保留篇数，防单源霸屏 | `DIGEST_MAX_PER_SOURCE` |
| `research_max` | digest 中 research 类硬上限 | `RESEARCH_MAX_IN_DIGEST` |
| `per_feed_cap` | 采集期单源单轮最大新增数 | `PER_FEED_CAP` |
| `max_age_days` | 采集时效：超过该天数的旧文跳过 | `MAX_AGE_DAYS` |
| `source_max_age_days` | 按源覆盖时效窗口（JSON），慢更新源用 | `SOURCE_MAX_AGE_DAYS` |
| `summarize_topic` | 概述时注入的主题定位（软约束） | `SUMMARIZE_TOPIC` |
| `honcho_workspace` | 该区 Honcho workspace（画像隔离） | `HONCHO_WORKSPACE_ID` |
| `honcho_session` | 该区 Honcho session | `HONCHO_SESSION_ID` |

创作类源（Waxy 等）更新慢、平均 3 天一发，全局 `MAX_AGE_DAYS=2` 会把它们几乎全挡掉，
所以创作区要显式放宽 `max_age_days`（示例给 30）。

### 3.6 配置 Honcho 记忆（每个分区独立）

```bash
# 启动 Honcho 后，无需特殊配置
# creator 分区自动使用配置的 honcho_workspace="fluxiars_creator"
# default 分区使用环境变量 HONCHO_WORKSPACE_ID
```

---

## 四、API 端点一览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 探活 |
| `/api/v1/zones` | GET | 列出所有分区 |
| `/api/v1/zones` | POST | 创建分区 |
| `/api/v1/zones/{zone}` | GET | 分区详情 |
| `/api/v1/zones/{zone}` | PUT | 更新分区 |
| `/api/v1/zones/{zone}` | DELETE | 删除分区 |
| `/api/v1/zones/{zone}/digest` | GET | 分区 digest |
| `/api/v1/zones/{zone}/collect` | POST | 分区采集 |
| `/api/v1/zones/{zone}/rating` | POST | 分区评分 |
| `/api/v1/zones/{zone}/profile` | GET | 分区 Honcho 画像 |
| `/api/v1/zones/{zone}/sources` | GET | 分区 RSS 源列表 |
| `/api/v1/zones/{zone}/sources` | POST | 添加 RSS 源 |
| `/api/v1/zones/{zone}/sources` | DELETE | 移除 RSS 源 |
| `/api/v1/digest` | GET | 等价于 `/zones/default/digest` |
| `/api/v1/collect` | POST | 等价于 `/zones/default/collect` |
| `/api/v1/rating` | POST | 等价于 `/zones/default/rating` |
| `/api/v1/profile` | GET | 等价于 `/zones/default/profile` |
| `/api/v1/sources` | GET/POST/DELETE | 等价于 `/zones/default/sources` |

---

## 四点五、Obsidian 插件（分区设置）

插件已支持分区切换，无需改代码：

1. 同步 `plugin/` 三个文件（`main.js`、`style.css`、`manifest.json`）到 vault 的
   `.obsidian/plugins/fluxiars-digest/`，重启 Obsidian 或重载插件。
2. 打开插件设置 → 「分区」→ 点「🔄 拉取分区」，下拉会列出服务端所有分区
   （形如 `创作灵感·海外见闻（creator）`）。
3. 选择要阅读的分区即可。切换后：
   - 笔记路径变为 `{digestDir}/{zone}/{date}.md`（default 区仍是 `{digestDir}/{date}.md`，老笔记不受影响）
   - 卡片头部显示 `分区 · <展示名>` 徽标
   - 「RSS 源」一节只增删当前分区的源
   - 打分/评论写入该分区的 Honcho workspace
4. 插件的「每日篇数」是 `?top=` 覆盖值；不填时以服务端该分区的 `digest_size` 为准。
   若要严格跟随分区配置，把它调成与分区 `digest_size` 一致即可。

## 五、创作分区配置参考

### 5.1 推荐 RSS 源（海外网络见闻方向）

| 源名 | URL | 更新频率 | 内容调性 |
|------|-----|---------|---------|
| Waxy.org Links | https://waxy.org/category/links/feed/ | 不定期，1-6条/次 | 极简链接博客，纯信息差 |
| Kottke.org | https://feeds.kottke.org/main | 每日，6-11条 | 文化/设计/科技，轻松为主 |
| Marginal Revolution | https://marginalrevolution.com/feed | 每日，3-5条 | 经济学+文化随笔 |

### 5.2 推荐配置

```json
{
  "digest_size": 5,
  "fresh_window_hours": 48,
  "honcho_workspace": "fluxiars_creator"
}
```

创作分区 **不设关键词过滤**（keywords=[]），依赖人工从 RSS 原文中选题。

---

## 六、迁移注意事项

- 旧版 `sources` 表在首次启动时自动迁移到 `default` 区的 feeds JSON 中
- 旧版 API 端点（`/api/v1/digest` 等）保持可用，等价于 `zone=default`
- Obsidian 插件无需修改，继续调用旧端点即可
- 如需在插件中支持分区选择，后续将 zone 参数暴露到插件配置
