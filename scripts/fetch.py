"""RSSフィードから直近24時間の記事を取得する"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import TypedDict

import feedparser
import yaml


class Article(TypedDict):
    title: str
    url: str
    description: str
    published: str  # ISO 8601
    lang: str       # "ja" or "en"
    category: str


def _parse_entry_time(entry: feedparser.FeedParserDict) -> datetime | None:
    """エントリの公開日時をUTC awareなdatetimeに変換する。取得できない場合はNone。"""
    time_tuple = (
        getattr(entry, "published_parsed", None)
        or getattr(entry, "updated_parsed", None)
    )
    if time_tuple is None:
        return None
    return datetime(*time_tuple[:6], tzinfo=timezone.utc)


def _clean_text(text: str) -> str:
    """HTMLタグを除去し、先頭400文字に切り詰める"""
    import re
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.strip()[:400]


def fetch_category(category_name: str, feeds: list[dict], hours: int = 24) -> list[Article]:
    """1カテゴリ分のRSSフィードを取得し、直近 hours 時間以内の記事を返す"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles: list[Article] = []

    for feed_cfg in feeds:
        url: str = feed_cfg["url"]
        lang: str = feed_cfg.get("lang", "en")

        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            # フィード取得失敗はスキップ（ログだけ出す）
            print(f"[fetch] WARN: failed to parse {url}")
            continue

        for entry in parsed.entries:
            pub_dt = _parse_entry_time(entry)
            # 日時が取得できない記事は「直近」とみなして含める
            if pub_dt is not None and pub_dt < cutoff:
                continue

            title = _clean_text(getattr(entry, "title", ""))
            description = _clean_text(
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
            )
            url_article = getattr(entry, "link", "")
            published = pub_dt.isoformat() if pub_dt else datetime.now(timezone.utc).isoformat()

            if not title or not url_article:
                continue

            articles.append(
                Article(
                    title=title,
                    url=url_article,
                    description=description,
                    published=published,
                    lang=lang,
                    category=category_name,
                )
            )

        # レートリミット回避
        time.sleep(0.5)

    # 新しい順にソートし、上限10件に制限
    articles.sort(key=lambda a: a["published"], reverse=True)
    return articles[:10]


def fetch_all(config_path: str = "config/feeds.yml", hours: int = 24) -> dict[str, list[Article]]:
    """
    feeds.yml を読み込み、全カテゴリの記事を取得する。

    Returns:
        {カテゴリ名: [Article, ...]} の辞書
    """
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    result: dict[str, list[Article]] = {}
    for cat in config["categories"]:
        name: str = cat["name"]
        print(f"[fetch] Fetching category: {name}")
        articles = fetch_category(name, cat["feeds"], hours=hours)
        print(f"[fetch]   -> {len(articles)} articles")
        result[name] = articles

    return result
