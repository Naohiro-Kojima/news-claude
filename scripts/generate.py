"""精査済み記事データからindex.htmlを生成する"""

from __future__ import annotations

import glob as _glob
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from jinja2 import Environment, BaseLoader

from process import ProcessedArticle

JST = timezone(timedelta(hours=9))

# Gemini カテゴリキー → (parent_tab, sub_tab, sub3_tab)
_CAT_MAP: dict[str, tuple[str, str | None, str | None]] = {
    "ai_social":        ("ai",       "social",      None),
    "ai_press":         ("ai",       "press",       None),
    "ai_academic":      ("ai",       "academic",    None),
    "neuro_social":     ("neuro",    "social",      None),
    "neuro_press":      ("neuro",    "press",       None),
    "neuro_embodiment": ("neuro",    "research",    "embodiment"),
    "neuro_psychology": ("neuro",    "research",    "psychology"),
    "neuro_ai":         ("neuro_ai", None,          None),
    "hr_social":        ("hr",       "social",      None),
    "hr_press":         ("hr",       "press",       None),
    "hr_academic":      ("hr",       "academic",    None),
}

_HOT_CATS: frozenset[str] = frozenset({
    "ai_social", "ai_press", "ai_academic",
    "hr_social", "hr_press",
})

_DOMAIN_TO_SOURCE: dict[str, str] = {
    "techcrunch.com":           "TechCrunch",
    "technologyreview.com":     "MIT Tech Review",
    "theverge.com":             "The Verge",
    "wired.com":                "WIRED",
    "venturebeat.com":          "VentureBeat",
    "arstechnica.com":          "Ars Technica",
    "zdnet.com":                "ZDNet",
    "bloomberg.com":            "Bloomberg",
    "wsj.com":                  "WSJ",
    "nytimes.com":              "NY Times",
    "ft.com":                   "Financial Times",
    "theatlantic.com":          "The Atlantic",
    "economist.com":            "The Economist",
    "nature.com":               "Nature",
    "science.org":              "Science",
    "cell.com":                 "Cell",
    "pnas.org":                 "PNAS",
    "arxiv.org":                "arXiv",
    "biorxiv.org":              "bioRxiv",
    "openai.com":               "OpenAI",
    "anthropic.com":            "Anthropic",
    "deepmind.google":          "DeepMind",
    "blog.google":              "Google",
    "ai.googleblog.com":        "Google AI Blog",
    "microsoft.com":            "Microsoft",
    "research.microsoft.com":   "MS Research",
    "huggingface.co":           "Hugging Face",
    "ieee.org":                 "IEEE",
    "frontiersin.org":          "Frontiers",
    "plos.org":                 "PLOS",
    "springer.com":             "Springer",
    "sciencedirect.com":        "ScienceDirect",
    "eurekalert.org":           "EurekAlert",
    "medicalxpress.com":        "MedicalXpress",
    "phys.org":                 "Phys.org",
    "neurosciencenews.com":     "Neuroscience News",
    "psychologytoday.com":      "Psychology Today",
    "scitechdaily.com":         "SciTechDaily",
    "nih.gov":                  "NIH",
    "medium.com":               "Medium",
    "towardsdatascience.com":   "Towards DS",
    "github.com":               "GitHub",
}


def _extract_source(url: str) -> str:
    """URL のホスト名から表示用ソース名を返す。マッピングになければドメイン第2レベルを使う。"""
    try:
        host = urlparse(url).hostname or ""
        host = host.lstrip("www.")
        if host in _DOMAIN_TO_SOURCE:
            return _DOMAIN_TO_SOURCE[host]
        for domain, name in _DOMAIN_TO_SOURCE.items():
            if host.endswith("." + domain) or host == domain:
                return name
        parts = host.split(".")
        return parts[-2].capitalize() if len(parts) >= 2 else host
    except Exception:
        return ""


_CATEGORY_LABELS: dict[str, str] = {
    "ai_social":        "AI · 社会実装",
    "ai_press":         "AI · プレス",
    "ai_academic":      "AI · 学術",
    "neuro_social":     "脳科学 · 社会",
    "neuro_press":      "脳科学 · プレス",
    "neuro_embodiment": "身体性",
    "neuro_psychology": "心理 · 認知",
    "neuro_ai":         "脳科学 × AI",
    "hr_social":        "組織人事 · 実装",
    "hr_press":         "組織人事 · プレス",
    "hr_academic":      "組織人事 · 研究",
}


def _impact_score(article: dict) -> float:
    """記事の impact フィールドを返す。未設定時は hot フラグ + URL ハッシュでフォールバック。"""
    stored = article.get("impact")
    if stored is not None:
        try:
            v = float(stored)
            if 0.0 < v <= 5.0:
                return round(v * 10) / 10
        except (TypeError, ValueError):
            pass
    if article.get("hot"):
        return 5.0
    url = article.get("url", "")
    h = int(hashlib.md5(url.encode()).hexdigest(), 16)
    return float((h % 3) + 2)  # 2.0, 3.0, 4.0 のいずれか（決定論的）


# ── Hot Topics 定数 ───────────────────────────────────────────────────────────
HOT_SCORE_THRESHOLD: float = 4.5
HOT_TOPICS_MAX: int = 6
HOT_TOPICS_MAX_AGE_DAYS: int = 14


# ── Hot Topics 永続化ヘルパー ─────────────────────────────────────────────────

def _hot_topics_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "hot_topics.json")


def load_hot_topics(cache_dir: str) -> list[dict]:
    path = _hot_topics_path(cache_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_hot_topics(cache_dir: str, articles: list[dict]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    with open(_hot_topics_path(cache_dir), "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def update_hot_topics(cache_dir: str, today_articles: list[dict]) -> list[dict]:
    """today_articles からスコア閾値以上の記事を追加し、期限切れを除去して保存する。"""
    existing = load_hot_topics(cache_dir)
    existing_urls = {a["url"] for a in existing}
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    cutoff = datetime.now(JST).date() - timedelta(days=HOT_TOPICS_MAX_AGE_DAYS)

    for a in today_articles:
        if a["url"] not in existing_urls and a.get("impact", 0.0) >= HOT_SCORE_THRESHOLD:
            existing.append({**a, "added_date": today_str})
            existing_urls.add(a["url"])

    # 期限切れ除去
    kept = []
    for a in existing:
        try:
            added = datetime.strptime(a.get("added_date", today_str), "%Y-%m-%d").date()
        except ValueError:
            added = datetime.now(JST).date()
        if added >= cutoff:
            kept.append(a)

    kept.sort(key=lambda a: (a.get("impact", 0.0), a.get("added_date", "")), reverse=True)
    result = kept[:HOT_TOPICS_MAX]
    save_hot_topics(cache_dir, result)
    return result


def _enrich_articles(processed: dict) -> list[dict]:
    """processed dict を flatten してインパクトスコア・カテゴリラベル・JST日時を付与する。"""
    all_articles: list[dict] = []
    for articles in processed.values():
        for a in articles:
            enriched = {
                **a,
                "published_jst":  _format_published(a["published"]),
                "impact":         _impact_score(a),
                "category_label": _CATEGORY_LABELS.get(a.get("category", ""), a.get("category", "")),
                "source":         a.get("source") or _extract_source(a["url"]),
            }
            all_articles.append(enriched)
    return all_articles


HTML_TEMPLATE = """\
{%- macro render_stars(impact) -%}
<span class="impact-stars" title="インパクト {{ impact }}/5">
  {%- for i in range(1, 6) -%}<span class="star {{ 'filled' if i <= impact|float else 'empty' }}">★</span>{%- endfor -%}
  <span class="impact-val">{{ "%.1f"|format(impact|float) }}</span>
</span>
{%- endmacro -%}
{%- macro render_card(article, extra_class='') -%}
<article class="card {{ extra_class }}" data-url="{{ article.url }}">
  <span class="badge-new">NEW</span>
  <div class="card-meta">
    <span class="card-tag">{{ article.category_label }}</span>
    {%- if article.source %}<span class="card-source">{{ article.source }}</span>{% endif %}
    {{ render_stars(article.impact) }}
  </div>
  <div class="card-title">
    <a href="{{ article.url }}" target="_blank" rel="noopener">{{ article.title_ja }}</a>
  </div>
  {%- if article.hashtags %}
  <div class="card-hashtags">
    {%- for tag in article.hashtags %}<span class="hashtag">{{ tag }}</span>{% endfor %}
  </div>
  {%- endif %}
  <div class="section-label-summary">Abstract</div>
  <p class="card-summary exp-text">{{ article.summary }}</p>
  <button class="exp-btn" onclick="toggleExp(this)">続きを読む...</button>
  <div class="section-label-insight">Insight</div>
  <div class="card-insight">
    <div class="exp-text">{{ article.insight }}</div>
    <button class="exp-btn" onclick="toggleExp(this)">続きを読む...</button>
  </div>
  <div class="card-footer">
    <span class="card-date">{{ article.published_jst }}</span>
    <a class="read-link" href="{{ article.url }}" target="_blank" rel="noopener">原文 →</a>
  </div>
</article>
{%- endmacro -%}
{%- macro render_panel(articles) -%}
<div class="card-grid">
{% if articles %}{% for a in articles %}{{ render_card(a) }}{% endfor %}{% else %}<div class="empty">本日の注目記事はありません</div>{% endif %}
</div>
{%- endmacro -%}
<!DOCTYPE html>
<html lang="ja" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Human Science Insights</title>
  <style>
    /* ===== CSS Custom Properties ===== */
    :root {
      --bg:               #fcfce8;
      --surface:          #ffffff;
      --surface-insight:  #fff0f3;
      --border:           #e8e0d5;
      --border-subtle:    #eeeed8;
      --accent:           #cc1340;
      --accent2:          #a80f34;
      --hot-accent:       #cc1340;
      --text:             #2a2a2a;
      --text2:            #5a4448;
      --muted:            #9e8e92;
      --star-filled:      #c8960c;
      --star-empty:       #ddd5cc;
      --tag-bg:           #fce8ed;
      --tag-text:         #cc1340;
      --insight-bg:       #fff0f3;
      --insight-border:   #cc1340;
      --hot-bg:           #fff5f7;
      --hot-border:       #cc1340;
      --badge-bg:         #cc1340;
      --header-bg:        #cc1340;
      --header-text:      #ffffff;
      --subnav-bg:        #fafadf;
      --subnav-border:    #e8e0d5;
      --radius:           8px;
    }
    [data-theme="dark"] {
      --bg:               #00212b;
      --surface:          #003847;
      --surface-insight:  #00404f;
      --border:           #005566;
      --border-subtle:    #003040;
      --accent:           #5bc8e0;
      --accent2:          #7dddf0;
      --hot-accent:       #ff7070;
      --text:             #e0e0e0;
      --text2:            #a8c8d4;
      --muted:            #5a8090;
      --star-filled:      #f0c040;
      --star-empty:       #003345;
      --tag-bg:           #004555;
      --tag-text:         #7dddf0;
      --insight-bg:       #003040;
      --insight-border:   #5bc8e0;
      --hot-bg:           #002030;
      --hot-border:       #ff7070;
      --badge-bg:         #5bc8e0;
      --header-bg:        #001820;
      --header-text:      #e0e0e0;
      --subnav-bg:        #00212b;
      --subnav-border:    #005566;
    }

    /* ===== Reset & Base ===== */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Yu Gothic', 'YuGothic', 'Hiragino Sans', 'Noto Sans JP', sans-serif;
      font-size: 15px;
      line-height: 1.72;
      min-height: 100vh;
      transition: background 0.25s, color 0.25s;
    }

    /* ===== Header ===== */
    header {
      background: var(--header-bg);
      color: var(--header-text);
      padding: 10px 28px 0;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 2px 10px rgba(0,0,0,0.18);
      will-change: transform;
      transition: transform 0.28s ease, background 0.25s;
    }
    header.header--hidden {
      transform: translateY(-100%);
    }
    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .header-brand { display: flex; flex-direction: column; }
    h1 {
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.04em;
      color: var(--header-text);
      font-family: 'Yu Gothic', 'YuGothic', -apple-system, 'Helvetica Neue', sans-serif;
      line-height: 1.2;
    }
    .header-updated {
      font-size: 10.5px;
      color: rgba(255,255,255,0.42);
      margin-top: 2px;
      font-family: -apple-system, sans-serif;
    }

    /* ===== Theme Toggle ===== */
    .theme-toggle {
      flex-shrink: 0;
      background: rgba(255,255,255,0.10);
      border: 1px solid rgba(255,255,255,0.22);
      color: rgba(255,255,255,0.85);
      padding: 5px 14px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 11.5px;
      font-family: -apple-system, sans-serif;
      font-weight: 500;
      letter-spacing: 0.04em;
      transition: background 0.18s, border-color 0.18s;
      white-space: nowrap;
    }
    .theme-toggle:hover {
      background: rgba(255,255,255,0.20);
      border-color: rgba(255,255,255,0.40);
    }

    /* ===== Primary Navigation ===== */
    .nav-bar {
      border-top: 1px solid rgba(255,255,255,0.10);
      display: flex;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .nav-bar::-webkit-scrollbar { display: none; }
    .tab-btn {
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      color: rgba(255,255,255,0.52);
      padding: 10px 20px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      font-family: 'Yu Gothic', 'YuGothic', -apple-system, 'Hiragino Sans', sans-serif;
      letter-spacing: 0.03em;
      white-space: nowrap;
      transition: color 0.15s, border-color 0.15s;
      -webkit-tap-highlight-color: transparent;
    }
    .tab-btn.active {
      color: #ffffff;
      border-bottom-color: #ffffff;
      font-weight: 700;
    }
    .tab-btn:hover:not(.active) { color: rgba(255,255,255,0.80); }

    /* ===== Sub Navigation ===== */
    .sub-nav-bar {
      background: var(--subnav-bg);
      border-bottom: 1px solid var(--subnav-border);
      padding: 0 28px;
      display: flex;
      overflow-x: auto;
      scrollbar-width: none;
      transition: background 0.25s;
    }
    .sub-nav-bar::-webkit-scrollbar { display: none; }
    .sub-tab-btn {
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--text2);
      padding: 8px 16px;
      cursor: pointer;
      font-size: 12.5px;
      font-weight: 500;
      font-family: 'Yu Gothic', 'YuGothic', -apple-system, 'Hiragino Sans', sans-serif;
      white-space: nowrap;
      transition: color 0.15s, border-color 0.15s;
      -webkit-tap-highlight-color: transparent;
    }
    .sub-tab-btn.active {
      color: var(--accent);
      border-bottom-color: var(--accent);
      font-weight: 700;
    }
    .sub-tab-btn:hover:not(.active) { color: var(--accent); }

    .hidden { display: none !important; }

    /* ===== Main Layout ===== */
    main {
      padding: 28px 28px 56px;
      max-width: 1440px;
      margin: 0 auto;
    }
    @media (max-width: 640px) {
      main { padding: 16px 16px 48px; }
      header { padding: 12px 16px 0; }
      .sub-nav-bar { padding: 0 16px; }
    }

    /* ===== Section Headings ===== */
    .section-heading {
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--hot-accent);
      margin-bottom: 18px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
      font-family: -apple-system, sans-serif;
    }
    .section-heading.regular { color: var(--accent); }
    .section-divider {
      border: none;
      border-top: 1px solid var(--border);
      margin: 28px 0;
    }

    /* ===== Card Grid ===== */
    .card-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      align-items: stretch;  /* 同一行のカードを同じ高さに揃える */
    }
    @media (max-width: 1080px) {
      .card-grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 640px) {
      .card-grid { grid-template-columns: 1fr; gap: 12px; }
    }

    /* ===== Card Base ===== */
    .card {
      position: relative;
      background: var(--surface);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      border-top: 3px solid var(--accent);
      padding: 18px;
      transition: box-shadow 0.2s, transform 0.2s;
      display: flex;
      flex-direction: column;  /* フッターを底部に固定するための flex 化 */
    }
    @media (hover: hover) {
      .card:hover {
        box-shadow: 0 6px 20px rgba(0,0,0,0.10);
        transform: translateY(-2px);
      }
      [data-theme="dark"] .card:hover {
        box-shadow: 0 6px 20px rgba(0,0,0,0.45);
      }
    }

    /* Hot Card */
    .card-hot {
      border-top-color: var(--hot-accent);
      border-color: var(--hot-border);
      background: var(--hot-bg);
    }

    /* ===== Card Internals ===== */
    .card-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }
    .card-tag {
      display: inline-block;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.07em;
      text-transform: uppercase;
      background: var(--tag-bg);
      color: var(--tag-text);
      padding: 2px 8px;
      border-radius: 3px;
      font-family: -apple-system, sans-serif;
    }
    .card-hot .card-tag {
      background: var(--hot-border);
      color: #ffffff;
    }
    .impact-stars {
      font-size: 12px;
      letter-spacing: 0.01em;
      line-height: 1;
    }
    .star.filled { color: var(--star-filled); }
    .star.empty  { color: var(--star-empty); }

    .badge-new {
      position: absolute;
      top: 11px; right: 11px;
      background: var(--badge-bg);
      color: #fff;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.08em;
      padding: 2px 7px;
      border-radius: 3px;
      pointer-events: none;
      font-family: -apple-system, sans-serif;
    }
    .card-hot .badge-new {
      background: var(--hot-accent);
    }

    .card-title {
      font-size: 14.5px;
      font-weight: 600;
      line-height: 1.5;
      /* 14.5px × 1.5 × 3行 = 65.25px → サブピクセル丸め対策で 68px に切り上げ
         min-height により短いタイトルも同じ高さを確保し、
         横並びカード全体で ABSTRACT の開始位置をピクセル単位で揃える */
      min-height: 68px;
      margin-bottom: 10px;
      font-family: 'Yu Gothic', 'YuGothic', -apple-system, 'Hiragino Sans', 'Noto Sans JP', sans-serif;
    }
    .card-title a {
      display: -webkit-box;
      -webkit-line-clamp: 3;   /* 長いタイトルは3行でクリップ */
      -webkit-box-orient: vertical;
      overflow: hidden;
      color: var(--text);
      text-decoration: none;
    }
    .card-title a:hover { color: var(--accent); }

    /* Section labels — visually distinct for Summary vs Insight */
    .section-label-summary {
      display: inline-block;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--text2);
      background: transparent;
      margin: 12px 0 5px;
      font-family: -apple-system, sans-serif;
    }
    .section-label-insight {
      display: inline-block;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent2);
      background: transparent;
      margin: 12px 0 5px;
      font-family: -apple-system, sans-serif;
    }

    /* Summary: plain, secondary tone */
    .card-summary {
      font-size: 13.5px;
      color: var(--text2);
      line-height: 1.68;
    }

    /* Insight: highlighted block with left border */
    .card-insight {
      font-size: 13.5px;
      color: var(--text);
      background: var(--insight-bg);
      border-left: 3px solid var(--insight-border);
      border-radius: 0 4px 4px 0;
      padding: 10px 13px;
      line-height: 1.68;
      /* ボタンを右下に配置するための flex 化 */
      display: flex;
      flex-direction: column;
    }

    .card-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: auto;  /* カード内コンテンツに関わらず常に底部に配置 */
      padding-top: 10px;
      border-top: 1px solid var(--border-subtle);
    }
    .card-date {
      font-size: 11px;
      color: var(--muted);
      font-family: -apple-system, sans-serif;
    }
    .read-link {
      font-size: 12px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
      font-family: -apple-system, sans-serif;
      letter-spacing: 0.02em;
    }
    .read-link:hover { text-decoration: underline; }

    .empty {
      grid-column: 1 / -1;
      text-align: center;
      color: var(--muted);
      padding: 60px 0;
      font-size: 14px;
      font-family: -apple-system, sans-serif;
    }

    /* ===== Source label ===== */
    .card-source {
      display: inline-block;
      font-size: 9.5px;
      font-weight: 500;
      color: var(--muted);
      background: var(--border-subtle);
      padding: 1px 7px;
      border-radius: 3px;
      font-family: -apple-system, sans-serif;
      letter-spacing: 0.02em;
      white-space: nowrap;
    }

    /* ===== Accordion — expand / collapse ===== */
    /* max-height ベース：JS が実高さを測定してアニメーションを制御する */
    .exp-text {
      max-height: 4.8em;   /* ≈ 3行（font-size × line-height × 3 ÷ font-size） */
      overflow: hidden;
      transition: max-height 0.32s ease;
    }
    /* .exp-text.open は JS が inline style で max-height を上書きするため CSS 定義不要 */
    .exp-btn {
      /* .card（flex-column）と .card-insight（flex-column）の両方で
         align-self: flex-end により右下に自動配置される */
      align-self: flex-end;
      background: none;
      border: none;
      color: var(--accent);
      font-size: 11px;
      font-weight: 600;
      font-family: -apple-system, sans-serif;
      cursor: pointer;
      padding: 5px 0 0;   /* テキストとボタンの垂直余白 */
      letter-spacing: 0.02em;
      -webkit-tap-highlight-color: transparent;
      line-height: 1;
    }
    .exp-btn:hover { text-decoration: underline; }
    .card-insight .exp-btn { color: var(--accent2); }

    /* ===== Hashtags ===== */
    .card-hashtags {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin: 8px 0 10px;
    }
    .hashtag {
      font-size: 10.5px;
      color: var(--accent);
      background: var(--tag-bg);
      padding: 2px 7px;
      border-radius: 10px;
      font-family: -apple-system, sans-serif;
      letter-spacing: 0.02em;
    }
    [data-theme="dark"] .hashtag {
      color: var(--accent2);
      background: var(--tag-bg);
    }

    /* ===== Impact numeric ===== */
    .impact-val {
      font-size: 10px;
      color: var(--muted);
      font-family: -apple-system, sans-serif;
      margin-left: 2px;
    }

    /* ===== Archive link ===== */
    .archive-link {
      flex-shrink: 0;
      background: rgba(255,255,255,0.10);
      border: 1px solid rgba(255,255,255,0.22);
      color: rgba(255,255,255,0.85);
      padding: 5px 14px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 11.5px;
      font-family: -apple-system, sans-serif;
      font-weight: 500;
      letter-spacing: 0.04em;
      text-decoration: none;
      white-space: nowrap;
      transition: background 0.18s, border-color 0.18s;
    }
    .archive-link:hover {
      background: rgba(255,255,255,0.20);
      border-color: rgba(255,255,255,0.40);
    }
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <div class="header-brand">
        <h1>Human Science Insights</h1>
        <span class="header-updated">{{ updated }}</span>
      </div>
      <div style="display:flex;gap:8px;align-items:center;">
        <a class="archive-link" href="archive/index.html">アーカイブ</a>
        <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()">Dark Mode</button>
      </div>
    </div>
    <nav class="nav-bar" role="tablist">
      <button class="tab-btn active" data-parent="all"      onclick="showParent('all')">All</button>
      <button class="tab-btn"        data-parent="ai"       onclick="showParent('ai')">AI</button>
      <button class="tab-btn"        data-parent="neuro"    onclick="showParent('neuro')">脳科学</button>
      <button class="tab-btn"        data-parent="neuro_ai" onclick="showParent('neuro_ai')">脳科学 × AI</button>
      <button class="tab-btn"        data-parent="hr"       onclick="showParent('hr')">組織人事</button>
    </nav>
  </header>

  <!-- AI サブナビ -->
  <div class="sub-nav-bar hidden" id="sub-nav-ai">
    <button class="sub-tab-btn active" data-sub="social"   onclick="showSub('ai','social')">社会実装</button>
    <button class="sub-tab-btn"        data-sub="press"    onclick="showSub('ai','press')">プレスリリース</button>
    <button class="sub-tab-btn"        data-sub="academic" onclick="showSub('ai','academic')">学術・研究</button>
  </div>
  <!-- 脳科学 サブナビ -->
  <div class="sub-nav-bar hidden" id="sub-nav-neuro">
    <button class="sub-tab-btn active" data-sub="social"   onclick="showSub('neuro','social')">社会実装</button>
    <button class="sub-tab-btn"        data-sub="press"    onclick="showSub('neuro','press')">プレスリリース</button>
    <button class="sub-tab-btn"        data-sub="research" onclick="showSub('neuro','research')">研究・論文</button>
  </div>
  <!-- 脳科学 > 研究・論文 第3階層 -->
  <div class="sub-nav-bar hidden" id="sub3-nav-research">
    <button class="sub-tab-btn active" data-sub3="embodiment" onclick="showSub3('embodiment')">身体性</button>
    <button class="sub-tab-btn"        data-sub3="psychology" onclick="showSub3('psychology')">心理・認知</button>
  </div>
  <!-- 組織人事 サブナビ -->
  <div class="sub-nav-bar hidden" id="sub-nav-hr">
    <button class="sub-tab-btn active" data-sub="social"   onclick="showSub('hr','social')">社会実装</button>
    <button class="sub-tab-btn"        data-sub="press"    onclick="showSub('hr','press')">プレスリリース</button>
    <button class="sub-tab-btn"        data-sub="academic" onclick="showSub('hr','academic')">研究・論文</button>
  </div>

  <main>
    <!-- ===== All パネル（Hot Topics 表示あり） ===== -->
    <div id="panel-all">
      {% if hot_articles %}
      <section>
        <div class="section-heading">Hot Topics — パラダイムシフト候補</div>
        <div class="card-grid">
          {% for article in hot_articles %}{{ render_card(article, 'card-hot') }}{% endfor %}
        </div>
      </section>
      <hr class="section-divider">
      {% endif %}
      <div class="section-heading regular">All Articles</div>
      <div class="card-grid">
        {% if all_non_hot_articles %}
          {% for a in all_non_hot_articles %}{{ render_card(a) }}{% endfor %}
        {% else %}
          <div class="empty">本日の記事はありません</div>
        {% endif %}
      </div>
    </div>

    <!-- ===== AI パネル（Hot Topics 非表示） ===== -->
    <div id="panel-ai" class="hidden">
      <div class="sub-panel" id="sub-panel-ai-social">
        {{ render_panel(ai['social']['articles']) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-ai-press">
        {{ render_panel(ai['press']['articles']) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-ai-academic">
        {{ render_panel(ai['academic']['articles']) }}
      </div>
    </div>

    <!-- ===== 脳科学 パネル ===== -->
    <div id="panel-neuro" class="hidden">
      <div class="sub-panel" id="sub-panel-neuro-social">
        {{ render_panel(neuro['social']['articles']) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-neuro-press">
        {{ render_panel(neuro['press']['articles']) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-neuro-research">
        <div class="sub3-panel" id="sub3-panel-embodiment">
          {{ render_panel(neuro['research']['embodiment']['articles']) }}
        </div>
        <div class="sub3-panel hidden" id="sub3-panel-psychology">
          {{ render_panel(neuro['research']['psychology']['articles']) }}
        </div>
      </div>
    </div>

    <!-- ===== 脳科学×AI パネル ===== -->
    <div id="panel-neuro-ai" class="hidden">
      {{ render_panel(neuro_ai['articles']) }}
    </div>

    <!-- ===== 組織人事 パネル ===== -->
    <div id="panel-hr" class="hidden">
      <div class="sub-panel" id="sub-panel-hr-social">
        {{ render_panel(hr['social']['articles']) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-hr-press">
        {{ render_panel(hr['press']['articles']) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-hr-academic">
        {{ render_panel(hr['academic']['articles']) }}
      </div>
    </div>
  </main>

  <script>
    /* ── State ── */
    let currentParent   = 'all';
    let currentSubAi    = 'social';
    let currentSubNeuro = 'social';
    let currentSubHr    = 'social';
    let currentSub3     = 'embodiment';

    /* ── Parent tab switching ── */
    function showParent(tab) {
      currentParent = tab;

      document.querySelectorAll('.nav-bar .tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.parent === tab));

      // サブナビ表示制御
      document.getElementById('sub-nav-ai').classList.toggle('hidden', tab !== 'ai');
      document.getElementById('sub-nav-neuro').classList.toggle('hidden', tab !== 'neuro');
      document.getElementById('sub-nav-hr').classList.toggle('hidden', tab !== 'hr');
      const show3 = tab === 'neuro' && currentSubNeuro === 'research';
      document.getElementById('sub3-nav-research').classList.toggle('hidden', !show3);

      // パネル表示制御
      document.getElementById('panel-all').classList.toggle('hidden',      tab !== 'all');
      document.getElementById('panel-ai').classList.toggle('hidden',       tab !== 'ai');
      document.getElementById('panel-neuro').classList.toggle('hidden',    tab !== 'neuro');
      document.getElementById('panel-neuro-ai').classList.toggle('hidden', tab !== 'neuro_ai');
      document.getElementById('panel-hr').classList.toggle('hidden',       tab !== 'hr');
    }

    /* ── Sub tab switching ── */
    function showSub(parent, tab) {
      if (parent === 'ai') {
        currentSubAi = tab;
        document.querySelectorAll('#sub-nav-ai .sub-tab-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['social', 'press', 'academic'].forEach(k =>
          document.getElementById(`sub-panel-ai-${k}`).classList.toggle('hidden', k !== tab));
      } else if (parent === 'neuro') {
        currentSubNeuro = tab;
        document.querySelectorAll('#sub-nav-neuro .sub-tab-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['social', 'press', 'research'].forEach(k =>
          document.getElementById(`sub-panel-neuro-${k}`).classList.toggle('hidden', k !== tab));
        document.getElementById('sub3-nav-research').classList.toggle('hidden', tab !== 'research');
      } else if (parent === 'hr') {
        currentSubHr = tab;
        document.querySelectorAll('#sub-nav-hr .sub-tab-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['social', 'press', 'academic'].forEach(k =>
          document.getElementById(`sub-panel-hr-${k}`).classList.toggle('hidden', k !== tab));
      }
    }

    /* ── Sub3 tab switching ── */
    function showSub3(tab) {
      currentSub3 = tab;
      document.querySelectorAll('#sub3-nav-research .sub-tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.sub3 === tab));
      ['embodiment', 'psychology'].forEach(k =>
        document.getElementById(`sub3-panel-${k}`).classList.toggle('hidden', k !== tab));
    }

    /* ── Dark / Light mode ── */
    function toggleTheme() {
      const html    = document.documentElement;
      const isDark  = html.dataset.theme === 'dark';
      const next    = isDark ? 'light' : 'dark';
      html.dataset.theme = next;
      document.getElementById('theme-toggle').textContent = isDark ? 'Dark Mode' : 'Light Mode';
      localStorage.setItem('neuroai_theme_v1', next);
    }
    // 起動時にテーマを復元
    (function () {
      const saved = localStorage.getItem('neuroai_theme_v1');
      if (saved) {
        document.documentElement.dataset.theme = saved;
        const btn = document.getElementById('theme-toggle');
        if (btn) btn.textContent = saved === 'dark' ? 'Light Mode' : 'Dark Mode';
      }
    })();

    /* ── Accordion: expand / collapse ── */
    function toggleExp(btn) {
      var el = btn.previousElementSibling;
      if (!el || !el.classList.contains('exp-text')) return;

      if (el.classList.contains('open')) {
        /* 折りたたむ: 現在高さを inline で固定してから 4.8em へアニメーション */
        el.style.maxHeight = el.scrollHeight + 'px';
        el.classList.remove('open');
        requestAnimationFrame(function () {
          el.style.maxHeight = '4.8em';
        });
        btn.textContent = '続きを読む...';
      } else {
        /* 展開する: 実際のコンテンツ高さを測定してアニメーション目標にする */
        var targetH = el.scrollHeight + 'px';
        el.classList.add('open');
        el.style.maxHeight = targetH;
        btn.textContent = '閉じる';
        /* アニメーション完了後 auto を設定し、コンテンツ変化にも追従させる */
        el.addEventListener('transitionend', function handler() {
          if (el.classList.contains('open')) el.style.maxHeight = 'none';
          el.removeEventListener('transitionend', handler);
        });
      }
    }

    /* テキストが 3 行未満のカードはボタンを非表示にする */
    window.addEventListener('load', function () {
      document.querySelectorAll('.exp-btn').forEach(function (btn) {
        var content = btn.previousElementSibling;
        if (content && content.classList.contains('exp-text')) {
          if (content.scrollHeight <= content.clientHeight + 4) {
            btn.style.display = 'none';
          }
        }
      });
    });

    /* ── Scroll-hide header ── */
    (function () {
      const hdr       = document.querySelector('header');
      const THRESHOLD = 6;      // px — この量以上の変化で判定
      let lastY       = window.scrollY;
      let ticking     = false;

      function onScroll() {
        if (!ticking) {
          requestAnimationFrame(() => {
            const y    = window.scrollY;
            const diff = y - lastY;
            if (diff > THRESHOLD && y > 60) {
              hdr.classList.add('header--hidden');
            } else if (diff < -THRESHOLD || y <= 0) {
              hdr.classList.remove('header--hidden');
            }
            lastY   = y;
            ticking = false;
          });
          ticking = true;
        }
      }
      window.addEventListener('scroll', onScroll, { passive: true });
    })();

    /* ── 未読バッジ管理（localStorage） ── */
    const READ_KEY = 'neuroai_read_v1';
    function getReadSet() {
      try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) || '[]')); }
      catch { return new Set(); }
    }
    function markRead(url) {
      const s = getReadSet(); s.add(url);
      localStorage.setItem(READ_KEY, JSON.stringify([...s]));
    }
    function hideBadge(card) {
      const b = card.querySelector('.badge-new');
      if (b) b.style.display = 'none';
    }
    const readSet = getReadSet();
    document.querySelectorAll('.card').forEach(card => {
      if (readSet.has(card.dataset.url)) hideBadge(card);
    });
    document.querySelectorAll('.card a').forEach(link => {
      link.addEventListener('click', () => {
        const card = link.closest('.card');
        if (card) { markRead(card.dataset.url); hideBadge(card); }
      });
    });
  </script>
</body>
</html>"""


def _format_published(iso_str: str) -> str:
    """ISO 8601 文字列を JST の「YYYY-MM-DD HH:MM」形式に変換する"""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_str


ARCHIVE_DAY_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Human Science Insights — {{ date }}</title>
  <style>
    :root {
      --bg:#fcfce8;--surface:#ffffff;--border:#e8e0d5;--border-subtle:#eeeed8;
      --accent:#cc1340;--accent2:#a80f34;--hot-accent:#cc1340;
      --text:#2a2a2a;--text2:#5a4448;--muted:#9e8e92;
      --star-filled:#c8960c;--star-empty:#ddd5cc;
      --tag-bg:#fce8ed;--tag-text:#cc1340;
      --insight-bg:#fff0f3;--insight-border:#cc1340;
      --hot-bg:#fff5f7;--hot-border:#cc1340;
      --badge-bg:#cc1340;--radius:8px;
    }
    [data-theme="dark"] {
      --bg:#00212b;--surface:#003847;--border:#005566;--border-subtle:#003040;
      --accent:#5bc8e0;--accent2:#7dddf0;--hot-accent:#ff7070;
      --text:#e0e0e0;--text2:#a8c8d4;--muted:#5a8090;
      --star-filled:#f0c040;--star-empty:#003345;
      --tag-bg:#004555;--tag-text:#7dddf0;
      --insight-bg:#003040;--insight-border:#5bc8e0;
      --hot-bg:#002030;--hot-border:#ff7070;
      --badge-bg:#5bc8e0;
    }
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:'Yu Gothic','YuGothic','Hiragino Sans','Noto Sans JP',sans-serif;font-size:15px;line-height:1.72;padding:24px 28px 56px}
    .page-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:10px}
    h1{font-size:20px;font-weight:700;color:var(--accent)}
    .back-link{font-size:12px;color:var(--accent);text-decoration:none}
    .back-link:hover{text-decoration:underline}
    .date-label{font-size:13px;color:var(--muted)}
    .theme-toggle{background:var(--tag-bg);border:1px solid var(--border);color:var(--accent);padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11.5px;font-weight:500}
    .card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
    @media(max-width:1080px){.card-grid{grid-template-columns:repeat(2,1fr)}}
    @media(max-width:640px){.card-grid{grid-template-columns:1fr;gap:12px}}
    .card{position:relative;background:var(--surface);border-radius:var(--radius);border:1px solid var(--border);border-top:3px solid var(--accent);padding:18px;display:flex;flex-direction:column}
    .card-hot{border-top-color:var(--hot-accent);border-color:var(--hot-border);background:var(--hot-bg)}
    .card-meta{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
    .card-tag{font-size:9.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;background:var(--tag-bg);color:var(--tag-text);padding:2px 8px;border-radius:3px}
    .card-hot .card-tag{background:var(--hot-border);color:#fff}
    .card-source{font-size:9.5px;color:var(--muted);background:var(--border-subtle);padding:1px 7px;border-radius:3px}
    .impact-stars{font-size:12px}.star.filled{color:var(--star-filled)}.star.empty{color:var(--star-empty)}
    .impact-val{font-size:10px;color:var(--muted);margin-left:2px}
    .card-hashtags{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0 10px}
    .hashtag{font-size:10.5px;color:var(--accent);background:var(--tag-bg);padding:2px 7px;border-radius:10px}
    .card-title{font-size:14.5px;font-weight:600;line-height:1.5;min-height:68px;margin-bottom:10px;font-family:'Yu Gothic','YuGothic',-apple-system,sans-serif}
    .card-title a{color:var(--text);text-decoration:none}.card-title a:hover{color:var(--accent)}
    .section-label-summary{font-size:9.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text2);margin:12px 0 5px}
    .section-label-insight{font-size:9.5px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--accent2);margin:12px 0 5px}
    .card-summary{font-size:13.5px;color:var(--text2);line-height:1.68}
    .card-insight{font-size:13.5px;color:var(--text);background:var(--insight-bg);border-left:3px solid var(--insight-border);border-radius:0 4px 4px 0;padding:10px 13px;line-height:1.68}
    .card-footer{display:flex;justify-content:space-between;align-items:center;margin-top:auto;padding-top:10px;border-top:1px solid var(--border-subtle)}
    .card-date{font-size:11px;color:var(--muted)}.read-link{font-size:12px;color:var(--accent);text-decoration:none;font-weight:600}
    .read-link:hover{text-decoration:underline}
    .section-heading{font-size:10.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--hot-accent);margin-bottom:18px;padding-bottom:8px;border-bottom:1px solid var(--border)}
    .section-heading.regular{color:var(--accent)}
    .section-divider{border:none;border-top:1px solid var(--border);margin:28px 0}
    .empty{text-align:center;color:var(--muted);padding:60px 0;font-size:14px}
  </style>
</head>
<body>
  <div class="page-header">
    <div>
      <h1>Human Science Insights</h1>
      <span class="date-label">{{ date }} のアーカイブ</span>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <a class="back-link" href="index.html">← 一覧へ戻る</a>
      <button class="theme-toggle" onclick="(function(){var h=document.documentElement,d=h.dataset.theme==='dark';h.dataset.theme=d?'light':'dark';this.textContent=d?'Dark Mode':'Light Mode'}).call(this)">Dark Mode</button>
    </div>
  </div>
  {% if hot_articles %}
  <div class="section-heading">Hot Topics</div>
  <div class="card-grid" style="margin-bottom:28px">
    {% for a in hot_articles %}
    <article class="card card-hot">
      <div class="card-meta">
        <span class="card-tag">{{ a.category_label }}</span>
        {% if a.source %}<span class="card-source">{{ a.source }}</span>{% endif %}
        <span class="impact-stars">{% for i in range(1,6) %}<span class="star {{ 'filled' if i <= a.impact|float else 'empty' }}">★</span>{% endfor %}<span class="impact-val">{{ "%.1f"|format(a.impact|float) }}</span></span>
      </div>
      <div class="card-title"><a href="{{ a.url }}" target="_blank" rel="noopener">{{ a.title_ja }}</a></div>
      {% if a.hashtags %}<div class="card-hashtags">{% for tag in a.hashtags %}<span class="hashtag">{{ tag }}</span>{% endfor %}</div>{% endif %}
      <div class="section-label-summary">Abstract</div>
      <p class="card-summary">{{ a.summary }}</p>
      <div class="section-label-insight">Insight</div>
      <div class="card-insight">{{ a.insight }}</div>
      <div class="card-footer"><span class="card-date">{{ a.published_jst }}</span><a class="read-link" href="{{ a.url }}" target="_blank" rel="noopener">原文 →</a></div>
    </article>
    {% endfor %}
  </div>
  <hr class="section-divider">
  {% endif %}
  <div class="section-heading regular">All Articles</div>
  <div class="card-grid">
    {% for a in all_articles %}
    <article class="card">
      <div class="card-meta">
        <span class="card-tag">{{ a.category_label }}</span>
        {% if a.source %}<span class="card-source">{{ a.source }}</span>{% endif %}
        <span class="impact-stars">{% for i in range(1,6) %}<span class="star {{ 'filled' if i <= a.impact|float else 'empty' }}">★</span>{% endfor %}<span class="impact-val">{{ "%.1f"|format(a.impact|float) }}</span></span>
      </div>
      <div class="card-title"><a href="{{ a.url }}" target="_blank" rel="noopener">{{ a.title_ja }}</a></div>
      {% if a.hashtags %}<div class="card-hashtags">{% for tag in a.hashtags %}<span class="hashtag">{{ tag }}</span>{% endfor %}</div>{% endif %}
      <div class="section-label-summary">Abstract</div>
      <p class="card-summary">{{ a.summary }}</p>
      <div class="section-label-insight">Insight</div>
      <div class="card-insight">{{ a.insight }}</div>
      <div class="card-footer"><span class="card-date">{{ a.published_jst }}</span><a class="read-link" href="{{ a.url }}" target="_blank" rel="noopener">原文 →</a></div>
    </article>
    {% else %}
    <div class="empty">記事がありません</div>
    {% endfor %}
  </div>
</body>
</html>"""


ARCHIVE_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Human Science Insights — アーカイブ</title>
  <style>
    :root{--bg:#fcfce8;--surface:#ffffff;--border:#e8e0d5;--accent:#cc1340;--text:#2a2a2a;--text2:#5a4448;--muted:#9e8e92;--tag-bg:#fce8ed;--radius:8px}
    [data-theme="dark"]{--bg:#00212b;--surface:#003847;--border:#005566;--accent:#5bc8e0;--text:#e0e0e0;--text2:#a8c8d4;--muted:#5a8090;--tag-bg:#004555}
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:'Yu Gothic','YuGothic','Hiragino Sans',sans-serif;font-size:15px;line-height:1.72;padding:32px 28px 56px}
    .page-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px;flex-wrap:wrap;gap:10px}
    h1{font-size:20px;font-weight:700;color:var(--accent)}
    .back-link{font-size:12px;color:var(--accent);text-decoration:none}.back-link:hover{text-decoration:underline}
    .theme-toggle{background:var(--tag-bg);border:1px solid var(--border);color:var(--accent);padding:5px 14px;border-radius:4px;cursor:pointer;font-size:11.5px;font-weight:500}
    .archive-list{list-style:none;display:flex;flex-direction:column;gap:10px;max-width:480px}
    .archive-list li a{display:block;background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:var(--radius);padding:12px 16px;color:var(--text);text-decoration:none;font-size:14px;font-weight:600;transition:box-shadow .18s}
    .archive-list li a:hover{box-shadow:0 4px 12px rgba(0,0,0,.1);color:var(--accent)}
    .empty{color:var(--muted);font-size:14px;padding:40px 0}
  </style>
</head>
<body>
  <div class="page-header">
    <h1>アーカイブ一覧</h1>
    <div style="display:flex;gap:8px;align-items:center">
      <a class="back-link" href="../index.html">← トップへ戻る</a>
      <button class="theme-toggle" onclick="(function(){var h=document.documentElement,d=h.dataset.theme==='dark';h.dataset.theme=d?'light':'dark';this.textContent=d?'Dark Mode':'Light Mode'}).call(this)">Dark Mode</button>
    </div>
  </div>
  {% if entries %}
  <ul class="archive-list">
    {% for entry in entries %}
    <li><a href="{{ entry.filename }}">{{ entry.date }}</a></li>
    {% endfor %}
  </ul>
  {% else %}
  <div class="empty">アーカイブはまだありません</div>
  {% endif %}
</body>
</html>"""


def generate_html(
    processed: dict[str, list[ProcessedArticle]],
    output_path: str = "docs/index.html",
    cache_dir: str | None = None,
) -> None:
    """精査済み記事データからindex.htmlを生成し output_path に書き出す。"""
    now_jst = datetime.now(JST)
    updated_str = now_jst.strftime("%Y-%m-%d %H:%M JST")

    panels: dict = {
        "ai": {
            "social":   {"articles": []},
            "press":    {"articles": []},
            "academic": {"articles": []},
        },
        "neuro": {
            "social":   {"articles": []},
            "press":    {"articles": []},
            "research": {
                "embodiment": {"articles": []},
                "psychology": {"articles": []},
            },
        },
        "neuro_ai": {"articles": []},
        "hr": {
            "social":   {"articles": []},
            "press":    {"articles": []},
            "academic": {"articles": []},
        },
    }

    all_articles = _enrich_articles(processed)

    # Hot Topics: スコアベースで永続化（cache_dir あり）または当日のみフィルタ
    if cache_dir:
        hot_articles = update_hot_topics(cache_dir, all_articles)
        today_urls = {a["url"] for a in all_articles}
        hot_url_set = {a["url"] for a in hot_articles if a["url"] in today_urls}
    else:
        hot_candidates = [
            a for a in all_articles
            if a.get("impact", 0.0) >= HOT_SCORE_THRESHOLD and a.get("category") in _HOT_CATS
        ]
        hot_candidates.sort(key=lambda a: (a.get("impact", 0.0), a.get("published", "")), reverse=True)
        hot_articles = hot_candidates[:HOT_TOPICS_MAX]
        hot_url_set = {a["url"] for a in hot_articles}

    # パネルへ振り分け（today の hot は除外）
    today_urls_set = {a["url"] for a in all_articles}
    for a in all_articles:
        if a["url"] in hot_url_set and a["url"] in today_urls_set:
            continue
        mapping = _CAT_MAP.get(a.get("category", ""))
        if not mapping:
            continue
        parent, sub, sub3 = mapping

        if parent == "ai" and sub:
            panels["ai"][sub]["articles"].append(a)
        elif parent == "neuro" and sub == "research" and sub3:
            panels["neuro"]["research"][sub3]["articles"].append(a)
        elif parent == "neuro" and sub:
            panels["neuro"][sub]["articles"].append(a)
        elif parent == "neuro_ai":
            panels["neuro_ai"]["articles"].append(a)
        elif parent == "hr" and sub:
            panels["hr"][sub]["articles"].append(a)

    # パネルごとにインパクト降順・上限10件
    def _sort_limit(lst: list, n: int = 10) -> list:
        lst.sort(key=lambda a: (a.get("impact", 0.0), a.get("published", "")), reverse=True)
        return lst[:n]

    for sub in ("social", "press", "academic"):
        panels["ai"][sub]["articles"]   = _sort_limit(panels["ai"][sub]["articles"])
        panels["hr"][sub]["articles"]   = _sort_limit(panels["hr"][sub]["articles"])
    for sub in ("social", "press"):
        panels["neuro"][sub]["articles"] = _sort_limit(panels["neuro"][sub]["articles"])
    panels["neuro"]["research"]["embodiment"]["articles"] = \
        _sort_limit(panels["neuro"]["research"]["embodiment"]["articles"])
    panels["neuro"]["research"]["psychology"]["articles"] = \
        _sort_limit(panels["neuro"]["research"]["psychology"]["articles"])
    panels["neuro_ai"]["articles"] = _sort_limit(panels["neuro_ai"]["articles"])

    # All タブ用：hot 以外の全記事をインパクト降順で最大 30 件
    all_non_hot = [a for a in all_articles if a["url"] not in hot_url_set]
    all_non_hot.sort(key=lambda a: (a.get("impact", 0.0), a.get("published", "")), reverse=True)
    all_non_hot_articles = all_non_hot[:30]

    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(HTML_TEMPLATE)
    html = tmpl.render(
        updated=updated_str,
        hot_articles=hot_articles,
        all_non_hot_articles=all_non_hot_articles,
        ai=panels["ai"],
        neuro=panels["neuro"],
        neuro_ai=panels["neuro_ai"],
        hr=panels["hr"],
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[generate] Written to {output_path}")


def generate_archive_page(
    processed: dict[str, list[ProcessedArticle]],
    archive_dir: str = "docs/archive",
) -> None:
    """本日分のアーカイブ HTML を docs/archive/YYYY-MM-DD.html に保存する。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    os.makedirs(archive_dir, exist_ok=True)
    out_path = os.path.join(archive_dir, f"{today}.html")

    all_articles = _enrich_articles(processed)
    all_articles.sort(key=lambda a: (a.get("impact", 0.0), a.get("published", "")), reverse=True)

    hot_articles = [
        a for a in all_articles
        if a.get("impact", 0.0) >= HOT_SCORE_THRESHOLD and a.get("category") in _HOT_CATS
    ][:HOT_TOPICS_MAX]
    hot_urls = {a["url"] for a in hot_articles}
    non_hot = [a for a in all_articles if a["url"] not in hot_urls]

    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(ARCHIVE_DAY_TEMPLATE)
    html = tmpl.render(date=today, hot_articles=hot_articles, all_articles=non_hot)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[generate] Archive saved: {out_path}")


def generate_archive_index(archive_dir: str = "docs/archive") -> None:
    """docs/archive/ 内の HTML ファイルを走査してインデックスページを生成する。"""
    os.makedirs(archive_dir, exist_ok=True)
    pattern = os.path.join(archive_dir, "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html")
    files = sorted(_glob.glob(pattern), reverse=True)

    entries = [
        {"date": os.path.basename(f).replace(".html", ""), "filename": os.path.basename(f)}
        for f in files
    ]

    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(ARCHIVE_INDEX_TEMPLATE)
    html = tmpl.render(entries=entries)

    index_path = os.path.join(archive_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[generate] Archive index: {index_path}")
