"""精査済み記事データからindex.htmlを生成する"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

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
}

_HOT_CATS: frozenset[str] = frozenset({"ai_social", "ai_press", "ai_academic"})

HTML_TEMPLATE = """\
{%- macro render_card(article, extra_class='') -%}
<article class="card {{ extra_class }}" data-url="{{ article.url }}">
  <span class="badge-new">NEW</span>
  <div class="card-title">
    <a href="{{ article.url }}" target="_blank" rel="noopener">{% if extra_class == 'card-hot' %}🔥 {% endif %}{{ article.title_ja }}</a>
  </div>
  <div class="section-label">要約</div>
  <p class="card-summary">{{ article.summary }}</p>
  <div class="section-label">インサイト</div>
  <div class="card-insight">{{ article.insight }}</div>
  <div class="card-footer">
    <span class="card-date">{{ article.published_jst }}</span>
    <a class="read-link" href="{{ article.url }}" target="_blank" rel="noopener">元記事 →</a>
  </div>
</article>
{%- endmacro -%}
{%- macro render_panel(articles) -%}
{% if articles %}{% for a in articles %}{{ render_card(a) }}{% endfor %}{% else %}<div class="empty">本日の注目記事はありません</div>{% endif %}
{%- endmacro -%}
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My News Digest</title>
  <style>
    :root {
      --bg: #fcfce8;
      --surface: #ffffff;
      --insight-bg: #fde8ec;
      --accent: #CC1340;
      --text: #333333;
      --muted: #888888;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, 'Hiragino Sans', 'Noto Sans JP', sans-serif;
      font-size: 16.5px;
      line-height: 1.65;
      min-height: 100vh;
    }

    /* ── ヘッダー ── */
    header {
      background: var(--accent);
      padding: 12px 16px 0;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .header-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
    }
    h1 { font-size: 19px; font-weight: 700; color: #fff; letter-spacing: 0.02em; }
    .updated { font-size: 12px; color: rgba(255,255,255,0.72); }

    /* ── ナビゲーション ── */
    .nav-row    { padding-bottom: 10px; }
    .sub-nav-row  { padding: 5px 0 9px; }
    .sub3-nav-row { padding: 4px 0 8px; }

    .seg-group {
      display: inline-flex;
      background: rgba(255,255,255,0.18);
      border-radius: 999px;
      padding: 3px;
      gap: 2px;
    }
    .seg-btn {
      background: transparent;
      border: none;
      color: rgba(255,255,255,0.72);
      font-size: 14px;
      font-weight: 500;
      padding: 7px 18px;
      border-radius: 999px;
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.18s, color 0.18s;
      -webkit-tap-highlight-color: transparent;
    }
    .seg-btn.active {
      background: #ffffff;
      color: var(--accent);
      font-weight: 700;
    }
    /* 第2階層 */
    .sub-nav-row .seg-btn { font-size: 13px; padding: 5px 15px; }
    /* 第3階層 */
    .sub3-nav-row .seg-group { background: rgba(255,255,255,0.12); }
    .sub3-nav-row .seg-btn {
      font-size: 12px;
      padding: 4px 14px;
      color: rgba(255,255,255,0.62);
    }
    .sub3-nav-row .seg-btn.active {
      background: rgba(255,255,255,0.88);
      color: var(--accent);
      font-weight: 600;
    }
    .hidden { display: none !important; }

    /* ── レイアウト ── */
    main { padding: 16px 16px 48px; max-width: 720px; margin: 0 auto; }

    /* ── Hot Topics ── */
    .hot-section { margin-bottom: 10px; }
    .hot-heading {
      font-size: 14px;
      font-weight: 800;
      letter-spacing: 0.02em;
      color: var(--accent);
      margin-bottom: 14px;
    }
    .section-divider {
      border: none;
      border-top: 1px solid rgba(0,0,0,0.09);
      margin: 4px 0 20px;
    }

    /* ── カード（共通） ── */
    .card {
      position: relative;
      background: var(--surface);
      border-radius: var(--radius);
      border-left: 4px solid var(--accent);
      box-shadow: 0 1px 6px rgba(0,0,0,0.08);
      padding: 16px;
      margin-bottom: 14px;
      opacity: 0.72;
      transform: scale(0.985);
      transform-origin: center top;
      transition: transform 0.3s ease, box-shadow 0.3s ease, opacity 0.3s ease;
      will-change: transform, opacity;
    }
    .card.in-view { opacity: 1; transform: scale(1); }
    @media (hover: hover) {
      .card:hover, .card.in-view:hover {
        transform: scale(1.01);
        box-shadow: 0 4px 14px rgba(0,0,0,0.11);
        opacity: 1;
      }
    }
    /* Hot カード */
    .card-hot {
      background: #fff7f0;
      border: 2px solid var(--accent);
      border-top: 4px solid var(--accent);
      padding: 20px;
      box-shadow: 0 4px 16px rgba(204,19,64,0.15);
    }
    .card-hot .card-title { font-size: 17.5px; }
    /* 未読バッジ */
    .badge-new {
      position: absolute;
      top: 10px; right: 10px;
      background: var(--accent);
      color: #fff;
      font-size: 10px; font-weight: 700;
      letter-spacing: 0.06em; line-height: 1;
      padding: 3px 8px;
      border-radius: 999px;
      pointer-events: none;
    }
    /* カード内要素 */
    .card-title { font-size: 16.5px; font-weight: 600; margin-bottom: 8px; line-height: 1.45; }
    .card-title a { color: var(--text); text-decoration: none; }
    .card-title a:hover { color: var(--accent); }
    .section-label {
      font-size: 11px; font-weight: 700;
      letter-spacing: 0.08em; color: var(--accent);
      text-transform: uppercase; margin: 11px 0 4px;
    }
    .card-summary { font-size: 15px; color: #555555; }
    .card-insight {
      font-size: 15px; color: #4a0016;
      background: var(--insight-bg);
      border-left: 3px solid var(--accent);
      border-radius: 0 6px 6px 0;
      padding: 9px 12px;
    }
    .card-footer {
      display: flex; justify-content: space-between;
      align-items: center; margin-top: 12px;
    }
    .card-date { font-size: 12px; color: var(--muted); }
    .read-link { font-size: 14px; color: var(--accent); text-decoration: none; font-weight: 600; }
    .empty { text-align: center; color: var(--muted); padding: 60px 0; font-size: 15px; }
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <h1>My News Digest</h1>
      <span class="updated">{{ updated }}</span>
    </div>
    <!-- 親タブ (3並列) -->
    <div class="nav-row">
      <div class="seg-group">
        <button class="seg-btn active" data-parent="ai" onclick="showParent('ai')">🤖 AI</button>
        <button class="seg-btn" data-parent="neuro" onclick="showParent('neuro')">🧠 脳科学</button>
        <button class="seg-btn" data-parent="neuro_ai" onclick="showParent('neuro_ai')">⚡ 脳科学×AI</button>
      </div>
    </div>
    <!-- AI 子タブ (第2階層) -->
    <div class="sub-nav-row" id="sub-nav-ai">
      <div class="seg-group">
        <button class="seg-btn active" data-sub="social" onclick="showSub('ai','social')">💼 社会実装</button>
        <button class="seg-btn" data-sub="press" onclick="showSub('ai','press')">📢 プレスリリース</button>
        <button class="seg-btn" data-sub="academic" onclick="showSub('ai','academic')">🎓 学術・研究</button>
      </div>
    </div>
    <!-- 脳科学 子タブ (第2階層) -->
    <div class="sub-nav-row hidden" id="sub-nav-neuro">
      <div class="seg-group">
        <button class="seg-btn active" data-sub="social" onclick="showSub('neuro','social')">💼 社会実装</button>
        <button class="seg-btn" data-sub="press" onclick="showSub('neuro','press')">📢 プレスリリース</button>
        <button class="seg-btn" data-sub="research" onclick="showSub('neuro','research')">🔬 研究・論文</button>
      </div>
    </div>
    <!-- 脳科学 > 研究・論文 の第3階層 -->
    <div class="sub3-nav-row hidden" id="sub3-nav-research">
      <div class="seg-group">
        <button class="seg-btn active" data-sub3="embodiment" onclick="showSub3('embodiment')">🏃 身体性</button>
        <button class="seg-btn" data-sub3="psychology" onclick="showSub3('psychology')">🧑‍🤝‍🧑 心理・組織</button>
      </div>
    </div>
  </header>

  <main>
    <!-- Hot Topics (AI のみ) -->
    {% if hot_articles %}
    <section class="hot-section" id="hot-section">
      <div class="hot-heading">🔥 今、絶対に見るべきHot Topics</div>
      {% for article in hot_articles %}
      {{ render_card(article, 'card-hot') }}
      {% endfor %}
    </section>
    <hr class="section-divider" id="hot-divider">
    {% endif %}

    <!-- 🤖 AI パネル -->
    <div id="panel-ai">
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

    <!-- 🧠 脳科学 パネル -->
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

    <!-- ⚡ 脳科学×AI パネル -->
    <div id="panel-neuro-ai" class="hidden">
      {{ render_panel(neuro_ai['articles']) }}
    </div>
  </main>

  <script>
    let currentParent = 'ai';
    let currentSubAi   = 'social';
    let currentSubNeuro = 'social';
    let currentSub3    = 'embodiment';

    function showParent(tab) {
      currentParent = tab;
      document.querySelectorAll('.nav-row .seg-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.parent === tab));

      // 第2階層の表示切り替え
      document.getElementById('sub-nav-ai').classList.toggle('hidden', tab !== 'ai');
      document.getElementById('sub-nav-neuro').classList.toggle('hidden', tab !== 'neuro');

      // 第3階層は 脳科学 > 研究・論文 のときだけ
      const show3 = tab === 'neuro' && currentSubNeuro === 'research';
      document.getElementById('sub3-nav-research').classList.toggle('hidden', !show3);

      // パネルの表示切り替え
      document.getElementById('panel-ai').classList.toggle('hidden', tab !== 'ai');
      document.getElementById('panel-neuro').classList.toggle('hidden', tab !== 'neuro');
      document.getElementById('panel-neuro-ai').classList.toggle('hidden', tab !== 'neuro_ai');

      // Hot Topics は AI のみ
      const hs = document.getElementById('hot-section');
      const hd = document.getElementById('hot-divider');
      if (hs) hs.classList.toggle('hidden', tab !== 'ai');
      if (hd) hd.classList.toggle('hidden', tab !== 'ai');

      // Observer リフレッシュ
      let targetId;
      if (tab === 'ai') {
        targetId = `sub-panel-ai-${currentSubAi}`;
      } else if (tab === 'neuro') {
        targetId = currentSubNeuro === 'research'
          ? `sub3-panel-${currentSub3}`
          : `sub-panel-neuro-${currentSubNeuro}`;
      } else {
        targetId = 'panel-neuro-ai';
      }
      setTimeout(() => refreshObserver(targetId), 50);
    }

    function showSub(parent, tab) {
      if (parent === 'ai') {
        currentSubAi = tab;
        document.querySelectorAll('#sub-nav-ai .seg-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['social', 'press', 'academic'].forEach(k =>
          document.getElementById(`sub-panel-ai-${k}`).classList.toggle('hidden', k !== tab));
        setTimeout(() => refreshObserver(`sub-panel-ai-${tab}`), 50);

      } else if (parent === 'neuro') {
        currentSubNeuro = tab;
        document.querySelectorAll('#sub-nav-neuro .seg-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.sub === tab));
        ['social', 'press', 'research'].forEach(k =>
          document.getElementById(`sub-panel-neuro-${k}`).classList.toggle('hidden', k !== tab));

        // 第3階層の表示/非表示
        document.getElementById('sub3-nav-research').classList.toggle('hidden', tab !== 'research');

        const targetId = tab === 'research'
          ? `sub3-panel-${currentSub3}`
          : `sub-panel-neuro-${tab}`;
        setTimeout(() => refreshObserver(targetId), 50);
      }
    }

    function showSub3(tab) {
      currentSub3 = tab;
      document.querySelectorAll('#sub3-nav-research .seg-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.sub3 === tab));
      ['embodiment', 'psychology'].forEach(k =>
        document.getElementById(`sub3-panel-${k}`).classList.toggle('hidden', k !== tab));
      setTimeout(() => refreshObserver(`sub3-panel-${tab}`), 50);
    }

    function refreshObserver(panelId) {
      document.querySelectorAll(`#${panelId} .card`).forEach(c => {
        observer.unobserve(c);
        observer.observe(c);
      });
    }

    /* ── Intersection Observer：スクロール中央フォーカス ── */
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry =>
        entry.target.classList.toggle('in-view', entry.intersectionRatio >= 0.45)
      );
    }, {
      threshold: [0, 0.45, 1.0],
      rootMargin: '-12% 0px -12% 0px'
    });
    document.querySelectorAll('.card').forEach(c => observer.observe(c));

    /* ── 未読バッジ管理（localStorage） ── */
    const STORAGE_KEY = 'newsdigest_read_v1';
    function getReadSet() {
      try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')); }
      catch { return new Set(); }
    }
    function markRead(url) {
      const s = getReadSet(); s.add(url);
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...s]));
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


def generate_html(
    processed: dict[str, list[ProcessedArticle]],
    output_path: str = "docs/index.html",
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
    }
    # 全記事を flatten してenrich（記事の category は Gemini が割り当てたキー）
    all_articles: list[dict] = []
    for articles in processed.values():
        for a in articles:
            all_articles.append({**a, "published_jst": _format_published(a["published"])})

    # Pass 1: AIカテゴリの hot 候補を収集
    hot_candidates = [
        a for a in all_articles
        if a.get("hot") and a.get("category") in _HOT_CATS
    ]
    hot_candidates.sort(key=lambda a: a.get("published", ""), reverse=True)
    hot_articles = hot_candidates[:3]
    hot_url_set = {a["url"] for a in hot_articles}

    # Pass 2: Gemini カテゴリキーでパネルに振り分け（hot は除外）
    for a in all_articles:
        if a["url"] in hot_url_set:
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

    # パネルごとに上限10件
    for sub in ("social", "press", "academic"):
        panels["ai"][sub]["articles"] = panels["ai"][sub]["articles"][:10]
    for sub in ("social", "press"):
        panels["neuro"][sub]["articles"] = panels["neuro"][sub]["articles"][:10]
    panels["neuro"]["research"]["embodiment"]["articles"] = panels["neuro"]["research"]["embodiment"]["articles"][:10]
    panels["neuro"]["research"]["psychology"]["articles"] = panels["neuro"]["research"]["psychology"]["articles"][:10]
    panels["neuro_ai"]["articles"] = panels["neuro_ai"]["articles"][:10]

    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(HTML_TEMPLATE)
    html = tmpl.render(
        updated=updated_str,
        hot_articles=hot_articles,
        ai=panels["ai"],
        neuro=panels["neuro"],
        neuro_ai=panels["neuro_ai"],
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[generate] Written to {output_path}")
