#!/usr/bin/env bash
# fluxiaRSS —— 在服务器上核验 RSS 源可达性（须 SSH 到新加坡服务器执行）
set -u
for u in \
  https://simonwillison.net/atom/everything/ \
  https://www.latent.space/feed \
  https://lilianweng.github.io/index.xml \
  https://export.arxiv.org/rss/cs.AI \
  https://export.arxiv.org/rss/cs.LG \
  https://www.bensbites.com/feed.xml \
  https://venturebeat.com/category/ai/feed/ \
  https://www.theverge.com/rss/index.xml \
  https://huggingface.co/blog/feed.xml ; do
  code=$(curl -sL -o /dev/null -w "%{http_code}" -m 12 "$u")
  xml=$(curl -sL -m 12 "$u" | head -c 200 | grep -oE '<rss|<feed|<rdf' | head -1)
  printf "%-55s http=%s %s\n" "$u" "$code" "$xml"
done
