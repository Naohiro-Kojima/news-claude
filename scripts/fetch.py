"""RSSフィードから直近24時間の記事を取得する"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from typing import TypedDict

import feedparser
import yaml

FETCH_TIMEOUT         = 12    # URL全文フェッチのタイムアウト（秒）
FULL_CONTENT_MAX_CHARS = 4500  # 本文の最大文字数（トークン節約）


class Article(TypedDict, total=False):
    title: str
    url: str
    description: str
    published: str   # ISO 8601
    lang: str        # "ja" or "en"
    category: str
    group: str       # 競合グループ識別子（競合プレスリリース用）


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
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.strip()[:400]


def fetch_full_content(url: str, max_chars: int = FULL_CONTENT_MAX_CHARS) -> str:
    """URLにアクセスし、広告・ナビゲーション除去済みの本文テキストを返す。

    trafilatura（記事本文抽出専用）→ requests + BeautifulSoup の順でフォールバック。
    取得失敗・アクセス拒否・ペイウォールの場合は空文字列を返す。
    """
    # Primary: trafilatura
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_recall=True,
            )
            if text and len(text.strip()) > 150:
                text = re.sub(r"\n{3,}", "\n\n", text.strip())
                return text[:max_chars]
    except Exception:
        pass

    # Fallback: requests + BeautifulSoup
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers=headers, allow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "aside", "noscript", "iframe", "form"]):
            tag.decompose()

        # メインコンテンツ要素を優先的に探す
        body = (
            soup.find("article")
            or soup.find(attrs={"role": "main"})
            or soup.find(class_=re.compile(r"\barticle\b|\bcontent\b|\bbody\b|\bmain\b", re.I))
            or soup.find("main")
            or soup.body
        )
        if body:
            text = body.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text.strip()) > 150:
                return text.strip()[:max_chars]
    except Exception:
        pass

    return ""


def fetch_category(category_name: str, feeds: list[dict], hours: int = 24) -> list[Article]:
    """1カテゴリ分のRSSフィードを取得し、直近 hours 時間以内の記事を返す"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles: list[Article] = []

    for feed_cfg in feeds:
        url: str = feed_cfg["url"]
        lang: str = feed_cfg.get("lang", "en")
        group: str = feed_cfg.get("group", "")

        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            print(f"[fetch] WARN: failed to parse {url}")
            continue

        for entry in parsed.entries:
            pub_dt = _parse_entry_time(entry)
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

            article: Article = {
                "title":       title,
                "url":         url_article,
                "description": description,
                "published":   published,
                "lang":        lang,
                "category":    category_name,
                "group":       group,
            }
            articles.append(article)

        time.sleep(0.5)

    articles.sort(key=lambda a: a["published"], reverse=True)
    return articles[:20]


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
