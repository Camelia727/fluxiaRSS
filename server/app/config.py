"""fluxiaRSS 服务端配置。"""
from __future__ import annotations

APP_NAME = "fluxiaRSS"
API_PREFIX = "/api/v1"
DEFAULT_DIGEST_SIZE = 8
EXPLORATION_BUDGET = 0.15

# 数据库
DB_PATH = "fluxiars.db"

# 话题与源
KEYWORDS = ["agent", "ai agent", "agent framework", "llm agent", "model release", "agents"]

FEEDS = [
    {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "topic": "agent"},
    {"name": "Latent Space", "url": "https://www.latent.space/feed", "topic": "agent"},
    {"name": "Lilian Weng", "url": "https://lilianweng.github.io/index.xml", "topic": "agent"},
    {"name": "arXiv cs.AI", "url": "https://export.arxiv.org/rss/cs.AI", "topic": "agent"},
    {"name": "arXiv cs.LG", "url": "https://export.arxiv.org/rss/cs.LG", "topic": "agent"},
    {"name": "Ben's Bites", "url": "https://www.bensbites.com/feed.xml", "topic": "agent"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "topic": "agent"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "topic": "agent"},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "topic": "agent"},
]

# 采集限制：单源单轮最多新增条数（防 arXiv 等大源刷库）
PER_FEED_CAP = 20

# DeepSeek（OpenAI 兼容）
DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TIMEOUT = 40
