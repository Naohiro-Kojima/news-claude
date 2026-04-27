"""精査済み記事データからindex.htmlを生成する（タイムライン形式）"""

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

TIMELINE_MAX_ALL     = 60   # All タブに表示する最大記事数
TIMELINE_MAX_PER_TAB = 30   # 各サブタブの最大記事数
TIMELINE_MAX_DAYS    = 14   # 遡る最大日数

# カテゴリキー → (parent_tab, sub_tab, sub3_tab)
_CAT_MAP: dict[str, tuple[str, str | None, str | None]] = {
    # 新カテゴリ
    "ai_product":        ("ai",         "product",    None),
    "ai_business":       ("ai",         "business",   None),
    "neuro_social":      ("neuro",      "social",     None),
    "neuro_press":       ("neuro",      "press",      None),
    "neuro_embodiment":  ("neuro",      "research",   "embodiment"),
    "neuro_psychology":  ("neuro",      "research",   "psychology"),
    "neuro_ai":          ("neuro",      "research",   "psychology"),  # 旧 neuro_ai は psychology へ
    "competitor_press":  ("competitor", "press",      None),
    "industry_trend":    ("competitor", "trend",      None),
    # 旧カテゴリ互換（既存キャッシュが壊れないように）
    "ai_social":         ("ai",         "business",   None),
    "ai_press":          ("ai",         "product",    None),
    "ai_academic":       ("ai",         "business",   None),
    "hr_social":         ("competitor", "trend",      None),
    "hr_press":          ("competitor", "press",      None),
    "hr_academic":       ("competitor", "trend",      None),
}

MARKET_TRENDS: list[dict] = [
    {
        "category": "コンサル × AI",
        "headline": "生成AIによるコンサル業務の大規模再編が進行中",
        "body": (
            "BCG・マッキンゼーなど大手コンサルは生成AI活用でプロジェクト工数の20〜40%削減を報告。"
            "分析・資料作成の自動化が加速し、コンサルタントの付加価値は「戦略立案・意思決定支援・関係構築」へ急速にシフト。"
            "ジュニアコンサルの採用絞り込みと、シニア層へのAIツール習熟投資が業界共通の動きとなっている。"
        ),
        "source": "McKinsey Global Institute: The Economic Potential of Generative AI",
        "url": "https://www.mckinsey.com/capabilities/tech-and-ai/our-insights/the-economic-potential-of-generative-ai-the-next-productivity-frontier",
    },
    {
        "category": "採用市場",
        "headline": "新卒採用の「超早期化」が止まらない",
        "body": (
            "政府指針（大学3年3月解禁）に反し、実態は大学3年の夏インターン（6〜8月）が実質選考化。"
            "2026年卒では採用活動の実質開始が3年6月以前となる企業が4割超を占める（リクルートワークス研究所調べ）。"
            "内定時期の前倒しは学業・留学機会の喪失を招き、社会問題として国会審議にも上がっている。"
        ),
        "source": "リクルートワークス研究所 採用見通し調査（2026年卒）",
        "url": "https://www.works-i.com/surveys/report/241220_recruitment_outlook.html",
    },
    {
        "category": "人事制度",
        "headline": "ジョブ型人事制度の導入が大企業から中堅企業へ波及",
        "body": (
            "パーソル総合研究所の調査では、ジョブ型人事制度を「導入済み」の企業は18%、「導入検討中」は39.6%。"
            "日立・富士通・資生堂など先行大手の事例が呼び水となり、「職能資格制度の限界」を感じる中堅企業にも波及中。"
            "一方で「ジョブ定義の難しさ」「評価基準の整備コスト」が導入障壁として挙げられている。"
        ),
        "source": "パーソル総合研究所 ジョブ型人事制度に関する企業実態調査",
        "url": "https://rc.persol-group.co.jp/thinktank/data/employment.html",
    },
    {
        "category": "エンゲージメント",
        "headline": "従業員エンゲージメントと企業業績の相関が経営指標に",
        "body": (
            "Gallupの調査では、エンゲージメント上位4分位の企業は下位4分位と比較しEPS成長率で147%上回る。"
            "日本のエンゲージメントスコアは先進国最低水準（世界平均23%に対し日本は約6%）であり、"
            "「エンゲージメントサーベイの経営KPI化」がHR投資判断の中心的テーマとなっている。"
        ),
        "source": "Gallup State of the Global Workplace 2024",
        "url": "https://www.gallup.com/workplace/349484/state-of-the-global-workplace.aspx",
    },
    {
        "category": "労働市場",
        "headline": "2030年に最大644万人の労働力不足が到来",
        "body": (
            "パーソル総合研究所・中央大学の試算では、2030年に国内で最大644万人の人手不足が発生する見込み。"
            "IT（約79万人）・医療介護・物流が特に深刻。少子高齢化と団塊ジュニア世代の退職が重なる構造問題であり、"
            "「外国人材・シニア活用・AI代替」の三本柱が政策・企業戦略の共通テーマとなっている。"
        ),
        "source": "パーソル総合研究所 労働市場の未来推計2030",
        "url": "https://rc.persol-group.co.jp/thinktank/spe/roudou2030/",
    },
    {
        "category": "マネジメント",
        "headline": "プレイングマネージャー化で管理職機能が空洞化",
        "body": (
            "リクルートワークス研究所の調査では管理職の87.3%がプレイヤー業務を兼務する「プレイングマネジャー」状態。"
            "リクルートMSの意識調査（2024年）では管理職の多くが「メンバーのやる気を高めること」を最も難しい業務と回答。"
            "プレイヤー業務の増加がメンバー育成・1on1・心理的安全性の確保を圧迫し、組織の自律性低下につながっている。"
        ),
        "source": "リクルートマネジメントソリューションズ マネジメントに対する意識調査2024年",
        "url": "https://www.recruit-ms.co.jp/news/pressrelease/7265428582/",
    },
    {
        "category": "人材開発",
        "headline": "リスキリングが国家政策化・企業の義務へ",
        "body": (
            "政府は2022年に「5年間で1兆円のリスキリング支援」を表明。DX・AI領域を中心に、"
            "企業主導の集合研修から「個人が主体的に学ぶリスキリング」へのパラダイムシフトが加速。"
            "経済産業省は学び直し支援の制度整備を進め、企業側も補助金活用と学習機会提供が競争力の差別化要因となってきている。"
        ),
        "source": "経済産業省 リスキリングを通じたキャリアアップ支援事業",
        "url": "https://careerup.reskilling.go.jp/",
    },
    {
        "category": "HRテック",
        "headline": "国内HRテック市場が急拡大、エンゲージメント・タレント管理が牽引",
        "body": (
            "デロイト トーマツ ミック経済研究所の調査では、国内HRTechクラウド市場は2024年度に前年比128.5%の1,385億円見込み。"
            "エンゲージメントサーベイ・タレントマネジメント・採用DXが三大注目領域。"
            "一方でツール乱立による「導入疲れ」も顕在化しており、統合型プラットフォームへの移行が次のトレンドとして浮上している。"
        ),
        "source": "デロイト トーマツ ミック経済研究所 HRTechクラウド市場の実態と展望2024年度版",
        "url": "https://mic-r.co.jp/mr/03350/",
    },
]

COMPETITOR_DIRECTORY: list[tuple[str, list[tuple[str, str]]]] = [
    ("組織人事コンサル", [
        ("マーサージャパン",                   "https://www.mercer.com/ja-jp/"),
        ("コーン・フェリー",                   "https://www.kornferry.com/"),
        ("ウイリス・タワーズワトソン",           "https://www.wtwco.com/ja-JP"),
        ("DDI",                               "https://www.ddi.com/"),
        ("リクルートマネジメントソリューションズ", "https://www.recruit-ms.co.jp/"),
    ]),
    ("総合・戦略コンサル", [
        ("マッキンゼー・アンド・カンパニー",   "https://www.mckinsey.com/jp"),
        ("BCG",                              "https://www.bcg.com/ja-jp"),
        ("ベイン・アンド・カンパニー",         "https://www.bain.com/ja/"),
        ("デロイト トーマツ コンサルティング", "https://www2.deloitte.com/jp/"),
        ("アクセンチュア",                    "https://www.accenture.com/jp-ja"),
        ("PwCコンサルティング",               "https://www.pwc.com/jp/ja/"),
        ("KPMGコンサルティング",              "https://kpmg.com/jp/ja/"),
        ("EYストラテジー",                    "https://www.ey.com/ja_jp/"),
        ("アーサー・D・リトル",               "https://www.adlittle.com/ja"),
        ("ドリームインキュベータ",             "https://www.dreamincubator.co.jp/"),
        ("経営共創基盤（IGPI）",              "https://www.igpi.co.jp/"),
        ("野村総合研究所（NRI）",             "https://www.nri.com/jp"),
        ("アビームコンサルティング",           "https://www.abeam.com/jp/ja"),
        ("NTTデータ経営研究所",              "https://www.nttdata-strategy.com/"),
    ]),
    ("HRテック", [
        ("SmartHR",    "https://smarthr.jp/"),
        ("カオナビ",   "https://kaonavi.jp/"),
        ("HRBrain",    "https://www.hrbrain.jp/"),
        ("ワークデイ", "https://www.workday.com/ja-jp/"),
        ("Works Human Intelligence（WHI）", "https://www.works-hi.co.jp/corporate"),
    ]),
    ("人材紹介・採用", [
        ("リクルートホールディングス", "https://www.recruit.co.jp/"),
        ("パーソルホールディングス",   "https://www.persol-group.co.jp/"),
        ("エン・ジャパン",            "https://corp.en-japan.com/"),
        ("マイナビ",                  "https://www.mynavi.jp/"),
        ("JAC Recruitment",          "https://www.jac-recruitment.jp/"),
        ("クイック",                  "https://919.jp/"),
        ("パソナグループ",            "https://www.pasonagroup.co.jp/"),
    ]),
    ("研修・教育", [
        ("グロービス",     "https://www.globis.co.jp/"),
        ("日本能率協会",   "https://www.jma.or.jp/"),
    ]),
    ("メガベンチャー", [
        ("サイバーエージェント", "https://www.cyberagent.co.jp/"),
        ("メルカリ",            "https://about.mercari.com/"),
        ("freee",               "https://corp.freee.co.jp/"),
        ("Sansan",              "https://jp.sansan.com/"),
        ("マネーフォワード",    "https://corp.moneyforward.com/"),
        ("DeNA",                "https://dena.com/jp/"),
    ]),
    ("シンクタンク・研究機関", [
        ("パーソル総合研究所",       "https://rc.persol-group.co.jp/"),
        ("リクルートワークス研究所", "https://www.works-i.com/"),
    ]),
]

_HOT_CATS: frozenset[str] = frozenset({
    "ai_product", "ai_business", "ai_press", "ai_social",
    "competitor_press", "hr_press",
})

_DOMAIN_TO_SOURCE: dict[str, str] = {
    "techcrunch.com":         "TechCrunch",
    "technologyreview.com":   "MIT Tech Review",
    "theverge.com":           "The Verge",
    "wired.com":              "WIRED",
    "venturebeat.com":        "VentureBeat",
    "arstechnica.com":        "Ars Technica",
    "zdnet.com":              "ZDNet",
    "bloomberg.com":          "Bloomberg",
    "wsj.com":                "WSJ",
    "nytimes.com":            "NY Times",
    "ft.com":                 "Financial Times",
    "nature.com":             "Nature",
    "science.org":            "Science",
    "arxiv.org":              "arXiv",
    "openai.com":             "OpenAI",
    "anthropic.com":          "Anthropic",
    "deepmind.google":        "DeepMind",
    "blog.google":            "Google",
    "microsoft.com":          "Microsoft",
    "huggingface.co":         "Hugging Face",
    "neurosciencenews.com":   "Neuroscience News",
    "psychologytoday.com":    "Psychology Today",
    "sciencedaily.com":       "ScienceDaily",
    "frontiersin.org":        "Frontiers",
    "plos.org":               "PLOS",
    "eurekalert.org":         "EurekAlert",
    "nih.gov":                "NIH",
    "hbr.org":                "HBR",
    "mckinsey.com":           "McKinsey",
    "bcg.com":                "BCG",
    "workday.com":            "Workday",
    "shrm.org":               "SHRM",
    "hrexecutive.com":        "HR Executive",
    "kornferry.com":          "Korn Ferry",
    "mercer.com":             "Mercer",
    "zenn.dev":               "Zenn",
    "qiita.com":              "Qiita",
}


def _extract_source(url: str) -> str:
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
    "ai_product":        "AI · プロダクト",
    "ai_business":       "AI · プロダクトTips",
    "neuro_social":      "脳科学 · 社会",
    "neuro_press":       "脳科学 · プレス",
    "neuro_embodiment":  "身体性",
    "neuro_psychology":  "心理 · 認知",
    "neuro_ai":          "脳科学 × AI",
    "competitor_press":  "他社 · プレス",
    "industry_trend":    "業界トレンド",
    # 旧互換
    "ai_social":         "AI · プロダクトTips",
    "ai_press":          "AI · プロダクト",
    "ai_academic":       "AI · 研究",
    "hr_social":         "業界トレンド",
    "hr_press":          "他社 · プレス",
    "hr_academic":       "業界トレンド",
}

_COMPETITOR_GROUP_LABELS: dict[str, str] = {
    "org_hr_consulting":  "組織人事コンサル",
    "strategy_consulting": "総合・戦略コンサル",
    "hr_tech":            "HRテック",
    "talent_recruitment": "人材紹介",
    "training_education": "研修・教育",
}

WEEKDAYS_EN = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _impact_score(article: dict) -> float:
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
    return float((h % 3) + 2)


def _format_published(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_str


# ── Hot Topics 永続化 ─────────────────────────────────────────────────────────

HOT_SCORE_THRESHOLD: float = 4.5
HOT_TOPICS_MAX: int = 6
HOT_TOPICS_MAX_AGE_DAYS: int = 14


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
    existing = load_hot_topics(cache_dir)
    existing_urls = {a["url"] for a in existing}
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    cutoff = datetime.now(JST).date() - timedelta(days=HOT_TOPICS_MAX_AGE_DAYS)

    for a in today_articles:
        if a["url"] not in existing_urls and a.get("impact", 0.0) >= HOT_SCORE_THRESHOLD:
            existing.append({**a, "added_date": today_str})
            existing_urls.add(a["url"])

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


# ── タイムライン用ヘルパー ────────────────────────────────────────────────────

def _load_all_days(cache_dir: str, max_days: int = TIMELINE_MAX_DAYS) -> list[dict]:
    """複数日分の日次キャッシュを読み込み、URL重複排除済みのフラットリストを返す"""
    pattern = os.path.join(cache_dir, "processed_[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")
    files = sorted(_glob.glob(pattern), reverse=True)[:max_days]

    seen: set[str] = set()
    articles: list[dict] = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for cat_arts in data.values():
            for a in cat_arts:
                url = a.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    articles.append(a)
    return articles


def _enrich_articles(articles: list) -> list[dict]:
    """記事リストにUI表示用フィールド（published_jst / impact / category_label / source）を付与する"""
    return [
        {
            **a,
            "published_jst":    _format_published(a.get("published", "")),
            "impact":           _impact_score(a),
            "category_label":   _CATEGORY_LABELS.get(a.get("category", ""), a.get("category", "")),
            "source":           a.get("source") or _extract_source(a.get("url", "")),
            "competitor_group": a.get("competitor_group", ""),
            "impact_axes":      a.get("impact_axes") or {"per": 0.0, "sci": 0.0, "cps": 0.0},
        }
        for a in articles
    ]


def _group_by_date(articles: list[dict]) -> list[dict]:
    """published 降順のリストを日付ごとにグループ化し [{date_label, articles}] を返す"""
    from collections import OrderedDict
    groups: OrderedDict[str, list] = OrderedDict()
    for a in articles:
        date_key = a.get("published", "")[:10]
        if date_key not in groups:
            groups[date_key] = []
        groups[date_key].append(a)

    result = []
    for date_key, arts in groups.items():
        try:
            dt = datetime.strptime(date_key, "%Y-%m-%d")
            wd = WEEKDAYS_EN[dt.weekday()]
            label = f"{date_key} {wd}"
        except ValueError:
            label = date_key
        result.append({"date_label": label, "articles": arts})
    return result


def _filter_and_group(articles: list[dict], cat_keys: list[str], limit: int = TIMELINE_MAX_PER_TAB) -> list[dict]:
    filtered = [a for a in articles if a.get("category") in cat_keys][:limit]
    return _group_by_date(filtered)


def _compute_related(articles: list[dict]) -> dict[str, list[dict]]:
    """各記事に対して共通ハッシュタグベースの関連記事（最大3件）を計算する"""
    url_to_art = {a["url"]: a for a in articles if a.get("url")}
    tag_index: dict[str, list[str]] = {}
    for a in articles:
        for tag in (a.get("hashtags") or []):
            tag_index.setdefault(tag, []).append(a.get("url", ""))

    def _parent(a: dict) -> str | None:
        m = _CAT_MAP.get(a.get("category", ""))
        return m[0] if m else None

    result: dict[str, list[dict]] = {}
    for a in articles:
        url = a.get("url")
        if not url:
            continue
        tags = set(a.get("hashtags") or [])
        scores: dict[str, int] = {}
        for tag in tags:
            for other_url in tag_index.get(tag, []):
                if other_url and other_url != url:
                    scores[other_url] = scores.get(other_url, 0) + 1
        ranked = sorted(scores, key=lambda u: scores[u], reverse=True)
        selected: list[str] = ranked[:3]
        if len(selected) < 3:
            p = _parent(a)
            for other in articles:
                if len(selected) >= 3:
                    break
                ou = other.get("url")
                if ou and ou != url and ou not in selected and _parent(other) == p:
                    selected.append(ou)
        result[url] = [
            {
                "url":            u,
                "title_ja":       url_to_art[u].get("title_ja", ""),
                "published_jst":  url_to_art[u].get("published_jst", ""),
                "category_label": url_to_art[u].get("category_label", ""),
            }
            for u in selected
            if u in url_to_art
        ]
    return result


# ── HTML テンプレート ─────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
{%- macro render_mini_stars(score) -%}
{%- for i in range(1, 6) -%}<span class="mini-star {{ 'filled' if i <= score|float else 'empty' }}">★</span>{%- endfor -%}<span class="mini-val">{{ "%.1f"|format(score|float) }}</span>
{%- endmacro -%}

{%- macro render_stars(impact, axes=none) -%}
<div class="impact-block">
<span class="impact-stars" title="Impact {{ "%.1f"|format(impact|float) }}/5{% if axes and axes.per is defined %} | PER:{{ "%.1f"|format(axes.per|float) }} SCI:{{ "%.1f"|format(axes.sci|float) }} CPS:{{ "%.1f"|format(axes.cps|float) }}{% endif %}">
  {%- for i in range(1, 6) -%}<span class="star {{ 'filled' if i <= impact|float else 'empty' }}">★</span>{%- endfor -%}
  <span class="impact-val">{{ "%.1f"|format(impact|float) }}</span><span class="impact-label">Total</span>
</span>
{%- if axes and axes.per is defined and (axes.per|float + axes.sci|float + axes.cps|float) > 0 %}
<div class="impact-axes">
  <span class="axis-item" title="PER &amp; Social Impact"><span class="axis-label">PER</span>{{ render_mini_stars(axes.per) }}</span>
  <span class="axis-item" title="Utility &amp; Curiosity"><span class="axis-label">SCI</span>{{ render_mini_stars(axes.sci) }}</span>
  <span class="axis-item" title="Transformative Potential"><span class="axis-label">CPS</span>{{ render_mini_stars(axes.cps) }}</span>
</div>
{%- endif %}
</div>
{%- endmacro -%}

{%- macro render_card(article, extra_class='') -%}
{%- set is_apex = article.impact|float >= 4.5 and extra_class != 'card-hot' -%}
<article class="card {{ extra_class }}{{ ' card-apex' if is_apex }}" data-url="{{ article.url }}" data-competitor-group="{{ article.competitor_group }}">
  <span class="badge-new">{{ 'HOT' if is_apex else 'NEW' }}</span>
  <button class="fav-btn" data-url="{{ article.url }}" onclick="toggleFav('{{ article.url }}')" title="お気に入りに追加">☆</button>
  <div class="card-meta">
    <span class="card-tag">{{ article.category_label }}</span>
    {%- if article.source %}<span class="card-source">{{ article.source }}</span>{% endif %}
  </div>
  {{ render_stars(article.impact, article.impact_axes) }}
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
  <div class="insight-actions">
    <button class="copy-btn" onclick="copyInsight(this)" title="インサイトをクリップボードにコピー">シェア</button>
  </div>
  <div class="card-footer">
    <span class="card-date">{{ article.published_jst }}</span>
    <a class="read-link" href="{{ article.url }}" target="_blank" rel="noopener">原文 →</a>
  </div>
</article>
{%- endmacro -%}

{%- macro render_timeline(date_groups) -%}
{% if date_groups %}
  {% for group in date_groups %}
  <div class="date-separator"><span>{{ group.date_label }}</span></div>
  <div class="card-grid">
    {% for a in group.articles %}{{ render_card(a) }}{% endfor %}
  </div>
  {% endfor %}
{% else %}
  <div class="empty">記事はありません</div>
{% endif %}
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
      --chip-bg:          #ffffff;
      --apex-ring:        rgba(204, 19, 64, 0.45);
      --apex-glow:        rgba(204, 19, 64, 0.09);
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
      --chip-bg:          #003847;
      --apex-ring:        rgba(255, 112, 112, 0.50);
      --apex-glow:        rgba(255, 112, 112, 0.11);
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
    header.header--hidden { transform: translateY(-100%); }
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
    .tab-btn.active { color: #ffffff; border-bottom-color: #ffffff; font-weight: 700; }
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
    .sub-tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 700; }
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
    .section-divider { border: none; border-top: 1px solid var(--border); margin: 28px 0; }

    /* ===== Date Separator ===== */
    .date-separator {
      display: flex;
      align-items: center;
      gap: 12px;
      margin: 28px 0 16px;
    }
    .date-separator:first-of-type { margin-top: 0; }
    .date-separator::before,
    .date-separator::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--border);
    }
    .date-separator span {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      color: var(--muted);
      font-family: -apple-system, sans-serif;
      white-space: nowrap;
    }

    /* ===== Chip Filter Bar ===== */

    /* ===== Card Grid ===== */
    .card-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      align-items: stretch;
    }
    @media (max-width: 1080px) { .card-grid { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 640px)  { .card-grid { grid-template-columns: 1fr; gap: 12px; } }

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
      flex-direction: column;
    }
    @media (hover: hover) {
      .card:hover { box-shadow: 0 6px 20px rgba(0,0,0,0.10); transform: translateY(-2px); }
      [data-theme="dark"] .card:hover { box-shadow: 0 6px 20px rgba(0,0,0,0.45); }
    }
    .card-hot { border-top-color: var(--hot-accent); border-color: var(--hot-border); background: var(--hot-bg); }
    .card-apex {
      box-shadow: 0 0 0 1.5px var(--apex-ring), 0 4px 20px var(--apex-glow);
    }
    [data-theme="dark"] .card-apex {
      box-shadow: 0 0 0 1.5px var(--apex-ring), 0 4px 20px var(--apex-glow);
    }

    /* ===== Card Internals ===== */
    .card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: nowrap; }
    .impact-block { margin-bottom: 6px; }
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
    .card-hot .card-tag { background: var(--hot-border); color: #ffffff; }
    .impact-stars { font-size: 12px; letter-spacing: 0.01em; line-height: 1; }
    .star.filled { color: var(--star-filled); }
    .star.empty  { color: var(--star-empty); }
    .badge-new {
      position: absolute;
      top: 11px; right: 48px;
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
    .card-hot .badge-new { background: var(--hot-accent); }
    .fav-btn {
      position: absolute;
      top: 8px; right: 11px;
      background: none;
      border: none;
      font-size: 17px;
      line-height: 1;
      cursor: pointer;
      color: var(--star-empty);
      padding: 2px;
      -webkit-tap-highlight-color: transparent;
      transition: color 0.15s, transform 0.15s;
    }
    .fav-btn.favorited { color: var(--star-filled); }
    .fav-btn:hover { transform: scale(1.25); }
    .card-title {
      font-size: 14.5px;
      font-weight: 600;
      line-height: 1.5;
      min-height: 68px;
      margin-bottom: 10px;
      font-family: 'Yu Gothic', 'YuGothic', -apple-system, 'Hiragino Sans', 'Noto Sans JP', sans-serif;
    }
    .card-title a {
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
      color: var(--text);
      text-decoration: none;
    }
    .card-title a:hover { color: var(--accent); }
    .section-label-summary {
      display: inline-block;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--text2);
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
      margin: 12px 0 5px;
      font-family: -apple-system, sans-serif;
    }
    .card-summary { font-size: 13.5px; color: var(--text2); line-height: 1.68; }
    .card-insight {
      font-size: 13.5px;
      color: var(--text);
      background: var(--insight-bg);
      border-left: 3px solid var(--insight-border);
      border-radius: 0 4px 4px 0;
      padding: 10px 13px;
      line-height: 1.68;
      display: flex;
      flex-direction: column;
    }
    .card-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: auto;
      padding-top: 10px;
      border-top: 1px solid var(--border-subtle);
    }
    .card-date { font-size: 11px; color: var(--muted); font-family: -apple-system, sans-serif; }
    .read-link {
      font-size: 12px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
      font-family: -apple-system, sans-serif;
      letter-spacing: 0.02em;
    }
    .read-link:hover { text-decoration: underline; }
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
    .card-hashtags { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0 10px; }
    .hashtag {
      font-size: 10.5px;
      color: var(--accent);
      background: var(--tag-bg);
      padding: 2px 7px;
      border-radius: 10px;
      font-family: -apple-system, sans-serif;
      letter-spacing: 0.02em;
    }
    [data-theme="dark"] .hashtag { color: var(--accent2); background: var(--tag-bg); }
    .impact-val { font-size: 10px; color: var(--muted); font-family: -apple-system, sans-serif; margin-left: 2px; }
    .impact-label { font-size: 8px; color: var(--muted); font-family: -apple-system, sans-serif; letter-spacing: 0.04em; margin-left: 4px; text-transform: uppercase; }
    .impact-block { display: inline-flex; flex-direction: column; gap: 2px; }
    .impact-axes { display: flex; gap: 6px; flex-wrap: nowrap; align-items: center; }
    .axis-item { display: inline-flex; align-items: center; gap: 1px; }
    .axis-label { font-size: 7.5px; font-weight: 700; letter-spacing: 0.06em; color: var(--muted); font-family: -apple-system, sans-serif; text-transform: uppercase; min-width: 20px; }
    .mini-star { font-size: 8px; line-height: 1; }
    .mini-star.filled { color: var(--star-filled); }
    .mini-star.empty  { color: var(--star-empty); }
    .mini-val { font-size: 8px; color: var(--muted); font-family: -apple-system, sans-serif; margin-left: 1px; }
    .empty { grid-column: 1 / -1; text-align: center; color: var(--muted); padding: 60px 0; font-size: 14px; font-family: -apple-system, sans-serif; }

    /* ===== Accordion ===== */
    .exp-text { max-height: 4.8em; overflow: hidden; transition: max-height 0.32s ease; }
    .exp-btn {
      align-self: flex-end;
      background: none;
      border: none;
      color: var(--accent);
      font-size: 11px;
      font-weight: 600;
      font-family: -apple-system, sans-serif;
      cursor: pointer;
      padding: 5px 0 0;
      letter-spacing: 0.02em;
      -webkit-tap-highlight-color: transparent;
      line-height: 1;
    }
    .exp-btn:hover { text-decoration: underline; }
    .card-insight .exp-btn { color: var(--accent2); }

    /* ===== Insight Share Button ===== */
    .insight-actions {
      display: flex;
      justify-content: flex-end;
      margin-top: 6px;
    }
    .copy-btn {
      display: inline-flex;
      align-items: center;
      background: none;
      border: 1.5px solid var(--border);
      border-radius: 6px;
      padding: 6px 14px;
      min-height: 36px;
      cursor: pointer;
      color: var(--text2);
      font-size: 11px;
      font-family: -apple-system, sans-serif;
      font-weight: 600;
      letter-spacing: 0.02em;
      transition: all 0.15s;
      -webkit-tap-highlight-color: transparent;
    }
    .copy-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--tag-bg); }
    @media (max-width: 640px) { .copy-btn { min-height: 44px; } }

    /* ===== Related Articles ===== */
    .card-related {
      margin-top: 10px;
      padding: 10px 13px;
      background: var(--border-subtle);
      border-radius: var(--radius);
      border: 1px solid var(--border);
    }
    .related-label {
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
      font-family: -apple-system, sans-serif;
    }
    .related-item {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
      padding: 5px 0;
      border-bottom: 1px solid var(--border);
      text-decoration: none;
      color: var(--text);
      transition: color 0.15s;
    }
    .related-item:last-child { border-bottom: none; }
    .related-item:hover { color: var(--accent); }
    .related-title {
      font-size: 12px;
      font-weight: 500;
      line-height: 1.4;
      flex: 1;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .related-date {
      font-size: 10.5px;
      color: var(--muted);
      font-family: -apple-system, sans-serif;
      white-space: nowrap;
      flex-shrink: 0;
    }

    /* ===== Market Trends ===== */
    .trend-grid { display: flex; flex-direction: column; gap: 16px; }
    .trend-card {
      background: var(--card-bg);
      border: 1.5px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .trend-cat {
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent2);
      background: var(--tag-bg);
      padding: 2px 9px;
      border-radius: 10px;
      font-family: -apple-system, sans-serif;
      width: fit-content;
    }
    .trend-headline {
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
      line-height: 1.4;
    }
    .trend-body {
      font-size: 13px;
      color: var(--text2);
      line-height: 1.75;
      margin: 0;
      font-family: -apple-system, sans-serif;
    }
    .trend-source-link {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: var(--muted);
      text-decoration: none;
      font-family: -apple-system, sans-serif;
      margin-top: 4px;
      transition: color 0.15s;
    }
    .trend-source-link:hover { color: var(--accent); }
    .trend-note {
      font-size: 11px;
      color: var(--muted);
      font-family: -apple-system, sans-serif;
      margin-bottom: 16px;
      padding: 8px 12px;
      border-left: 3px solid var(--border);
    }

    /* ===== Competitor Directory ===== */
    .dir-section { margin-bottom: 32px; }
    .dir-heading {
      font-size: 11px;
      font-weight: 700;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.1em;
      margin-bottom: 12px;
      padding-bottom: 8px;
      border-bottom: 1.5px solid var(--border);
      font-family: -apple-system, sans-serif;
    }
    .dir-grid { display: flex; flex-wrap: wrap; gap: 10px; }
    .dir-card {
      display: inline-flex;
      align-items: center;
      padding: 7px 18px;
      border: 1.5px solid var(--border);
      border-radius: 99px;
      background: var(--card-bg);
      color: var(--text);
      font-size: 13px;
      font-weight: 600;
      font-family: -apple-system, sans-serif;
      text-decoration: none;
      transition: border-color 0.15s, color 0.15s;
      white-space: nowrap;
    }
    .dir-card:hover { border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <div class="header-brand">
        <h1>Human Science Insights</h1>
        <span class="header-updated">{{ updated }}</span>
      </div>
      <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()">Dark Mode</button>
    </div>
    <nav class="nav-bar" role="tablist">
      <button class="tab-btn active" data-parent="all"        onclick="showParent('all')">All</button>
      <button class="tab-btn"        data-parent="ai"         onclick="showParent('ai')">AI</button>
      <button class="tab-btn"        data-parent="neuro"      onclick="showParent('neuro')">脳科学</button>
      <button class="tab-btn"        data-parent="competitor" onclick="showParent('competitor')">他社リサーチ</button>
      <button class="tab-btn"        data-parent="favorites"  onclick="showParent('favorites')">★ お気に入り</button>
    </nav>
  </header>

  <!-- AI サブナビ -->
  <div class="sub-nav-bar hidden" id="sub-nav-ai">
    <button class="sub-tab-btn active" data-sub="product"  onclick="showSub('ai','product')">プロダクト速報</button>
    <button class="sub-tab-btn"        data-sub="business" onclick="showSub('ai','business')">プロダクトTips</button>
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
  <!-- 他社リサーチ サブナビ -->
  <div class="sub-nav-bar hidden" id="sub-nav-competitor">
    <button class="sub-tab-btn active" data-sub="press"      onclick="showSub('competitor','press')">競合プレスリリース</button>
    <button class="sub-tab-btn"        data-sub="trend"      onclick="showSub('competitor','trend')">業界・市場トレンド</button>
    <button class="sub-tab-btn"        data-sub="directory"  onclick="showSub('competitor','directory')">競合他社URL</button>
  </div>

  <main>
    <!-- ===== All パネル ===== -->
    <div id="panel-all">
      {% if panels.all.hot %}
      <section>
        <div class="section-heading">Hot Topics — パラダイムシフト候補</div>
        <div class="card-grid">
          {% for a in panels.all.hot %}{{ render_card(a, 'card-hot') }}{% endfor %}
        </div>
      </section>
      <hr class="section-divider">
      {% endif %}
      <div class="section-heading regular">All Articles</div>
      {{ render_timeline(panels.all.timeline) }}
    </div>

    <!-- ===== AI パネル ===== -->
    <div id="panel-ai" class="hidden">
      <div class="sub-panel" id="sub-panel-ai-product">
        {{ render_timeline(panels.ai.product) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-ai-business">
        {{ render_timeline(panels.ai.business) }}
      </div>
    </div>

    <!-- ===== 脳科学 パネル ===== -->
    <div id="panel-neuro" class="hidden">
      <div class="sub-panel" id="sub-panel-neuro-social">
        {{ render_timeline(panels.neuro.social) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-neuro-press">
        {{ render_timeline(panels.neuro.press) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-neuro-research">
        <div class="sub3-panel" id="sub3-panel-embodiment">
          {{ render_timeline(panels.neuro.research.embodiment) }}
        </div>
        <div class="sub3-panel hidden" id="sub3-panel-psychology">
          {{ render_timeline(panels.neuro.research.psychology) }}
        </div>
      </div>
    </div>

    <!-- ===== 他社リサーチ パネル ===== -->
    <div id="panel-competitor" class="hidden">
      <div class="sub-panel" id="sub-panel-competitor-press">
        {{ render_timeline(panels.competitor.press) }}
      </div>
      <div class="sub-panel hidden" id="sub-panel-competitor-trend">
        <p class="trend-note">このタブは定期的に人手でキュレーションされる、業界の構造的トレンドをまとめたページです。毎朝のニュースではなく、現時点で多数の企業が注目している・困っているテーマを掲載しています。</p>
        <div class="trend-grid">
          {%- for t in market_trends %}
          <div class="trend-card">
            <span class="trend-cat">{{ t.category }}</span>
            <div class="trend-headline">{{ t.headline }}</div>
            <p class="trend-body">{{ t.body }}</p>
            <a class="trend-source-link" href="{{ t.url }}" target="_blank" rel="noopener">出典：{{ t.source }} →</a>
          </div>
          {%- endfor %}
        </div>
      </div>
      <div class="sub-panel hidden" id="sub-panel-competitor-directory">
        {%- for cat_name, companies in competitor_directory %}
        <div class="dir-section">
          <div class="dir-heading">{{ cat_name }}</div>
          <div class="dir-grid">
            {%- for name, url in companies %}
            <a class="dir-card" href="{{ url }}" target="_blank" rel="noopener">{{ name }}</a>
            {%- endfor %}
          </div>
        </div>
        {%- endfor %}
      </div>
    </div>

    <!-- ===== お気に入り パネル ===== -->
    <div id="panel-favorites" class="hidden">
      <div class="empty fav-empty-msg">お気に入り記事はありません。記事カードの ☆ ボタンで登録できます。</div>
    </div>
  </main>

  <script>
    /* ── State ── */
    let currentParent     = 'all';
    let currentSubAi      = 'product';
    let currentSubNeuro   = 'social';
    let currentSubComp    = 'press';
    let currentSub3       = 'embodiment';

    /* ── Parent tab switching ── */
    function showParent(tab) {
      console.log('[showParent] tab=' + tab + ' (prev=' + currentParent + ')');
      currentParent = tab;
      document.querySelectorAll('.nav-bar .tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.parent === tab));
      document.getElementById('sub-nav-ai').classList.toggle('hidden',         tab !== 'ai');
      document.getElementById('sub-nav-neuro').classList.toggle('hidden',      tab !== 'neuro');
      document.getElementById('sub-nav-competitor').classList.toggle('hidden', tab !== 'competitor');
      const show3 = tab === 'neuro' && currentSubNeuro === 'research';
      document.getElementById('sub3-nav-research').classList.toggle('hidden', !show3);
      document.getElementById('panel-all').classList.toggle('hidden',        tab !== 'all');
      document.getElementById('panel-ai').classList.toggle('hidden',         tab !== 'ai');
      document.getElementById('panel-neuro').classList.toggle('hidden',      tab !== 'neuro');
      document.getElementById('panel-competitor').classList.toggle('hidden', tab !== 'competitor');
      document.getElementById('panel-favorites').classList.toggle('hidden',  tab !== 'favorites');
      if (tab === 'favorites') renderFavorites();
      requestAnimationFrame(function() {
        var panelEl = document.getElementById('panel-' + tab);
        console.log('[showParent] rAF: initExpBtns for #panel-' + tab + ', found=' + !!panelEl);
        initExpBtns(panelEl);
      });
    }

    /* ── Sub tab switching ── */
    function showSub(parent, tab) {
      console.log('[showSub] parent=' + parent + ' tab=' + tab);
      if (parent === 'ai') {
        currentSubAi = tab;
        document.querySelectorAll('#sub-nav-ai .sub-tab-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['product', 'business'].forEach(k =>
          document.getElementById(`sub-panel-ai-${k}`).classList.toggle('hidden', k !== tab));
      } else if (parent === 'neuro') {
        currentSubNeuro = tab;
        document.querySelectorAll('#sub-nav-neuro .sub-tab-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['social', 'press', 'research'].forEach(k =>
          document.getElementById(`sub-panel-neuro-${k}`).classList.toggle('hidden', k !== tab));
        document.getElementById('sub3-nav-research').classList.toggle('hidden', tab !== 'research');
      } else if (parent === 'competitor') {
        currentSubComp = tab;
        document.querySelectorAll('#sub-nav-competitor .sub-tab-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['press', 'trend', 'directory'].forEach(k =>
          document.getElementById(`sub-panel-competitor-${k}`).classList.toggle('hidden', k !== tab));
      }
      requestAnimationFrame(function() {
        var pid = 'sub-panel-' + parent + '-' + tab;
        var subEl = document.getElementById(pid);
        console.log('[showSub] rAF: initExpBtns for #' + pid + ', found=' + !!subEl);
        initExpBtns(subEl);
      });
    }

    /* ── Sub3 tab switching (脳科学 > 研究) ── */
    function showSub3(tab) {
      console.log('[showSub3] tab=' + tab);
      currentSub3 = tab;
      document.querySelectorAll('#sub3-nav-research .sub-tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.sub3 === tab));
      ['embodiment', 'psychology'].forEach(k =>
        document.getElementById(`sub3-panel-${k}`).classList.toggle('hidden', k !== tab));
      requestAnimationFrame(function() {
        var pid = 'sub3-panel-' + tab;
        var sub3El = document.getElementById(pid);
        console.log('[showSub3] rAF: initExpBtns for #' + pid + ', found=' + !!sub3El);
        initExpBtns(sub3El);
      });
    }


    /* ── Dark / Light mode ── */
    function toggleTheme() {
      const html   = document.documentElement;
      const isDark = html.dataset.theme === 'dark';
      const next   = isDark ? 'light' : 'dark';
      html.dataset.theme = next;
      document.getElementById('theme-toggle').textContent = isDark ? 'Dark Mode' : 'Light Mode';
      localStorage.setItem('hsi_theme_v2', next);
    }
    (function () {
      const saved = localStorage.getItem('hsi_theme_v2');
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
      var isInsight = !!btn.closest('.card-insight');
      var card = btn.closest('.card');
      var relatedEl = card ? card.querySelector('.card-related') : null;
      if (el.classList.contains('open')) {
        el.style.maxHeight = el.scrollHeight + 'px';
        el.classList.remove('open');
        requestAnimationFrame(function () { el.style.maxHeight = '4.8em'; });
        btn.textContent = '続きを読む...';
        if (isInsight && relatedEl) relatedEl.classList.add('hidden');
      } else {
        el.classList.add('open');
        el.style.maxHeight = el.scrollHeight + 'px';
        btn.textContent = '閉じる';
        el.addEventListener('transitionend', function handler() {
          if (el.classList.contains('open')) el.style.maxHeight = 'none';
          el.removeEventListener('transitionend', handler);
        });
        if (isInsight && relatedEl) relatedEl.classList.remove('hidden');
      }
    }

    /* ── Copy Insight to Clipboard ── */
    function copyInsight(btn) {
      var card = btn.closest('.card');
      if (!card) return;
      var titleEl   = card.querySelector('.card-title a');
      var insightEl = card.querySelector('.card-insight .exp-text');
      var title   = titleEl   ? titleEl.textContent.trim()   : '';
      var insight = insightEl ? insightEl.textContent.trim() : '';
      var url     = card.dataset.url || '';
      var text    = [title, url].join('\\n');
      function feedback() {
        var orig = btn.textContent;
        btn.textContent = 'コピー済み ✓';
        setTimeout(function() { btn.textContent = orig; }, 2000);
      }
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(feedback);
      } else {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        feedback();
      }
    }

    /* ── checkExpBtn / initExpBtns ──────────────────────────────────────────
       【設計方針】
       - data-exp-init ガードを廃止: hidden パネルでは scrollHeight=0 になるため
         window.load 時に「短いテキスト」と誤判定→永続的に非表示になるバグを根絶。
       - scrollHeight===0 チェックで不可視要素をスキップ（判定を延期するだけ）。
       - ResizeObserver で「パネルが可視化された瞬間」を自動検知して再判定する。
         これにより showParent/showSub 呼び出し後に RAF を使わなくても確実に動く。
       - RAF + initExpBtns 呼び出しは念のため残し二重の安全策とする。
    ─────────────────────────────────────────────────────────────────────── */

    /* 1ボタン分の表示/非表示を判定 */
    function checkExpBtn(btn) {
      var el = btn.previousElementSibling;
      if (!el || !el.classList.contains('exp-text')) return;
      if (el.classList.contains('open')) return;
      if (el.scrollHeight === 0) {
        /* 不可視要素 (display:none の祖先あり) → 判定スキップ。
           ResizeObserver が可視化を検知して再コールされる。 */
        console.log('[checkExpBtn] skip: scrollHeight=0 (element not visible yet)');
        return;
      }
      var shouldHide = el.scrollHeight <= el.clientHeight + 4;
      console.log('[checkExpBtn] scrollH=' + el.scrollHeight
        + ' clientH=' + el.clientHeight + ' → ' + (shouldHide ? 'HIDE' : 'SHOW'));
      btn.style.display = shouldHide ? 'none' : '';
    }

    /* root 内の全 .exp-btn を評価し ResizeObserver をアタッチ */
    function initExpBtns(root) {
      console.log('[initExpBtns] start root=' + (root ? '#' + (root.id || root.className) : 'document'));
      var target = root || document;
      var total = 0, skipped = 0;
      target.querySelectorAll('.exp-btn').forEach(function(btn) {
        total++;
        checkExpBtn(btn);
        /* ResizeObserver: 0→実寸へのリサイズ（パネル可視化）を自動検知 */
        var el = btn.previousElementSibling;
        if (!el || !el.classList.contains('exp-text') || !window.ResizeObserver) return;
        if (el.dataset.roAttached) { skipped++; return; }
        el.dataset.roAttached = '1';
        (new ResizeObserver(function() {
          console.log('[ResizeObserver] fired, re-checking btn');
          checkExpBtn(btn);
        })).observe(el);
      });
      console.log('[initExpBtns] done: total=' + total + ' ro-skipped=' + skipped);
    }

    /* window.load: 全要素に ResizeObserver をアタッチ。
       hidden パネル内ボタンは checkExpBtn でスキップされ、
       タブ表示時に ResizeObserver が自動的に再判定を走らせる。 */
    window.addEventListener('load', function() {
      console.log('[load] initExpBtns on full document');
      initExpBtns();
    });

    /* ── Scroll-hide header ── */
    (function () {
      const hdr = document.querySelector('header');
      const THRESHOLD = 6;
      let lastY = window.scrollY, ticking = false;
      function onScroll() {
        if (!ticking) {
          requestAnimationFrame(() => {
            const y = window.scrollY, diff = y - lastY;
            if (diff > THRESHOLD && y > 60) hdr.classList.add('header--hidden');
            else if (diff < -THRESHOLD || y <= 0) hdr.classList.remove('header--hidden');
            lastY = y; ticking = false;
          });
          ticking = true;
        }
      }
      window.addEventListener('scroll', onScroll, { passive: true });
    })();

    /* ── お気に入り機能 ── */
    const FAV_KEY = 'hsi_fav_v1';
    function getFavs() {
      try { return JSON.parse(localStorage.getItem(FAV_KEY) || '{}'); }
      catch { return {}; }
    }
    function getCardData(card) {
      const axes = {};
      const title = card.querySelector('.impact-stars')?.getAttribute('title') || '';
      const m = title.match(/PER:([0-9.]+) SCI:([0-9.]+) CPS:([0-9.]+)/);
      if (m) { axes.per = parseFloat(m[1]); axes.sci = parseFloat(m[2]); axes.cps = parseFloat(m[3]); }
      return {
        url:            card.dataset.url,
        title_ja:       card.querySelector('.card-title a')?.textContent?.trim() || '',
        summary:        card.querySelector('.card-summary')?.textContent?.trim() || '',
        insight:        card.querySelector('.card-insight .exp-text')?.textContent?.trim() || '',
        category_label: card.querySelector('.card-tag')?.textContent?.trim() || '',
        source:         card.querySelector('.card-source')?.textContent?.trim() || '',
        published_jst:  card.querySelector('.card-date')?.textContent?.trim() || '',
        impact:         parseFloat(card.querySelector('.impact-val')?.textContent || '0'),
        impact_axes:    axes,
        hashtags:       [...card.querySelectorAll('.hashtag')].map(h => h.textContent.trim()),
        competitor_group: card.dataset.competitorGroup || '',
      };
    }
    function toggleFav(url) {
      const favs = getFavs();
      if (favs[url]) {
        delete favs[url];
      } else {
        const card = document.querySelector(`.card[data-url="${CSS.escape(url)}"]`);
        if (card) favs[url] = getCardData(card);
      }
      localStorage.setItem(FAV_KEY, JSON.stringify(favs));
      refreshFavBtns();
      if (currentParent === 'favorites') renderFavorites();
    }
    function refreshFavBtns() {
      const favs = getFavs();
      document.querySelectorAll('.fav-btn').forEach(btn => {
        const isFav = !!favs[btn.dataset.url];
        btn.classList.toggle('favorited', isFav);
        btn.textContent = isFav ? '★' : '☆';
        btn.title = isFav ? 'お気に入りから削除' : 'お気に入りに追加';
      });
    }
    function renderMiniStars(score) {
      const s = parseFloat(score) || 0;
      const stars = Array.from({length:5}, (_,i) =>
        `<span class="mini-star ${i < s ? 'filled' : 'empty'}">★</span>`).join('');
      return stars + `<span class="mini-val">${s.toFixed(1)}</span>`;
    }
    function renderFavCard(a) {
      const imp = parseFloat(a.impact) || 0;
      const stars = Array.from({length:5}, (_,i) =>
        `<span class="star ${i < imp ? 'filled' : 'empty'}">★</span>`).join('');
      const ax = a.impact_axes || {};
      const axHasData = ax.per != null && (ax.per + ax.sci + ax.cps) > 0;
      const axTitle = axHasData
        ? ` | PER:${ax.per} SCI:${ax.sci} CPS:${ax.cps}` : '';
      const axesHtml = axHasData ? `
    <div class="impact-axes">
      <span class="axis-item" title="PER &amp; Social Impact"><span class="axis-label">PER</span>${renderMiniStars(ax.per)}</span>
      <span class="axis-item" title="Utility &amp; Curiosity"><span class="axis-label">SCI</span>${renderMiniStars(ax.sci)}</span>
      <span class="axis-item" title="Transformative Potential"><span class="axis-label">CPS</span>${renderMiniStars(ax.cps)}</span>
    </div>` : '';
      const hashtags = (a.hashtags||[]).map(t=>`<span class="hashtag">${t}</span>`).join('');
      const src = a.source ? `<span class="card-source">${a.source}</span>` : '';
      return `<article class="card" data-url="${a.url}" data-competitor-group="${a.competitor_group||''}">
  <span class="badge-new">FAV</span>
  <button class="fav-btn favorited" data-url="${a.url}" onclick="toggleFav('${a.url.replace(/'/g,"\\'")}')">★</button>
  <div class="card-meta">
    <span class="card-tag">${a.category_label||''}</span>${src}
  </div>
  <div class="impact-block">
    <span class="impact-stars" title="Impact ${imp.toFixed(1)}/5${axTitle}">${stars}<span class="impact-val">${imp.toFixed(1)}</span><span class="impact-label">Total</span></span>${axesHtml}
  </div>
  <div class="card-title"><a href="${a.url}" target="_blank" rel="noopener">${a.title_ja}</a></div>
  ${hashtags ? `<div class="card-hashtags">${hashtags}</div>` : ''}
  <div class="section-label-summary">Abstract</div>
  <p class="card-summary exp-text">${a.summary}</p>
  <button class="exp-btn" onclick="toggleExp(this)">続きを読む...</button>
  <div class="section-label-insight">Insight</div>
  <div class="card-insight">
    <div class="exp-text">${a.insight}</div>
    <button class="exp-btn" onclick="toggleExp(this)">続きを読む...</button>
  </div>
  <div class="insight-actions">
    <button class="copy-btn" onclick="copyInsight(this)" title="インサイトをクリップボードにコピー">シェア</button>
  </div>
  <div class="card-footer">
    <span class="card-date">${a.published_jst}</span>
    <a class="read-link" href="${a.url}" target="_blank" rel="noopener">原文 →</a>
  </div>
</article>`;
    }
    function renderFavorites() {
      const favs = getFavs();
      const panel = document.getElementById('panel-favorites');
      const keys = Object.keys(favs);
      if (keys.length === 0) {
        panel.innerHTML = '<div class="empty">お気に入り記事はありません。記事カードの ☆ ボタンで登録できます。</div>';
        return;
      }
      const cards = keys.map(url => renderFavCard(favs[url])).join('');
      panel.innerHTML = `<div class="section-heading regular">お気に入り（${keys.length}件）</div>
<div class="card-grid">${cards}</div>`;
      requestAnimationFrame(function() { initExpBtns(panel); });
    }
    refreshFavBtns();

    /* ── 未読バッジ管理 ── */
    const READ_KEY = 'hsi_read_v2';
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


def generate_html(
    processed: dict[str, list[ProcessedArticle]],
    output_path: str = "docs/index.html",
    cache_dir: str | None = None,
    max_days: int = TIMELINE_MAX_DAYS,
) -> None:
    """精査済み記事データと過去キャッシュを合算してindex.htmlを生成する。"""
    now_jst = datetime.now(JST)
    updated_str = now_jst.strftime("%Y-%m-%d %H:%M JST")

    # 過去キャッシュ読み込み
    raw: list[dict] = []
    if cache_dir:
        raw = _load_all_days(cache_dir, max_days)

    # 今日分は processed から補完（キャッシュに含まれていない場合の保険）
    cached_urls = {a.get("url", "") for a in raw}
    for cat_arts in processed.values():
        for a in cat_arts:
            if a.get("url", "") not in cached_urls:
                raw.append(a)
                cached_urls.add(a.get("url", ""))

    if not raw:
        raw = [a for arts in processed.values() for a in arts]

    # エンリッチ → 日時降順ソート
    all_articles = _enrich_articles(raw)
    all_articles.sort(key=lambda a: a.get("published", ""), reverse=True)

    # 関連記事を計算し各記事に付与
    related_map = _compute_related(all_articles)
    for a in all_articles:
        a["related"] = related_map.get(a.get("url", ""), [])

    # Hot Topics 更新
    if cache_dir:
        hot_articles = update_hot_topics(cache_dir, all_articles)
    else:
        hot_cands = [
            a for a in all_articles
            if a.get("impact", 0.0) >= HOT_SCORE_THRESHOLD and a.get("category") in _HOT_CATS
        ]
        hot_cands.sort(key=lambda a: (a.get("impact", 0.0), a.get("published", "")), reverse=True)
        hot_articles = hot_cands[:HOT_TOPICS_MAX]

    hot_url_set = {a["url"] for a in hot_articles}

    # Hot を除いた全記事
    non_hot = [a for a in all_articles if a["url"] not in hot_url_set]

    # パネルデータ構築
    # サブタブは hot 記事も含む全記事から引く（non_hot 除外は All タブ専用）
    def _ft(cat_keys: list[str], limit: int = TIMELINE_MAX_PER_TAB) -> list[dict]:
        return _filter_and_group(all_articles, cat_keys, limit)

    panels = {
        "all": {
            "hot":      hot_articles,
            "timeline": _group_by_date(non_hot[:TIMELINE_MAX_ALL]),
        },
        "ai": {
            "product":  _ft(["ai_product", "ai_press"]),
            "business": _ft(["ai_business", "ai_social", "ai_academic"]),
        },
        "neuro": {
            "social":   _ft(["neuro_social"]),
            "press":    _ft(["neuro_press"]),
            "research": {
                "embodiment": _ft(["neuro_embodiment"]),
                "psychology": _ft(["neuro_psychology", "neuro_ai"]),
            },
        },
        "competitor": {
            "press": _ft(["competitor_press", "hr_press"]),
            "trend": _ft(["industry_trend", "hr_social", "hr_academic"]),
        },
    }

    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(HTML_TEMPLATE)
    html = tmpl.render(
        updated=updated_str,
        panels=panels,
        competitor_directory=COMPETITOR_DIRECTORY,
        market_trends=MARKET_TRENDS,
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[generate] Written to {output_path}")
