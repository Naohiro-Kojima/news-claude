"""Anthropic Claude API を使って記事の精査・翻訳・要約・インサイト生成を行う"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import TypedDict

import anthropic

from fetch import Article

SCREENING_MODEL    = "claude-haiku-4-5-20251001"
ANALYSIS_MODEL     = "claude-sonnet-4-6"
MAX_SCREEN_BATCH   = 10   # スクリーニングは多めに一括処理
MAX_ANALYSIS_BATCH = 3    # 全文含むため小バッチ
FETCH_WORKERS      = 4    # 並行フェッチのスレッド数
INTER_BATCH_SLEEP  = 1
MAX_RETRIES        = 4
RETRY_BASE_WAIT    = 30

JST = timezone(timedelta(hours=9))

SCREENING_PROMPT = """あなたはLM（リンクアンドモチベーション）の情報スクリーニング担当です。
記事のタイトルと説明文（抜粋）のみを読み、掲載可否・分類・熱量のみを判定してください。
要約・インサイトの生成は不要です。

【selected=true: 掲載候補】
AI / 脳科学・神経科学 / HR・組織開発・人材 / 競合他社動向 の分野で、
LMの3市場（事業市場・労働市場・資本市場）に関連する重要な情報。

【selected=false: 除外】
単純なイベント告知・採用求人情報・既報の焼き直し・LMの事業と接点のない純粋技術報告。

【hot=true】selected=true全体の20〜30%以下。市場を根底から再編するレベルの発見・発表のみ。

【カテゴリキー（9種のいずれか）】
ai_product / ai_business / neuro_social / neuro_press /
neuro_embodiment / neuro_psychology / neuro_ai / competitor_press / industry_trend

━━ 出力形式 ━━
JSON配列のみ。コードブロック・説明文は不要。selected=falseの記事も必ず配列に含めること。
[
  {"index": 0, "selected": true,  "hot": false, "category": "ai_product",  "title_ja": "日本語タイトル"},
  {"index": 1, "selected": false, "hot": false, "category": "ai_business", "title_ja": "日本語タイトル"}
]"""

DEEP_ANALYSIS_PROMPT = """あなたは、リンクアンドモチベーション（LM）の思想・技術・事業・組織を完全掌握した、戦略的インテリジェンス・アナリストです。
提供されたLMの全知識体系を「解析のレンズ」とし、記事の具体的エビデンスに基づいた鋭いインサイトを生成してください。

━━ LMとは：意味のあふれる社会の設計者 ━━
・アイデンティティ: 2000年創業。世界初の「モチベーション」にフォーカスした経営コンサルティング会社。
・ミッション: モチベーションエンジニアリング（ME）によって組織と個人に変革の機会を提供し、「意味のあふれる社会」を実現する。

━━ 3つのDivisionとグループ会社（事業体制の網羅） ━━
1.【組織開発Division】：個人から選ばれる組織（モチベーションカンパニー）創り
　- コンサル・クラウド：(株)リンクアンドモチベーション（診断・変革支援）、海外子会社（アジア5ヶ国）、Unipos(株)（ピアボーナスによるカルチャー変革支援）。
　- IR支援：(株)リンクソシュール（IR・SR・コーポレートブランディング）、ジャパンストラテジックファイナンス(株)（投資家向け動画・ライブ配信）、イー・アソシエイツ(株)（株主総会DX）。
2.【個人開発Division】：組織から選ばれる個人（アイカンパニー）創り
　- キャリアスクール：(株)リンクアカデミー（IT・語学・資格等のスキル開発・AVIVA/大栄運営）。
　- 学習塾：(株)モチベーションアカデミア（小中高向け学力向上と社会人スキル獲得支援）。
3.【マッチングDivision】：組織と個人のフィッティング機会の提供
　- ALT配置：(株)リンク・インタラック（民間シェア48.8%）。
　- 人材紹介：オープンワーク(株)（プラットフォーム「OpenWork」運営）、(株)リンク・アイ（大学生向け性格考慮マッチング）。
4.【その他】
   -(株)リンクダイニング（ステークホルダーの交流拠点）。
   - (一社)エッジソン・マネジメント協会: 次世代リーダー「エッジソン」の育成。

━━ 核となる技術：モチベーションエンジニアリング（ME） ━━

MEは経営学・社会システム論・行動経済学・心理学を融合し、組織に「実効性」と「再現性」をもたらす基幹技術である。
・診断技術 (Diagnosis): 期待度×満足度の2軸で組織状態を可視化。延べ13,930社、629万人のデータベースに基づく11段階レーティングで優先課題を特定する。
・変革技術 (Transformation): 「組織X」へ導く実践的手法。PCマトリクスや4Cによる経営統合、および「態度変容の3ステップ」を適用する。
　- 1. Unfreeze（解凍）：現状の課題を可視化し、変化への準備を行う。
　- 2. Change（変化）：新しい行動や仕組みを実装し、変化を促す。
　- 3. Refreeze（再凍結）：変化した状態を組織全体に定着・習慣化させる。
・WILLとwillの同期: 会社が目向かう先（WILL）と個人の実現したいこと（will）を接続し、エンゲージメントを最大化させる 。


━━ 期待度と満足度の4象限マトリクス（診断技術） ━━

このマトリクスは、従業員が会社に対して何をどの程度求めているか（期待度）と、それに対してどの程度満たされているか（満足度）を可視化するものです。組織の現状を正確に把握し、優先的に解決すべき課題を特定するために用いられます 。
1. INTER LINK（高期待・高満足）：強み維持領域状態: 社員の期待が高く、かつ満足度も高い領域です 。意味: 組織の競争優位の源泉であり、今後も維持・伸長させていくべき要素です 。
2. ICE BLOCK（高期待・低満足）：最優先課題領域状態: 社員が強く求めているにもかかわらず、満足度が低い領域です 。意味: 組織において最も緊急かつ重要に改善が必要なボトルネックです 。
3. INK BLOT（低期待・低満足）：現状維持・観察領域状態: 社員の関心が低く、満足度も低い領域です 。意味: 直ちに手を打つ優先順位は低いですが、なぜ期待されていないのかという要因分析の対象となります 。
4. IDLE LINE（低期待・高満足）：過剰投資・見直し領域状態: 社員があまり求めていないのに対し、提供されている満足度が高い領域です 。意味: リソースの配分を見直し、他の優先領域へ再配分することを検討すべきポイントです 。

━━ 解析の基軸：組織Xを実現するフレームワーク ━━

【理念の核心：MVVの構造】
・Mission（経営理念）: 経営の目的そのもの。
・Vision（経営指針）: 迷った時の「会社の判断基準」となる目指す姿。
・Value（行動指針）: 日々の仕事における「個人の行動基準」。

【経営の基軸：4P】
・Philosophy (理念策定): MVVの言語化と浸透。
・Positioning (戦略策定): 差別化、ターゲット(Who)、提供価値(What)の定義。
・Performance (業績向上): 目標の数値化、PDCA、プロセスの標準化。
・People (人財開発): 理念採用、フィードバック、キャリアと方向性のリンク。

【組織を接合する：4C】
各４Pの象限の「分断」を解消し、組織を一気通貫させるための結節点（接合技術）。
・Corporate-identity: 理念（Philosophy）と戦略（Positioning）を接合。「自分たちは何屋か」という本質的なアイデンティティの確立。
・Center-pin: 戦略（Positioning）と成果（Performance）を接合。経営目標と現場のKPI設計・共有による連動。
・Confidence: 業績（Performance）と人材（People）を接合。部署間の壁の打破、本音の信頼関係、心理的安全性の構築。
・Commit: 人材（People）と理念（Philosophy）を接合。会社と個人の意思（Will）を統合し、目標を自分事化させる。

【組織の状態：業務基準と心理的安全性のマトリクス】
・自律組織（ツヨい）: 高い業務基準 × 高い心理的安全性（理想）。
・軍隊組織（キツい）: 高い業務基準 × 低い心理的安全性。
・友達組織（ヌルい）: 低い業務基準 × 高い心理的安全性。
・形骸組織（ヨワい）: 低い業務基準 × 低い心理的安全性。

【独自コンセプト・価値公式】
・限定合理的な感情人: 人は勘定（論理）だけでなく感情で動く。
・協働システム: 組織は関係性の網の目。問題は「人」ではなく「間」にある。
・時価総額 = EPS（実績） × PER（期待値）。非財務資本（人的資本）の開示により投資家からの期待（PER）をマネジメントする。
・アイカンパニー: 個人を「自分株式会社」の経営者と見立てる自律的キャリア観。
・エッジソン: 志（高さ）× 5能力（底面積）を持つ次世代リーダー。

━━ 解析ステップ ━━

1.【summary】: 採用軍師や経営幹部が30秒で構造を把握できるよう、contentから以下の「具体的数値・事実」を必ず含めて3〜4文で記述せよ。
・脳科学: サンプルサイズ、効果量、測定手法（fMRI/EEG等）の具体的仕様。
・AI/事業: 導入規模、ROI数値、価格体系、投資戦略等の客観的事実。

2.【insight】: 記事カテゴリに応じ、以下の指針で2〜3文の鋭いインサイトを記述せよ。

【AI系 (ai_product / ai_business)】
LMの視点に縛られず、実務家・技術アナリストの視点で記述せよ。
・技術の本質：その技術が実務（現場の作業や意思決定）をどう具体的に変えるか。
・将来予測：今後、この技術が市場でどのように跳ねるか、あるいは既存ツールをどう淘汰するか。
・活用への示唆：ビジネスパーソンが明日からこの技術をどう実戦投入すべきか。

【脳科学系 (neuro_*)】
固有のフレームワーク（ME等）を排除し、純粋な科学・社会インパクトの視点で記述せよ。
・科学的意義：その神経・心理メカニズムが、人間理解においてどのようなブレイクスルーをもたらしたか。
・技術の発展と未来：この知見が将来的にどのようなサービスに結びつき、我々の生活や働き方をどう変えるか。

【競合/業界系 (competitor_press / industry_trend)】
LMへのこじつけを避け、業界全体の地殻変動や市場構造の変化に主眼を置いて記述せよ。
・市場への波及：他社のこの動きが、今後の業界標準やプレイヤーの立ち位置にどう影響するか。
・戦略的示唆：他社が狙う市場の隙間や、この動向が業界のEPSやPERにどう作用するか。
・LM視点（条件付き）：記事の内容がLMの事業領域（組織開発・モチベーション・人的資本・採用・HRテック等）と明確に接点を持つ場合に限り、その接点を1文で補足せよ。接点が薄い・なければ省略すること。

3.【impact_axes】: 以下の3軸を1.0〜5.0（0.1刻み）で厳格に評価せよ。
・per (PER & Social Impact): 大手ベンダーの重大発表や、社会・市場の期待値（PER）を押し上げる重大なニュースか。
・sci (Utility & Curiosity): 実務に使えるTips（Qiita/note等）としての有用性、あるいは知的好奇心を強烈に刺激する内容か。
・cps (Transformative Potential): 既存の組織運営、ビジネスモデル、あるいは人間の働き方を根本から変えるポテンシャルがあるか。

4.【impact】: (per + sci + cps) / 3 の単純平均（小数点第1位まで）。

5.【hashtags】: 記事テーマを端的に表す日本語ハッシュタグを3つ生成。

━━ 鉄則 ━━
・出力に引用番号（cite等）は一切含めないこと。
・出力は以下のJSON配列形式のみを厳守し、説明文やコードブロック（```等）は一切不要。

[
  {
    "index": 0,
    "summary": "...",
    "insight": "...",
    "impact": 0.0,
    "impact_axes": {"per": 0.0, "sci": 0.0, "cps": 0.0},
    "hashtags": ["#タグ1", "#タグ2", "#タグ3"]
  }
]
"""


# ── 後方互換 ─────────────────────────────────────────────────────────────────
# 旧コードが SYSTEM_PROMPT を参照している場合のエイリアス
SYSTEM_PROMPT = DEEP_ANALYSIS_PROMPT  # noqa: F841 (validate_prompt.py 等から参照される)




_AI_VENDOR_DOMAINS: frozenset[str] = frozenset({
    "openai.com", "anthropic.com", "deepmind.google", "blog.google",
    "ai.google", "labs.google", "microsoft.com", "huggingface.co",
    "meta.ai", "about.fb.com", "stability.ai", "mistral.ai", "cohere.com", "x.ai",
})


def _is_ai_vendor_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lstrip("www.")
    return any(host == d or host.endswith("." + d) for d in _AI_VENDOR_DOMAINS)


VALID_GEMINI_CATS: frozenset[str] = frozenset({
    "ai_product", "ai_business",
    "neuro_social", "neuro_press", "neuro_embodiment", "neuro_psychology", "neuro_ai",
    "competitor_press", "industry_trend",
})

# feeds.yml のカテゴリ名 → Gemini カテゴリキー
_FEED_TO_GEMINI_CAT: dict[str, str] = {
    # 新カテゴリ
    "AIプロダクト速報":      "ai_product",
    "AIビジネス活用事例":    "ai_business",
    "脳科学・社会実装":      "neuro_social",
    "脳科学・プレスリリース": "neuro_press",
    "脳科学研究・身体性":    "neuro_embodiment",
    "脳科学研究・心理認知":  "neuro_psychology",
    "競合プレスリリース":    "competitor_press",
    "業界・市場トレンド":    "industry_trend",
    # 旧カテゴリ（既存キャッシュとの互換）
    "AI社会実装・ビジネス":      "ai_business",
    "AIメーカー・プレスリリース": "ai_product",
    "AI学術研究・最新論文":      "ai_business",
    "脳科学×AI":                "neuro_ai",
    "脳科学研究・心理組織":      "neuro_psychology",
    "組織人事・社会実装":        "industry_trend",
    "組織人事・プレスリリース":  "competitor_press",
    "組織人事・研究論文":        "industry_trend",
}


class ProcessedArticle(TypedDict):
    title_ja: str
    url: str
    summary: str
    insight: str
    published: str
    category: str
    hot: bool
    source: str          # 取得元ドメイン表示名（例: "TechCrunch"）
    impact: float        # 0.0〜5.0（0.1刻み）—— 3軸の単純平均
    impact_axes: dict    # {"per": float, "sci": float, "cps": float}
    hashtags: list       # 日本語ハッシュタグ 3件
    competitor_group: str  # 競合グループ識別子（feeds.yml の group 値）


# ── ダミーデータ ──────────────────────────────────────────────────────────────

_DUMMY_TEMPLATES: dict[str, list[tuple[str, str, str, bool]]] = {
    "AI社会実装・ビジネス": [
        (
            "大手金融機関がAIを活用した与信審査システムを本格導入",
            "メガバンク3行がClaudeベースの与信審査AIを2026年度内に全店舗展開すると発表した。"
            "従来の審査時間を平均72時間から4時間に短縮し、小規模事業者への融資承認率が18%向上する見込みだ。",
            "AIによる金融インフラの刷新は、融資が困難だったロングテール層へのアクセスを劇的に改善する。"
            "銀行員の業務が審査から顧客提案にシフトし、金融機関のビジネスモデル自体が再定義されるだろう。",
            True,
        ),
        (
            "製造業向けAI品質検査SaaSが累計導入500社を突破",
            "工場向け画像認識AIのスタートアップが導入実績500社を発表し、シリーズCで120億円を調達した。"
            "不良品検出精度は99.7%に達し、人手による目視検査との比較でコストを60%削減できるという。",
            "製造DXにおけるAI活用は、品質管理から予知保全・生産最適化へと応用範囲が急拡大している。"
            "中小製造業がSaaS型で高精度AIを利用できる環境が整い、産業構造の底上げにつながるだろう。",
            False,
        ),
        (
            "EU AI法の施行細則が確定——企業が対応すべき主要ポイント",
            "欧州委員会がEU AI法の施行細則を正式公表し、高リスクAIシステムに関する要件が明確化された。"
            "医療・採用・信用評価などの用途では第三者監査と透明性レポートの提出が義務付けられる。",
            "EU AI法は事実上のグローバルスタンダードになりつつあり、EU外の企業も対応を迫られる。"
            "コンプライアンスコストが大企業優位の構造を生む可能性があり、規制設計の国際調和が急務だ。",
            False,
        ),
    ],
    "AIメーカー・プレスリリース": [
        (
            "Anthropicが次世代Claudeを正式発表——長期記憶と高度な推論機能を搭載",
            "Anthropicは最新Claudeの一般公開を発表し、最大100万トークンの長期記憶管理機能を搭載したと明らかにした。"
            "APIは従来比40%のコスト削減を実現し、エンタープライズ向けプランでは専用インスタンスも提供される。",
            "最新Claudeの投入はエージェント型AIの実用化を加速し、知識労働の自動化が新たな段階に入ることを意味する。"
            "競合他社への圧力が高まり、モデル性能・コスト・安全性をめぐる競争がさらに激化するだろう。",
            True,
        ),
        (
            "Google DeepMindがタンパク質設計AIの最新版を公開",
            "Google DeepMindは創薬研究者向けに最新版AIを公開し、抗体・核酸との相互作用予測精度を大幅に向上させた。"
            "新機能として動的構造変化のシミュレーションが追加され、創薬ターゲット探索の効率化が期待される。",
            "AIによるタンパク質科学の進化は、創薬コストと期間を桁違いに圧縮する可能性を秘めている。"
            "製薬大手とAI企業のパートナーシップが加速し、バイオテック産業のパラダイムシフトが進むだろう。",
            False,
        ),
        (
            "HuggingFaceがオープンソースLLMの新ベンチマーク「OpenEval」を公開",
            "HuggingFaceは商用・非商用を問わず公平に評価できるLLMベンチマーク「OpenEval」を発表した。"
            "従来のベンチマークへの依存から脱却し、実業務シナリオに近いタスクセットで評価する設計が特徴だ。",
            "ベンチマークの多様化はAI開発の評価指標を民主化し、特定企業によるランキング操作を抑制する効果がある。"
            "オープンモデルの信頼性向上がエンタープライズ採用を後押しし、クローズドモデル依存からの脱却が進むだろう。",
            False,
        ),
    ],
    "AI学術研究・最新論文": [
        (
            "推論時の計算資源を動的配分してLLM精度を向上させる新手法",
            "スタンフォード大学とMITの共同研究が、推論時の計算資源を動的に割り当てることでLLMの精度を向上させる手法を発表した。"
            "数学・コーディング・論理推論タスクで従来比15〜30%の改善を確認。モデルの再学習なしに適用できる。",
            "推論フェーズの効率化はAIのコストパフォーマンスを根本から変える可能性がある。"
            "訓練コスト競争から推論設計競争へとAI開発の主戦場がシフトするかもしれない。",
            True,
        ),
        (
            "大規模言語モデルにおける幻覚生成の内部表現パターンを特定",
            "MIT・東大共同チームがLLMの幻覚生成時にactivateされる内部表現パターンを解析手法で特定した。"
            "「確信度と正確性の乖離」が特定のAttentionヘッドに集中することが判明し、事前検出の可能性が示された。",
            "幻覚のメカニズム解明は信頼性の高いAIシステム設計に直結する重要な基礎研究だ。"
            "将来的には幻覚を事前に検知・修正する機構をモデルに組み込めるようになるかもしれない。",
            False,
        ),
        (
            "拡散モデルによる3Dタンパク質構造生成の精度がAlphaFoldに迫る水準に到達",
            "カーネギーメロン大学が発表した拡散モデルベースの構造予測システムが、"
            "CASP15ベンチマークでAlphaFold 3と僅差の精度を達成したと報告された。生成速度はAlphaFoldの10倍以上。",
            "複数の競合手法がAlphaFoldに迫ることで、タンパク質構造予測はコモディティ化しつつある。"
            "次の競争軸は動的な構造変化や相互作用予測へと移行するだろう。",
            False,
        ),
    ],
    "脳科学・社会実装": [
        (
            "心理的安全性の高い組織ほどイノベーション創出率が2.5倍——NIH大規模調査",
            "NIHが企業・官公庁を横断した4,200名規模の調査を実施し、心理的安全性スコアが上位25%の組織は"
            "下位25%と比較してイノベーション創出件数が2.5倍、離職率が38%低いことが明らかになった。",
            "心理的安全性は採用・育成コストの削減と創造性向上を同時に達成する組織設計の要だ。"
            "「失敗を共有できる文化」が最も強い予測因子であることが判明した点は、採用や評価制度設計のヒントになる。",
            True,
        ),
        (
            "マインドフルネス研修8週間で従業員エンゲージメントが平均18%向上",
            "大手製造業3社が導入したマインドフルネス研修プログラムの効果検証で、参加者のエンゲージメントスコアが"
            "平均18%向上し、欠勤率が12%低下したことが報告された。",
            "科学的根拠に基づく人材育成施策は、感情的投資対効果の可視化を可能にし経営層への説得力が増す。"
            "ウェルビーイング施策が人材マネジメントの主流に移行するペースが加速するだろう。",
            False,
        ),
        (
            "内発的動機づけが高い社員は創造的問題解決能力が3倍——fMRI研究",
            "慶應義塾大学と産業技術総合研究所の共同研究で、内発的動機づけの高い被験者は"
            "外発的報酬依存型に比べ、前帯状皮質の活性が顕著に高く、創造的タスク遂行速度も3倍であった。",
            "金銭インセンティブ偏重の報酬設計が創造性を阻害するメカニズムが脳科学的に実証された。"
            "目的・自律・成長の3要素を組み込んだ設計への移行が組織競争力の鍵となるだろう。",
            False,
        ),
    ],
    "脳科学・プレスリリース": [
        (
            "ハーバード大が認知症早期発見バイオマーカーを発表——発症10年前から検出可能",
            "ハーバード医科大学の研究チームが、血液検査で認知症発症の10年前から異常を検出できる新バイオマーカーを発表した。"
            "感度94%・特異度89%を達成し、既存の脳脊髄液検査に比べ侵襲性が大幅に低い。",
            "認知症の超早期発見は、介護費用削減と就労継続の両面で社会インパクトが大きい。"
            "予防医療と職場健康管理の連携が新たなビジネス機会を生み出す可能性がある。",
            True,
        ),
        (
            "理研が感情の脳内マッピングを高精度化——7種の感情状態をリアルタイム判別",
            "理化学研究所が開発した機械学習モデルが、fMRIデータから喜び・怒り・恐怖など7種の感情を"
            "リアルタイムかつ84%の精度で判別することに成功した。非侵襲的ウェアラブルへの応用研究も開始。",
            "感情状態のリアルタイム計測が可能になると、会議中のエンゲージメント把握やメンタルヘルス管理に革命をもたらす。"
            "この技術が「脳の表情」を読む新しいコミュニケーションの形を生み出すかもしれない。",
            False,
        ),
        (
            "欧州脳研究コンソーシアムが2035年神経科学ロードマップを公表",
            "Human Brain Project後継となる欧州神経科学コンソーシアムが2035年を見据えたロードマップを公表し、"
            "「デジタルツイン脳」の構築と神経疾患治療への応用を最重要目標に掲げた。",
            "脳のデジタルツインは創薬・教育・メンタルヘルスケアの設計を根本から変える可能性を持つ。"
            "日本企業にとっても国際共同研究への参画機会が広がる局面だ。",
            False,
        ),
    ],
    "脳科学研究・身体性": [
        (
            "歩きながら学ぶと記憶定着率が40%向上——運動と認知の神経回路を解明",
            "スタンフォード大学の研究が、軽度の有酸素運動（歩行）と同時に行う学習が"
            "海馬のBDNF分泌を促進し、記憶定着率を座学比で40%向上させることを神経科学的に実証した。",
            "「体を動かしながら学ぶ」設計は、研修効果と従業員の身体的健康を同時に高める投資対効果の高い手法だ。"
            "スタンディングデスクやウォーキングミーティングの導入根拠として即使えるエビデンスになる。",
            True,
        ),
        (
            "腸内マイクロバイオームが意思決定の質に影響——腸脳相関の新メカニズム",
            "腸内細菌叢の多様性が低い被験者群は、リスク判断タスクにおいて前頭前皮質の活性が23%低く"
            "衝動的意思決定が増加することが脳スキャンで確認された。",
            "食事と認知パフォーマンスの直接的関連が明らかになり、職場の食環境設計が戦略的意義を持つ。"
            "「腸活」が単なる健康トレンドでなく、意思決定の質を左右するという話題として会話で使える。",
            False,
        ),
        (
            "睡眠中の体温低下が記憶固定を促進——深部体温と海馬リプレイの相関",
            "深部体温が就寝後2時間で0.5℃以上低下した被験者は、翌朝の記憶テストで平均31%高いスコアを示した。"
            "海馬のシャープウェーブリプル発生頻度が体温低下と正の相関を持つことが確認された。",
            "最適な睡眠環境の設計（室温・湿度・寝具）が学習効率と業務パフォーマンスに直結する可能性がある。"
            "「なぜ涼しい部屋で寝ると頭がよくなるのか」という切り口で雑談でも刺さる知見だ。",
            False,
        ),
    ],
    "脳科学研究・心理組織": [
        (
            "心理的安全性と前頭前皮質の関係——「恐れのない職場」の神経科学的根拠",
            "MITスローンとハーバードビジネススクールの共同研究が、高い心理的安全性を感じている組織員は"
            "プレゼン・発言時の扁桃体活性が対照群比で42%低く、前頭前皮質が活性化することを確認した。",
            "「心理的安全性」が単なる感情論ではなく脳機能レベルでパフォーマンスに影響することが実証された。"
            "組織設計や1on1の設計根拠として、神経科学的エビデンスを提示できる場面で強い説得力になる。",
            True,
        ),
        (
            "マインドフルネス瞑想8週間で扁桃体の体積が縮小——ストレス耐性の構造変化",
            "8週間のマインドフルネス介入を受けた被験者120名のfMRI解析で、扁桃体のストレス反応が平均23%低下し"
            "灰白質体積の有意な縮小が観察された。効果は介入終了6ヶ月後も持続していた。",
            "瞑想の神経科学的エビデンスが蓄積されることで、メンタルヘルス治療の選択肢が広がる。"
            "「脳の形が変わる」という事実は、習慣化の重要性を語るときの強力なフックになる。",
            False,
        ),
        (
            "認知的柔軟性が高い社員ほどAIと協働した問題解決能力が3倍——組織研究",
            "オックスフォード大学が社会人500名を対象に行った研究で、認知的柔軟性スコアが上位25%の社員は"
            "AIツール活用時の生産性が下位25%比で3倍高く、新しい問題への適応速度も顕著に速かった。",
            "AI時代の人材育成は認知的柔軟性の向上を中核に据えるべき時代に入った。"
            "採用基準や育成プログラムの設計に「AIと協働できる認知特性」という視点を入れるべきタイミングだ。",
            False,
        ),
    ],
    "脳科学×AI": [
        (
            "脳型ニューラルネットワークが従来比7倍のエネルギー効率を達成——ニューロモーフィックAIの実用化",
            "IntelとIBMの共同研究チームが、ヒト脳のスパイキングニューロンを模倣したチップ上で"
            "従来のGPUベースDNNと同等精度を達成しながらエネルギー消費を7分の1に抑えることに成功した。",
            "ニューロモーフィックコンピューティングの実用化はAIの民主化とグリーン化を同時に推進する。"
            "脳科学とAI工学の融合が次のハードウェア革命の中心軸になりつつあるというトレンドの証拠になる。",
            True,
        ),
        (
            "AIによる感情認識が組織の離職リスクを82%の精度で予測——HR技術への応用",
            "顔表情・音声・テキストを融合したマルチモーダルAIが、面談データから6ヶ月以内の離職リスクを"
            "82%の精度で予測することに成功した研究が発表された。",
            "AI×脳科学の知見を組み合わせたHRテクノロジーは、人材損失コストの大幅削減を可能にする。"
            "倫理的活用ガイドラインの整備が急務であり、先行企業が競争優位を確立する局面だ。",
            False,
        ),
        (
            "大規模言語モデルと脳波データの統合でうつ病の早期診断精度が91%に到達",
            "慶應大学医学部とAnthropicの共同研究が、会話AIと脳波（EEG）データを組み合わせた"
            "うつ病早期診断システムを開発し、精神科医と同等の91%診断精度を達成した。",
            "メンタルヘルスケアへのAI×神経科学の応用は、精神科医不足問題の解決と早期介入を実現する。"
            "企業のEAP（従業員支援プログラム）との統合が次のイノベーション領域になるだろう。",
            False,
        ),
    ],
    "組織人事・社会実装": [
        (
            "心理的安全性が高いチームの生産性は低いチームの1.9倍——Google Project Aristotle追跡調査",
            "Googleが「Project Aristotle」の10年追跡データを公開し、心理的安全性スコア上位25%のチームは"
            "下位25%比で生産性1.9倍・自発的な改善提案数3.2倍であることが明らかになった。",
            "心理的安全性の定量的な生産性効果が改めて実証され、経営層への投資対効果説明が容易になる。"
            "1on1設計・フィードバック文化・評価制度の見直しに即活用できるエビデンスだ。",
            True,
        ),
        (
            "リモートワーク導入3年後の組織力調査——エンゲージメントと昇進機会に格差",
            "大手コンサルが国内企業300社を対象に実施したリモートワーク定着調査によると、"
            "フル出社と比べてリモート主体社員のエンゲージメントは8%低く、昇進確率は17%低い結果が示された。",
            "「見えない不利益」の存在が数値化されたことで、ハイブリッド勤務制度の公平設計が急務になった。"
            "出社頻度と評価の分離、非同期コミュニケーションの設計強化が組織の喫緊の課題だ。",
            False,
        ),
        (
            "1on1ミーティングの頻度と従業員定着率の相関——週1回が最も効果的",
            "HR SaaSのデータ分析部門が20万人規模の行動データを解析した結果、"
            "週1回の1on1を継続した社員は離職率が31%低く、目標達成率も22%高いことが確認された。",
            "1on1の頻度と質が定着率・パフォーマンスに直結することがスケールデータで裏付けられた。"
            "マネジャー育成プログラムの中心に1on1設計を置くことの経営的根拠が強化されたといえる。",
            False,
        ),
    ],
    "組織人事・プレスリリース": [
        (
            "WorkdayがAIエージェントによる人事業務自動化プラットフォームを発表",
            "WorkdayはAIエージェントを活用して採用・評価・異動・給与計算を横断的に自動化する"
            "新プラットフォーム「Workday Illuminate」を発表。HRBPの意思決定支援を中核機能とする。",
            "HRオペレーションの自動化が加速し、HRBP・HRBOの役割が戦略・判断に特化していく転換点だ。"
            "国内HRSaaS市場でも同様の機能競争が激化し、HR担当者には新しいAIリテラシーが求められる。",
            True,
        ),
        (
            "リクルートが採用候補者のスキルベースマッチングAIをリリース",
            "リクルートグループが学歴・職歴ではなくスキルデータを軸にした採用マッチングAIを正式リリース。"
            "履歴書フリー採用を実現するAPIとして国内100社以上が導入を予定している。",
            "スキルベース採用への移行は学歴フィルタを解体し、潜在的な人材プールを大幅に拡大する。"
            "採用担当者はスキル要件の定義力と評価ノウハウの習得が競争優位の鍵になる。",
            False,
        ),
        (
            "Mercer、グローバル報酬ベンチマーク2026年版を公開——日本の給与水準に警鐘",
            "Mercerの年次グローバル報酬調査によると、日本のIT・データサイエンス職の給与は"
            "韓国比で平均23%低く、シンガポール比では41%低いことが明らかになった。",
            "国際的な人材獲得競争において日本企業の給与水準の低さが構造的リスクになりつつある。"
            "報酬設計の見直しは単なるコスト論でなく人材戦略の優先課題として経営アジェンダ化が必要だ。",
            False,
        ),
    ],
    "組織人事・研究論文": [
        (
            "多様性スコアが高い取締役会は長期ROEが1.4倍——S&P500企業10年分析",
            "ハーバードビジネススクールがS&P500企業の取締役会構成と財務指標の10年データを分析した結果、"
            "ジェンダー・専門性・国籍の多様性指数が上位30%の取締役会は下位30%比でROEが1.4倍高かった。",
            "経営多様性と財務パフォーマンスの因果関係がスケールデータで証明されたことは投資家・経営者双方に影響する。"
            "DEI施策をコストでなくリターンとして語る経営言語への転換を後押しするエビデンスになる。",
            True,
        ),
        (
            "フィードバックの即時性と学習定着率の相関——ラーニングアジリティ研究",
            "スタンフォード大学教育研究所が実施した実験研究で、行動から24時間以内のフィードバックは"
            "1週間後のフィードバックに比べてスキル定着率が2.3倍高いことが確認された。",
            "OJTや研修設計において「フィードバックの即時化」が最も費用対効果の高い介入であることが証明された。"
            "マネジャー評価への組み込みや、AIによるリアルタイムコーチング普及の理論的根拠となる。",
            False,
        ),
        (
            "内発的動機づけモデルの再検証——自己決定理論の組織応用メタ分析",
            "Journal of Applied Psychologyに掲載されたメタ分析（N=142,000）で、自律性・有能感・関係性の"
            "3要素が揃った職場環境は、報酬水準が同等でも離職意図が38%低いことが確認された。",
            "自己決定理論の実証的有効性がスケールデータで再確認され、組織設計の理論的根拠が強固になった。"
            "給与以外の動機づけ要因を制度化する際の説得材料として即座に活用できるエビデンスだ。",
            False,
        ),
    ],
}

_DUMMY_HASHTAGS: dict[str, list[str]] = {
    # 新カテゴリ
    "ai_product":        ["#生成AI", "#LLM", "#AIサービス"],
    "ai_business":       ["#AI実装", "#業務自動化", "#DX"],
    "neuro_social":      ["#脳科学", "#組織設計", "#ウェルビーイング"],
    "neuro_press":       ["#神経科学", "#脳研究", "#ニューロテック"],
    "neuro_embodiment":  ["#身体性", "#具身認知", "#VR"],
    "neuro_psychology":  ["#認知科学", "#心理学", "#認知バイアス"],
    "neuro_ai":          ["#BCI", "#ニューロモーフィック", "#脳×AI"],
    "competitor_press":  ["#競合動向", "#HRテック", "#業界リサーチ"],
    "industry_trend":    ["#HR市場", "#人材トレンド", "#組織変革"],
    # 旧カテゴリ互換
    "ai_social":         ["#AI実装", "#業務自動化", "#DX"],
    "ai_press":          ["#生成AI", "#LLM", "#AIサービス"],
    "ai_academic":       ["#機械学習", "#深層学習", "#AI研究"],
    "hr_social":         ["#組織開発", "#人材育成", "#HR実践"],
    "hr_press":          ["#HRテック", "#HRツール", "#採用DX"],
    "hr_academic":       ["#組織行動", "#人的資本", "#エンゲージメント"],
}

_DUMMY_DEFAULT: list[tuple[str, str, str, bool]] = [
    (
        "注目の最新研究が発表される（ダミー）",
        "これはdry-run用のサンプル要約です。実際の記事では、ここに3〜4文の日本語要約が入ります。",
        "これはdry-run用のサンプルインサイトです。今後のトレンドへの影響が2〜3文で記述されます。",
        True,
    ),
    (
        "業界に大きな影響を与えるニュース（ダミー）",
        "2件目のサンプル要約です。カードのレイアウトや文字の折り返し、余白などを確認するために使用します。",
        "2件目のサンプルインサイトです。背景色や枠線が正しく表示されているか確認してください。",
        False,
    ),
    (
        "重要な論文・製品発表が報告される（ダミー）",
        "3件目のサンプル要約です。タブ切り替えや複数カテゴリの表示が正常に動作するか確認できます。",
        "3件目のサンプルインサイトです。モバイル表示でもレイアウトが崩れないか確認してください。",
        False,
    ),
]


def _dummy_articles(category_name: str) -> list[ProcessedArticle]:
    """カテゴリ名に対応した3件のダミー記事を返す"""
    now = datetime.now(JST).isoformat()
    templates = _DUMMY_TEMPLATES.get(category_name, _DUMMY_DEFAULT)
    slug = category_name.replace(" ", "-").replace("・", "-")
    gemini_cat = _FEED_TO_GEMINI_CAT.get(category_name, "ai_product")
    dummy_tags = _DUMMY_HASHTAGS.get(gemini_cat, ["#AI", "#研究", "#トレンド"])
    return [
        ProcessedArticle(
            title_ja=f"[DRY-RUN] {title}",
            url=f"https://example.com/{slug}/{i}",
            summary=summary,
            insight=insight,
            published=now,
            category=gemini_cat,
            hot=hot,
            source="Example",
            impact=4.5 if hot else 3.0,
            impact_axes={"per": 4.5, "sci": 4.5, "cps": 4.5} if hot else {"per": 3.0, "sci": 3.0, "cps": 3.0},
            hashtags=dummy_tags,
            competitor_group="",
        )
        for i, (title, summary, insight, hot) in enumerate(templates)
    ]


# ── キャッシュ ──────────────────────────────────────────────────────────────

def _daily_cache_path(base_dir: str) -> str:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    return os.path.join(base_dir, f"processed_{today}.json")


def _url_cache_path(base_dir: str) -> str:
    return os.path.join(base_dir, "cache.json")


def load_cache(base_dir: str) -> dict[str, list[ProcessedArticle]] | None:
    path = _daily_cache_path(base_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    print(f"[process] キャッシュを使用: {path}")
    return data


def save_cache(base_dir: str, result: dict[str, list[ProcessedArticle]]) -> None:
    os.makedirs(base_dir, exist_ok=True)
    path = _daily_cache_path(base_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[process] キャッシュ保存: {path}")


def load_url_cache(base_dir: str) -> dict[str, ProcessedArticle]:
    """URL単位の永続キャッシュを読み込む（既知記事のAPI呼び出しをスキップ）"""
    path = _url_cache_path(base_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_url_cache(base_dir: str, url_cache: dict[str, ProcessedArticle]) -> None:
    os.makedirs(base_dir, exist_ok=True)
    path = _url_cache_path(base_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(url_cache, f, ensure_ascii=False, indent=2)


# ── API 共通呼び出し ─────────────────────────────────────────────────────────

def _call_claude(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 2048,
) -> list[dict]:
    """Claude API 呼び出し（リトライ付き）。JSON配列を返す。"""
    wait = RETRY_BASE_WAIT
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system_prompt,
                          "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except anthropic.RateLimitError:
            if attempt < MAX_RETRIES - 1:
                print(f"[process] Rate limited. {wait}秒後にリトライ ({attempt+1}/{MAX_RETRIES-1})...")
                time.sleep(wait)
                wait *= 2
            else:
                raise
        except Exception:
            raise
    else:
        raise RuntimeError("APIリトライ上限に達しました")

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _build_screen_input(articles: list[Article]) -> str:
    """スクリーニング用の記事テキストを構築する"""
    lines: list[str] = []
    for i, a in enumerate(articles):
        lines.append(f"[{i}] lang={a.get('lang','en')}")
        lines.append(f"  title: {a.get('title','')}")
        lines.append(f"  description: {a.get('description','')}")
        lines.append("")
    return "\n".join(lines)


# ── Step A: スクリーニング ────────────────────────────────────────────────────

def _screening_all(
    client: anthropic.Anthropic,
    articles: list[Article],
    fallback_cat: str,
) -> dict[str, dict]:
    """{url: screen_result} を返す。screen_result = {selected, hot, category, title_ja}"""
    url_to_screen: dict[str, dict] = {}

    for batch_start in range(0, len(articles), MAX_SCREEN_BATCH):
        batch = articles[batch_start:batch_start + MAX_SCREEN_BATCH]
        try:
            parsed = _call_claude(
                client,
                SCREENING_PROMPT,
                f"以下の記事を判定してください:\n\n{_build_screen_input(batch)}",
                SCREENING_MODEL,
                max_tokens=1024,
            )
        except Exception as e:
            print(f"[process] スクリーニングエラー: {e}")
            continue

        for item in parsed:
            idx = item.get("index", -1)
            if not isinstance(idx, int) or idx < 0 or idx >= len(batch):
                continue
            if item.get("category", "") not in VALID_GEMINI_CATS:
                item["category"] = fallback_cat
            url_to_screen[batch[idx]["url"]] = item

    return url_to_screen


# ── Step B: 全文フェッチ（並行）─────────────────────────────────────────────

def _fetch_contents_parallel(articles: list[Article]) -> dict[str, str]:
    """{url: full_text} を並行取得する。失敗時は空文字列。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from fetch import fetch_full_content

    url_to_content: dict[str, str] = {}

    def _fetch_one(a: Article) -> tuple[str, str]:
        url = a["url"]
        try:
            content = fetch_full_content(url)
        except Exception as e:
            print(f"[process] WARN: フェッチ失敗 {url[:60]} → {e}")
            content = ""
        return url, content

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, a): a["url"] for a in articles}
        for future in as_completed(futures):
            try:
                url, content = future.result()
                url_to_content[url] = content
            except Exception as e:
                print(f"[process] WARN: フェッチ結果取得失敗 → {e}")

    return url_to_content


# ── Step C: 深層解析 ─────────────────────────────────────────────────────────

def _parse_article(original: Article, item: dict, screen: dict,
                    fallback_cat: str) -> ProcessedArticle:
    """deep_analysis の結果 + screening 結果 + 元記事 → ProcessedArticle"""
    from urllib.parse import urlparse as _urlparse

    cat = screen.get("category", fallback_cat)
    if cat not in VALID_GEMINI_CATS:
        cat = fallback_cat
    # AI主要ベンダーの一次情報は競合・業界分類から ai_product へ優先昇格
    if cat in {"competitor_press", "industry_trend"} and _is_ai_vendor_url(original["url"]):
        cat = "ai_product"

    host = (_urlparse(original["url"]).hostname or "").lstrip("www.")
    parts = host.split(".")
    src = parts[-2].capitalize() if len(parts) >= 2 else host

    try:
        impact = float(item.get("impact", 0.0))
        impact = max(0.0, min(5.0, round(impact * 10) / 10))
    except (TypeError, ValueError):
        impact = 3.0

    axes_raw = item.get("impact_axes") or {}
    try:
        axes = {
            "per": round(max(0.0, min(5.0, float(axes_raw.get("per", 0.0)))), 1),
            "sci": round(max(0.0, min(5.0, float(axes_raw.get("sci", 0.0)))), 1),
            "cps": round(max(0.0, min(5.0, float(axes_raw.get("cps", 0.0)))), 1),
        }
    except (TypeError, ValueError, AttributeError):
        axes = {"per": 0.0, "sci": 0.0, "cps": 0.0}

    return ProcessedArticle(
        title_ja=screen.get("title_ja", item.get("title_ja", original.get("title", ""))),
        url=original["url"],
        summary=item.get("summary", ""),
        insight=item.get("insight", ""),
        published=original["published"],
        category=cat,
        hot=bool(screen.get("hot", False)),
        source=src,
        impact=impact,
        impact_axes=axes,
        hashtags=[str(t) for t in (item.get("hashtags") or [])[:3]],
        competitor_group=original.get("group", ""),
    )


def _deep_analysis_all(
    client: anthropic.Anthropic,
    articles: list[Article],
    url_to_content: dict[str, str],
    screening_map: dict[str, dict],
    fallback_cat: str,
) -> list[ProcessedArticle]:
    """全文テキストを使った深層解析。ProcessedArticle のリストを返す。"""
    processed: list[ProcessedArticle] = []
    first_batch = True

    for batch_start in range(0, len(articles), MAX_ANALYSIS_BATCH):
        if not first_batch:
            time.sleep(INTER_BATCH_SLEEP)
        first_batch = False

        batch = articles[batch_start:batch_start + MAX_ANALYSIS_BATCH]

        items = []
        for local_idx, a in enumerate(batch):
            screen = screening_map.get(a["url"], {})
            content = url_to_content.get(a["url"], "") or a.get("description", "")
            items.append({
                "index":    local_idx,
                "lang":     a.get("lang", "en"),
                "category": screen.get("category", fallback_cat),
                "title_ja": screen.get("title_ja", a.get("title", "")),
                "content":  content,
            })

        user_msg = (
            "以下の記事（全文テキスト含む）を深層解析し、"
            "summary / insight / impact / impact_axes / hashtags をJSON配列で出力してください:\n\n"
            + json.dumps(items, ensure_ascii=False, indent=2)
        )

        try:
            parsed = _call_claude(
                client, DEEP_ANALYSIS_PROMPT, user_msg,
                ANALYSIS_MODEL, max_tokens=4096,
            )
        except Exception as e:
            print(f"[process] 深層解析エラー: {e}")
            continue

        for item in parsed:
            local_idx = item.get("index", 0)
            if not isinstance(local_idx, int) or local_idx >= len(batch):
                continue
            original = batch[local_idx]
            screen = screening_map.get(original["url"], {})
            article = _parse_article(original, item, screen, fallback_cat)
            processed.append(article)

    return processed


# ── パイプライン統合 ──────────────────────────────────────────────────────────

def process_category(
    client: anthropic.Anthropic,
    category_name: str,
    articles: list[Article],
    url_cache: dict[str, ProcessedArticle],
) -> list[ProcessedArticle]:
    """2ステップパイプライン: screening → full-text fetch → deep analysis"""
    if not articles:
        return []

    fallback_cat = _FEED_TO_GEMINI_CAT.get(category_name, "ai_product")
    results: list[ProcessedArticle] = []

    uncached     = [a for a in articles if a["url"] not in url_cache]
    cached_hits  = [url_cache[a["url"]] for a in articles if a["url"] in url_cache]
    results.extend(cached_hits)
    if cached_hits:
        print(f"[process]   キャッシュヒット: {len(cached_hits)}件スキップ")

    if not uncached:
        return results

    # Step A ──────────────────────────────────────────────────────────────────
    print(f"[process] Step A スクリーニング '{category_name}' ({len(uncached)}件)...")
    screening_map = _screening_all(client, uncached, fallback_cat)
    selected = [a for a in uncached if screening_map.get(a["url"], {}).get("selected")]
    hot_n    = sum(1 for r in screening_map.values() if r.get("hot"))
    print(f"[process]   通過: {len(selected)}/{len(uncached)}件  hot候補: {hot_n}件")

    if not selected:
        return results

    # Step B ──────────────────────────────────────────────────────────────────
    print(f"[process] Step B 全文フェッチ ({len(selected)}件並行)...")
    url_to_content = _fetch_contents_parallel(selected)
    ok = sum(1 for c in url_to_content.values() if c)
    print(f"[process]   取得成功: {ok}/{len(selected)}件"
          + (f"  フォールバック: {len(selected)-ok}件" if ok < len(selected) else ""))

    # Step C ──────────────────────────────────────────────────────────────────
    print(f"[process] Step C 深層解析 ({len(selected)}件)...")
    deep = _deep_analysis_all(client, selected, url_to_content, screening_map, fallback_cat)
    for article in deep:
        results.append(article)
        url_cache[article["url"]] = article
    print(f"[process]   完了: {len(deep)}件")

    return results


# ── エントリポイント ──────────────────────────────────────────────────────────

def process_all(
    articles_by_category: dict[str, list[Article]],
    cache_dir: str = "cache",
    dry_run: bool = False,
) -> dict[str, list[ProcessedArticle]]:
    """
    全カテゴリの記事を処理して返す。

    dry_run=True: API・キャッシュを使わずダミーデータを返す。
    通常時: 今日のキャッシュがあれば再利用し、なければ 2ステップパイプラインを実行。
    """
    if dry_run:
        print("[process] DRY-RUN: ダミーデータを生成します（APIは呼びません）")
        return {cat: _dummy_articles(cat) for cat in articles_by_category}

    cached = load_cache(cache_dir)
    if cached is not None:
        return cached

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY が設定されていません")

    client = anthropic.Anthropic(api_key=api_key)
    url_cache = load_url_cache(cache_dir)
    print(f"[process] URLキャッシュ: {len(url_cache)}件既知")

    result: dict[str, list[ProcessedArticle]] = {}
    for category_name, articles in articles_by_category.items():
        result[category_name] = process_category(client, category_name, articles, url_cache)

    save_url_cache(cache_dir, url_cache)
    if any(result.values()):
        save_cache(cache_dir, result)
    else:
        print("[process] 選別結果が0件のためキャッシュは保存しません。")
    return result
