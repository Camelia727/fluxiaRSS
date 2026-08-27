"""fluxiaRSS 服务端配置 —— 从环境变量读取，密钥不入库。

复制 server/.env.example 为 .env 并按需修改；运行前加载环境变量。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 显式加载 server 目录下的 .env（含密钥，不入库）；override=True 让 .env 成为权威
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

APP_NAME = os.getenv("FLUXIARSS_APP_NAME", "fluxiaRSS")
API_PREFIX = "/api/v1"
DEFAULT_DIGEST_SIZE = int(os.getenv("DIGEST_SIZE", "8"))
EXPLORATION_BUDGET = float(os.getenv("EXPLORATION_BUDGET", "0.15"))

# 数据库（本地文件）
DB_PATH = os.getenv("FLUXIARSS_DB", "fluxiars.db")

# 话题与源（可在本地/部署时按需调整）
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
PER_FEED_CAP = int(os.getenv("PER_FEED_CAP", "20"))

# 概述并发数（凌晨定时任务，8 并发足够）
SUMMARY_WORKERS = int(os.getenv("SUMMARY_WORKERS", "8"))

# DeepSeek（OpenAI 兼容）
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT = int(os.getenv("DEEPSEEK_TIMEOUT", "40"))

# Honcho（记忆层）
HONCHO_ENABLED = os.getenv("HONCHO_ENABLED", "0") == "1"
HONCHO_BASE_URL = os.getenv("HONCHO_BASE_URL", "http://localhost:8001")
HONCHO_WORKSPACE_ID = os.getenv("HONCHO_WORKSPACE_ID", "")
HONCHO_PEER_ID = os.getenv("HONCHO_PEER_ID", "user")
HONCHO_SESSION_ID = os.getenv("HONCHO_SESSION_ID", "reading")




