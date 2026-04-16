"""Anthropic Claude API を使って記事の精査・翻訳・要約・インサイト生成を行う"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import TypedDict

import anthropic

from fetch import Article

MODEL = "claude-haiku-4-5-20251001"
MAX_ARTICLES_PER_BATCH = 5
INTER_BATCH_SLEEP = 1       # Claude APIはレートリミットが緩いため短い
MAX_RETRIES = 4
RETRY_BASE_WAIT = 30

JST = timezone(timedelta(hours=9))

SYSTEM_PROMPT = """あなたはAI・脳科学・組織人事専門のニュース編集者です。以下のステップで記事を分析し、JSON配列のみを出力してください。

━━ ステップ1：言語処理 ━━
・英語記事（lang=en）: title + description を自然な日本語に翻訳してから分析する
・日本語記事（lang=ja）: そのまま分析する

━━ ステップ2：カテゴリ自動分類 ━━
記事の内容に基づき、以下の11のカテゴリキーのいずれかを割り当てる:

| category キー      | 対象                                                              |
|--------------------|-------------------------------------------------------------------|
| ai_social          | AI技術の実社会・ビジネス応用、産業実装、企業導入事例             |
| ai_press           | AI企業のプレスリリース、新製品発表、モデル・サービス公開         |
| ai_academic        | AI・機械学習・深層学習の学術論文・研究成果                       |
| neuro_social       | 脳科学・心理学のビジネス利用、製品化、社会実装                   |
| neuro_press        | 脳科学・神経科学領域の企業・研究機関による最新公式発表           |
| neuro_embodiment   | 自己意識、身体所有感、ロボット・VR介在、fNIRS計測など身体性研究  |
| neuro_psychology   | 心理学、組織行動、集団心理、認知科学、脳の豆知識・ナゾロジー系  |
| neuro_ai           | 脳科学とAIの融合（ニューロモーフィック、BCI、BMIなど）           |
| hr_social          | 組織人事の社会実装・ビジネス応用、HRの実践事例・導入レポート     |
| hr_press           | HRテック・組織人事領域の企業発表・新製品・サービスリリース       |
| hr_academic        | 組織行動・人的資本・採用・評価・エンゲージメントの学術研究       |

━━ ステップ3：精査基準 ━━
【selected=true（掲載）】
・AI系: 業務生産性・自動化・エージェント化・意思決定支援に直結する技術・製品・研究
・脳科学系: ビジネス・組織・教育・研究の文脈で話のネタやヒントになりうる発見・研究
・組織人事系: 採用・育成・エンゲージメント・組織設計に実践的な示唆を持つ研究・事例・製品

【selected=false（除外）】
・単純なイベント告知・採用情報・既報の焼き直し・応用価値が不明瞭な純粋臨床報告

【hot=true（パラダイムシフト判定）】
2027年の就職・入社前に知っておくべきパラダイムシフト——業界・職種・組織の在り方を根底から変える発見や発表のみ。
selected=true 全体の20〜30%以下が目安。

━━ ステップ4：要約とインサイト生成（日本語） ━━
【要約 summary】大学院生が30秒で本質を理解できる3〜4文。「何が・どう・なぜ重要か」を明確に。
【インサイト insight（AI系）】この技術・研究を業務生産性向上にどう転用できるか、具体的なアクション示唆を2〜3文。
【インサイト insight（脳科学系）】この発見・研究の面白さは何か、ビジネス・研究・日常会話のどの場面で話のネタやヒントになりそうか、2〜3文。
【インサイト insight（組織人事系）】採用・育成・制度設計・マネジメントにどう活かせるか、実践的アクション示唆を2〜3文。

━━ ステップ5：インパクトスコア算出 ━━
selected=trueの記事のみ対象。以下の基準で0.0〜5.0の数値を0.1刻みで算出する。
・5.0: 産業・職種・組織構造を根底から変える、10年に1度の発見・発表
・4.0〜4.9: 今後1〜3年以内に実務・業界に大きな影響を与える可能性が高い
・3.0〜3.9: 重要だが影響範囲が限定的、または実用化まで時間がかかる
・2.0〜2.9: 参考情報・周辺知識として価値あり
・1.0〜1.9: 限定的な応用価値、特定領域の専門家向け
・0.0〜0.9: 掲載基準ギリギリの情報価値

━━ ステップ6：ハッシュタグ生成 ━━
selected=trueの記事のみ対象。記事テーマを端的に表す日本語ハッシュタグを3つ生成。
例: ["#生成AI", "#業務自動化", "#エージェント"]

━━ 出力形式 ━━
以下のJSON配列のみを出力してください。コードブロック・説明文は不要です。
[
  {
    "index": 元記事のインデックス番号(整数),
    "selected": true/false,
    "hot": true/false,
    "category": "11のカテゴリキーのいずれか",
    "title_ja": "日本語タイトル（英語は翻訳、日本語はそのまま）",
    "summary": "3〜4文の日本語要約（selected=falseの場合は空文字列）",
    "insight": "2〜3文の日本語インサイト（selected=falseの場合は空文字列）",
    "impact": スコア数値（0.0〜5.0、selected=falseの場合は0.0）,
    "hashtags": ["#タグ1", "#タグ2", "#タグ3"]（selected=falseの場合は空配列）
  }
]
重要: selected=false の記事も配列に含め、その場合 hot=false、category は最も近いキーを入れてください。"""


VALID_GEMINI_CATS: frozenset[str] = frozenset({
    "ai_social", "ai_press", "ai_academic",
    "neuro_social", "neuro_press", "neuro_embodiment", "neuro_psychology",
    "neuro_ai",
    "hr_social", "hr_press", "hr_academic",
})

_FEED_TO_GEMINI_CAT: dict[str, str] = {
    "AI社会実装・ビジネス":      "ai_social",
    "AIメーカー・プレスリリース": "ai_press",
    "AI学術研究・最新論文":      "ai_academic",
    "脳科学・社会実装":          "neuro_social",
    "脳科学・プレスリリース":    "neuro_press",
    "脳科学研究・身体性":        "neuro_embodiment",
    "脳科学研究・心理認知":      "neuro_psychology",
    "脳科学×AI":                "neuro_ai",
    "組織人事・社会実装":        "hr_social",
    "組織人事・プレスリリース":  "hr_press",
    "組織人事・研究論文":        "hr_academic",
}


class ProcessedArticle(TypedDict):
    title_ja: str
    url: str
    summary: str
    insight: str
    published: str
    category: str
    hot: bool
    source: str     # 取得元ドメイン表示名（例: "TechCrunch"）
    impact: float   # 0.0〜5.0（0.1刻み）
    hashtags: list  # 日本語ハッシュタグ 3件


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
    "ai_social":        ["#AI実装", "#業務自動化", "#DX"],
    "ai_press":         ["#生成AI", "#LLM", "#AIサービス"],
    "ai_academic":      ["#機械学習", "#深層学習", "#AI研究"],
    "neuro_social":     ["#脳科学", "#組織設計", "#ウェルビーイング"],
    "neuro_press":      ["#神経科学", "#脳研究", "#ニューロテック"],
    "neuro_embodiment": ["#身体性", "#具身認知", "#VR"],
    "neuro_psychology": ["#認知科学", "#心理学", "#認知バイアス"],
    "neuro_ai":         ["#BCI", "#ニューロモーフィック", "#脳×AI"],
    "hr_social":        ["#組織開発", "#人材育成", "#HR実践"],
    "hr_press":         ["#HRテック", "#HRツール", "#採用DX"],
    "hr_academic":      ["#組織行動", "#人的資本", "#エンゲージメント"],
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
    gemini_cat = _FEED_TO_GEMINI_CAT.get(category_name, "ai_social")
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
            hashtags=dummy_tags,
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


# ── API呼び出し ────────────────────────────────────────────────────────────

def _build_article_list(articles: list[Article], category_name: str = "") -> str:
    lines = []
    if category_name:
        lines.append(f"カテゴリ: {category_name}\n")
    for i, a in enumerate(articles):
        lines.append(f"[{i}] lang={a['lang']}")
        lines.append(f"  title: {a['title']}")
        lines.append(f"  description: {a['description']}")
        lines.append("")
    return "\n".join(lines)


def _call_api(client: anthropic.Anthropic, article_text: str) -> list[dict]:
    wait = RETRY_BASE_WAIT
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"以下の記事リストを処理してください:\n\n{article_text}",
                    }
                ],
            )
            break
        except anthropic.RateLimitError as e:
            if attempt < MAX_RETRIES - 1:
                print(f"[process] Rate limited. {wait}秒後にリトライ ({attempt + 1}/{MAX_RETRIES - 1})...")
                time.sleep(wait)
                wait *= 2
            else:
                raise
        except Exception as e:
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


def process_category(
    client: anthropic.Anthropic,
    category_name: str,
    articles: list[Article],
    url_cache: dict[str, ProcessedArticle],
) -> list[ProcessedArticle]:
    if not articles:
        return []

    fallback_cat = _FEED_TO_GEMINI_CAT.get(category_name, "ai_social")
    results: list[ProcessedArticle] = []

    # URLキャッシュにある記事はAPIをスキップ
    uncached = [a for a in articles if a["url"] not in url_cache]
    cached_hits = [url_cache[a["url"]] for a in articles if a["url"] in url_cache]
    results.extend(cached_hits)
    if cached_hits:
        print(f"[process]   (キャッシュヒット: {len(cached_hits)}件スキップ)")

    first_batch = True
    for batch_start in range(0, len(uncached), MAX_ARTICLES_PER_BATCH):
        if not first_batch:
            print(f"[process]   {INTER_BATCH_SLEEP}秒待機中...")
            time.sleep(INTER_BATCH_SLEEP)
        first_batch = False

        batch = uncached[batch_start : batch_start + MAX_ARTICLES_PER_BATCH]
        article_text = _build_article_list(batch, category_name)

        print(f"[process] Calling API for '{category_name}' ({len(batch)} articles)...")
        try:
            parsed = _call_api(client, article_text)
        except (json.JSONDecodeError, Exception) as e:
            print(f"[process] ERROR: {e}")
            continue

        for item in parsed:
            if not item.get("selected"):
                continue
            idx: int = item["index"]
            if idx >= len(batch):
                continue
            original = batch[idx]
            cat = item.get("category", "")
            if cat not in VALID_GEMINI_CATS:
                cat = fallback_cat
            from urllib.parse import urlparse as _urlparse
            _host = (_urlparse(original["url"]).hostname or "").lstrip("www.")
            _parts = _host.split(".")
            _src = _parts[-2].capitalize() if len(_parts) >= 2 else _host
            try:
                _impact = float(item.get("impact", 0.0))
                _impact = max(0.0, min(5.0, round(_impact * 10) / 10))
            except (TypeError, ValueError):
                _impact = 3.0
            _hashtags = [str(t) for t in (item.get("hashtags") or [])[:3]]
            article = ProcessedArticle(
                title_ja=item.get("title_ja", original["title"]),
                url=original["url"],
                summary=item.get("summary", ""),
                insight=item.get("insight", ""),
                published=original["published"],
                category=cat,
                hot=bool(item.get("hot", False)),
                source=_src,
                impact=_impact,
                hashtags=_hashtags,
            )
            results.append(article)
            url_cache[original["url"]] = article

    print(f"[process]   -> {len(results)} articles selected")
    return results


# ── エントリポイント ────────────────────────────────────────────────────────

def process_all(
    articles_by_category: dict[str, list[Article]],
    cache_dir: str = "cache",
    dry_run: bool = False,
) -> dict[str, list[ProcessedArticle]]:
    """
    全カテゴリの記事を処理して返す。

    dry_run=True のとき: API・キャッシュを使わずダミーデータを返す。
    通常時: 今日のキャッシュがあれば再利用し、なければ Claude API を呼ぶ。
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
