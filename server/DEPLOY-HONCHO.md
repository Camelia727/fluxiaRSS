# Honcho 自托管部署（DeepSeek LLM + 豆包 embedding）

> 目标：在新加坡 2C2G 服务器上自托管 Honcho，作为 fluxiaRSS 的记忆层。
> 参考踩坑文：https://jishuzhan.net/article/2054388137501757442
> 状态：方案定稿，待执行

## 架构

```
[fluxiaRSS 服务] ──HTTP :8000──▶ Honcho Server (Docker)
   ┌───────────────────────────────────────────────┐
   │  api(FastAPI)   deriver(记忆提取)  dialectic(推理) │
   │  database(Postgres+pgvector)   redis(消息队列)    │
   └───────────────┬────────────────────────────────┘
        LLM: DeepSeek API (需 json_schema 补丁)
        Embedding: 豆包/火山方舟 (OpenAI 兼容, 云端)
```

> 运行 key 类配置请参考根目录 server/.env.example（勿提交真实 key）。

## 前置条件
- 服务器已装 Docker + docker compose
- 两个 key：
  - **DeepSeek** API key（LLM）
  - **豆包/火山方舟** API key + **已开通的 embedding 推理接入点 ID**（`ep-xxxx`）
- 内存提醒：2C2G 偏紧，**务必用云端 embedding**，不要本地 ONNX 模型

## 步骤

### 1. 克隆并准备
```bash
git clone https://github.com/plastic-labs/honcho
cd honcho
cp .env.template .env
```

### 2. 配置 DeepSeek 作为 LLM（.env）
```env
# LLM —— DeepSeek（OpenAI 兼容）
LLM_OPENAI_API_KEY=sk-你的deepseek-key

# Deriver - 记忆提取
DERIVER_MODEL_CONFIG__MODEL=deepseek-chat
DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://api.deepseek.com/v1

# Dialectic - 推理回答（所有 level 都配）
DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL=deepseek-chat
DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL=https://api.deepseek.com/v1
# （若有 medium/high 同理）

# Summary / Dream 等其余 MODEL_CONFIG 段同样指向 deepseek-chat
```

### 3. 配置豆包 embedding（.env）
用你**已开通的推理接入点 ID** 作为 MODEL：
```env
# Embedding —— 豆包/火山方舟（OpenAI 兼容，云端）
EMBEDDING_MODEL_CONFIG__TRANSPORT=openai
EMBEDDING_MODEL_CONFIG__MODEL=ep-你的接入点ID
EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=https://ark.cn-beijing.volces.com/api/v3
EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY=<你的豆包/火山方舟 key>
```
> 接入点 ID 以火山方舟控制台为准（`ep-` 开头）；也可直接用模型名（如 `doubao-embedding`）。

### 4. 打 json_schema → json_object 补丁（DeepSeek 必需）
DeepSeek 不支持 `json_schema`，只支持 `json_object`。改 Honcho 源码
`src/llm/backends/openai.py` 的 `_create_structured_response`，加 fallback：
```python
async def _create_structured_response(self, *, params, response_format):
    structured_params = dict(params)
    structured_params["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": response_format.__name__,
            "schema": response_format.model_json_schema(),
        },
    }
    try:
        return await self._client.chat.completions.create(**structured_params)
    except BadRequestError:  # DeepSeek 不支持 json_schema，回退 json_object
        structured_params["response_format"] = {"type": "json_object"}
        return await self._client.chat.completions.create(**structured_params)
```

### 5. 构建并启动
```bash
docker compose build api deriver
docker compose up -d --force-recreate api deriver
```

## 验证
```bash
docker compose ps                 # api/deriver/database/redis 均 Up
docker compose logs deriver | tail   # 应无 BadRequestError / 401
# 本地冒烟：起 fluxiaRSS 后通过 honcho SDK 写一条 message，再读 representation
```

## 资源与注意事项
- Honcho 全家桶（无本地 embedding）约 450~500MB；加上 fluxiaRSS(~150MB) + 现有 Caddy/Hysteria2，2C2G 偏紧但可行
- **不要**本地部署 ONNX embedding（~900MB 会 OOM）
- 火山方舟/DeepSeek 都走海外→国内跨境，调用小、频率低，延迟可接受
- 若日后拿到 Anthropic key，可切 `LLM_ANTHROPIC_API_KEY`，且无需 json_schema 补丁（native 结构化输出）

