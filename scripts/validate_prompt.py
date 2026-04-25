"""
2ステップパイプライン検証スクリプト
Step A（スクリーニング）→ Step B（深層解析）の両プロンプトを検証する
"""

from __future__ import annotations

import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

import anthropic
from process import SCREENING_PROMPT, DEEP_ANALYSIS_PROMPT, SCREENING_MODEL, ANALYSIS_MODEL

SAMPLE_ARTICLES = [
    {
        "index": 0,
        "lang": "en",
        "category": "AIプロダクト速報",
        "title": "Anthropic launches Claude for Work with real-time tool use and memory",
        "description": (
            "Anthropic today announced Claude for Work, an enterprise platform that combines "
            "real-time web search, code execution, and persistent memory across sessions. "
            "The system allows managers to configure AI agents that can access internal wikis, "
            "draft performance reviews, and schedule 1-on-1 meetings autonomously. Pricing starts "
            "at $25/user/month for teams above 50 seats."
        ),
    },
    {
        "index": 1,
        "lang": "en",
        "category": "脳科学・社会実装",
        "title": "Oxytocin release during team rituals predicts six-month retention, Stanford study finds",
        "description": (
            "A Stanford study of 1,240 employees across 14 companies found that measurable oxytocin "
            "release during onboarding ceremonies, weekly stand-ups, and goal-setting rituals was "
            "the strongest predictor of six-month retention (r=0.71), outperforming salary and role "
            "clarity. Synchronized physiological arousal—not just verbal agreement—distinguished "
            "high-cohesion teams. Researchers propose that ritual design, not ping-pong tables, "
            "is the true lever for belonging."
        ),
    },
    {
        "index": 2,
        "lang": "en",
        "category": "競合プレスリリース",
        "title": "Korn Ferry launches AI-powered succession planning tool integrated with LinkedIn Talent Insights",
        "description": (
            "Korn Ferry announced a new succession planning platform that ingests LinkedIn Talent "
            "Insights data to automatically identify internal successors and benchmark them against "
            "external talent pools. The tool generates 'readiness scores' for each candidate and "
            "delivers development roadmaps. It is now bundled into Korn Ferry's enterprise talent "
            "management suite at no additional cost for existing clients."
        ),
    },
    {
        "index": 3,
        "lang": "en",
        "category": "業界・市場トレンド",
        "title": "SHRM 2026 report: 68% of HR leaders say employee motivation gap is top barrier to productivity",
        "description": (
            "The Society for Human Resource Management's 2026 State of the Workplace report found "
            "that 68% of HR leaders identify the 'motivation gap'—employees who show up but are "
            "not energized—as the #1 barrier to productivity, ahead of skills shortages (54%) and "
            "hybrid work friction (47%). Only 12% of respondents say their current engagement "
            "platforms give them actionable data on intrinsic motivation drivers. Companies "
            "reporting above-median engagement scores showed 2.3x higher revenue per employee."
        ),
    },
    {
        "index": 4,
        "lang": "en",
        "category": "AIプロダクト速報",
        "title": "New JavaScript framework released for hobby developers",
        "description": (
            "A new lightweight JavaScript framework targeting hobbyist developers was released today. "
            "It focuses on simplicity and has no enterprise features. Suitable for personal projects."
        ),
    },
]


def _call(client: anthropic.Anthropic, system: str, user: str, model: str, max_tokens: int) -> list[dict]:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    raw = resp.content[0].text.strip()
    import re
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
    return json.loads(cleaned), resp.usage


def run_validation() -> None:
    client = anthropic.Anthropic()

    print("=== 2ステップパイプライン検証 ===\n")
    print(f"スクリーニングモデル : {SCREENING_MODEL}")
    print(f"深層解析モデル       : {ANALYSIS_MODEL}")
    print(f"サンプル記事数       : {len(SAMPLE_ARTICLES)}\n")

    # ── Step A: スクリーニング ────────────────────────────────────────────────
    screen_input = "\n".join(
        f"[{a['index']}] lang={a['lang']}\n  title: {a['title']}\n  description: {a['description']}\n"
        for a in SAMPLE_ARTICLES
    )
    print("── Step A: スクリーニング APIコール中... ──\n")
    screen_results, screen_usage = _call(
        client,
        SCREENING_PROMPT,
        f"以下の記事を判定してください:\n\n{screen_input}",
        SCREENING_MODEL,
        max_tokens=512,
    )
    screen_map = {r["index"]: r for r in screen_results}

    print(f"{'='*65}")
    for r in screen_results:
        sel = "✓" if r.get("selected") else "✗"
        hot = "🔥" if r.get("hot") else "  "
        print(f"  [{r['index']}] {sel}{hot} cat={r.get('category','?'):<20} {r.get('title_ja','')[:40]}")
    print(f"\n【Step A トークン】入力: {screen_usage.input_tokens}  出力: {screen_usage.output_tokens}")
    cr = getattr(screen_usage, "cache_read_input_tokens", 0)
    cc = getattr(screen_usage, "cache_creation_input_tokens", 0)
    if cr or cc: print(f"  キャッシュ読込: {cr}  作成: {cc}")

    # ── Step B: 選別 & 擬似フェッチ ─────────────────────────────────────────
    selected = [a for a in SAMPLE_ARTICLES if screen_map.get(a["index"], {}).get("selected")]
    print(f"\n選別通過: {len(selected)}/{len(SAMPLE_ARTICLES)} 件\n")

    if not selected:
        print("選別通過記事が0件のため Step C をスキップします。")
        return

    # Step B では実際にURLフェッチはせずdescriptionをcontentとして使う
    analysis_input = [
        {
            "index": i,
            "lang": a["lang"],
            "category": screen_map[a["index"]].get("category", ""),
            "title_ja": screen_map[a["index"]].get("title_ja", a["title"]),
            "content": a["description"],
        }
        for i, a in enumerate(selected)
    ]

    # ── Step C: 深層解析 ──────────────────────────────────────────────────────
    print("── Step C: 深層解析 APIコール中... ──\n")
    deep_results, deep_usage = _call(
        client,
        DEEP_ANALYSIS_PROMPT,
        "以下の記事（全文テキスト含む）を深層解析し、"
        "summary / insight / impact / impact_axes / hashtags をJSON配列で出力してください:\n\n"
        + json.dumps(analysis_input, ensure_ascii=False, indent=2),
        ANALYSIS_MODEL,
        max_tokens=4096,
    )

    print(f"{'='*65}\n")
    for r in deep_results:
        local_idx = r.get("index", 0)
        orig_article = selected[local_idx] if local_idx < len(selected) else {}
        screen = screen_map.get(orig_article.get("index", -1), {})
        print(f"[記事 {orig_article.get('index','?')}] {orig_article.get('category', '')}")
        print(f"  訳題  : {screen.get('title_ja', '')}")
        print(f"  判定  : selected=True  hot={screen.get('hot')}  impact={r.get('impact')}  category={screen.get('category')}")
        print(f"  要約  : {r.get('summary', '')}")
        print(f"  洞察  : {r.get('insight', '')}")
        axes = r.get("impact_axes") or {}
        print(f"  3軸   : PER={axes.get('per','-')}  SCI={axes.get('sci','-')}  CPS={axes.get('cps','-')}")
        print(f"  タグ  : {' '.join(r.get('hashtags', []))}")
        print()

    # LM用語チェック
    lm_terms = [
        "Center-pin", "Sense Making", "Unfreeze", "Confidence", "Commit",
        "Corporate-identity", "PCマトリクス", "モチベーションエンジニアリング",
        "ICE BLOCK", "INK BLOT", "農耕型", "狩猟型", "EPS", "PER", "i-Company",
        "4C", "C1", "C2", "C3", "C4",
    ]
    all_text = " ".join(r.get("summary", "") + r.get("insight", "") for r in deep_results)
    found   = [t for t in lm_terms if t in all_text]
    missing = [t for t in lm_terms if t not in all_text]
    print(f"{'='*65}")
    print(f"【LM OS 5.0 用語チェック】")
    print(f"  使用済み ({len(found)}): {', '.join(found)}")
    print(f"  未使用   ({len(missing)}): {', '.join(missing)}")

    print(f"\n【Step C トークン】入力: {deep_usage.input_tokens}  出力: {deep_usage.output_tokens}")
    cr = getattr(deep_usage, "cache_read_input_tokens", 0)
    cc = getattr(deep_usage, "cache_creation_input_tokens", 0)
    if cr or cc: print(f"  キャッシュ読込: {cr}  作成: {cc}")


if __name__ == "__main__":
    run_validation()
