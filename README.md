# fluxiaRSS

> 个人 RSS 智读流：每天自动采集你关注话题的文章，用 LLM 做**概述 + 综合排序**，推送到 **Obsidian（Windows / 安卓）**；看完可**打分 / 评论**，反馈回流**长期记忆**以影响下一轮采集与排序，并内置**防信息茧房**调度。

## 特性

- **定时采集**：按话题配置 RSS 源，去重后增量入库
- **AI 概述 + 排序**：对每篇文章生成要点概述，并按相关度 / 偏好 / 新颖度综合排序
- **Obsidian 客户端**：一套插件同时支持 Windows 与安卓；后台异步拉取，带超时与结构化报错
- **反馈闭环**：打分 / 评论回流，持续校准个人偏好
- **防信息茧房**：探索调度，避免越推越窄

## 架构

```
[RSS 源]  →  cron（每天凌晨）
              ▼
[服务端]  采集 → AI 概述/排序 → 生成今日 digest → API
              ▼
[客户端]  Obsidian 插件（Win/安卓）：查看 digest、评分回传
              ▼
          长期记忆（Honcho 式）→ 影响下一轮采集/排序/探索
```

## 快速开始

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # 填入 DEEPSEEK_API_KEY 等
.venv/bin/python -c "from app.pipeline import run_pipeline; print(run_pipeline())"  # 采集
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000                          # 起 API
```

- `GET /api/v1/digest` — 今日 digest
- `POST /api/v1/collect` — 手动触发采集
- `POST /api/v1/rating` — 反馈（P2 接入）

配置见 [`server/.env.example`](server/.env.example)。

## 记忆与自成长

采用 Honcho 式「分层记忆 + 双时间尺度反馈」：

- **事件层**：每条评分/评论/跳过为原子事件，永不删
- **结论层**：`deductive`（事实）与 `inductive`（推断，带置信度、可被推翻）分离
- **dialectic 自纠正**：新证据与旧推断冲突时**重写结论**而非叠权重
- **子模型 (observer, observed)**：建模「对每个主题/来源的认知」，是防茧房的关键

Honcho 自托管部署见 [`server/DEPLOY-HONCHO.md`](server/DEPLOY-HONCHO.md)。

## 防信息茧房

- 建模「已知覆盖」，新颖度 = 与已知区域的向量距离
- 预留探索槽位（低置信试探 + 新方向），由画像驱动而非随机
- 刻意反差：偶尔推送与主偏好相悖的角度，校验画像并破茧房

## 技术栈

- 服务端：Python + FastAPI · feedparser · httpx · SQLite
- 记忆：Honcho（自托管）· Postgres + pgvector
- 客户端：Obsidian 插件（TypeScript）
- LLM：DeepSeek（OpenAI 兼容）；embedding 用火山方舟（豆包）

## 路线图

- **P0**：采集 → 概述排序 → digest → 插件展示 → 打分回传（当前）
- **P1**：话题/源配置化 + 排序调优
- **P2**：记忆闭环（Honcho）生效
- **P3**：防茧房调度 + 已读/稍后读落 vault + 轻量通知

## License

MIT（待定）
