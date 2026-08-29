"""简单规则分类器：给文章打 research / practical / news / other 标签。

设计取向：对 research **保守**——只把明显的研究/评测/综述类内容标为 research。
因为 digest 会按 RESEARCH_MAX_IN_DIGEST 对 research 类硬封顶，误标会把教程/概念文
挤进封顶名额、挤占用户想要的实践内容。宁可漏标（研究文落入 other，不封顶，反正
研究文章量本来就少），不可误标。

规则（确定性、零 LLM 成本）：
1. 来源名含 arxiv → research（兜底，防未来加回 arXiv 源）。
2. 命中强实践信号 → practical（教程/上手类即使提到 benchmark 仍是实践）。
3. 否则按研究词 / 新闻词命中数取高者；两边都无 → other。

关键词表集中在文件顶部，按口味调整即可。
"""
from __future__ import annotations

# 强实践信号：命中即视为 practical
_STRONG_PRACTICAL = [
    "tutorial", "how to", "getting started", "hands-on", "step-by-step",
    "cookbook", "quickstart", "smolagents", "deep dive", "crash course",
    # 中文
    "教程", "入门", "上手", "实战", "手把手", "从零",
]

# 研究前沿信号（保守）：仅出现这些强研究词才计分。
# 刻意排除 framework / tool use / evaluation / dataset / training 等
# 常见于教程/概念文的宽泛词，避免误标。
_RESEARCH = [
    "arxiv", "preprint",
    "benchmark", "benchmarks", "benchmarking",
    "baseline", "ablation", "sota", "state-of-the-art",
    "grpo", "ppo", "mcts", "rlvr", "reinforcement learning",
    "self-improvement", "post-training", "pre-training", "pretraining",
    "fine-tuning", "finetuning", "distillation", "scaling law",
    "test-time", "inference-time", "reward hacking", "reward model",
    "evaluation suite", "survey", "we propose",
    # 中文（只保留明确指向论文/综述的词，强化学习/评测等通用词不收录）
    "论文", "综述",
]

# 行业新闻信号：按命中数计分
_NEWS = [
    "funding", "raises", "acquisition", "acquires", "billion", "million",
    "lawsuit", "court", "revenue", "stock", "ipo", "securities",
    "blacklisted", "blacklist",
    # 中文
    "融资", "收购", "股价", "上市", "营收", "进账", "裁员",
]


def _hits(text: str, kws: list[str]) -> int:
    return sum(1 for k in kws if k in text)


def classify(title: str, desc: str, source: str) -> str:
    """按标题+正文+来源返回类别：research / practical / news / other。"""
    src = (source or "").lower()
    if "arxiv" in src:
        return "research"
    hay = f"{title or ''} {desc or ''}".lower()
    if _hits(hay, _STRONG_PRACTICAL) > 0:
        return "practical"
    n_r = _hits(hay, _RESEARCH)
    n_n = _hits(hay, _NEWS)
    if n_r == 0 and n_n == 0:
        return "other"
    return "research" if n_r >= n_n else "news"
