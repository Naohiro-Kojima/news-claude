# My News Digest

毎朝8時（JST）に自動更新される個人用ニュースキュレーションサイトです。
RSS フィードから記事を収集し、Claude API で精査・要約・インサイトを生成して GitHub Pages に公開します。

## セットアップ手順

### 1. このリポジトリを GitHub に作成する

```bash
git init
git add .
git commit -m "initial commit"
gh repo create my-news-digest --public --source=. --push
```

### 2. Anthropic API キーを GitHub Secrets に登録する

1. リポジトリの **Settings → Secrets and variables → Actions**
2. **New repository secret** をクリック
3. Name: `ANTHROPIC_API_KEY` / Value: APIキーを入力して保存

### 3. GitHub Pages を有効にする

1. リポジトリの **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / Folder: `/docs` を選択して **Save**

公開URL: `https://<username>.github.io/<repo-name>/`

### 4. 動作確認（手動実行）

1. リポジトリの **Actions** タブを開く
2. **Update News** ワークフローを選択
3. **Run workflow** をクリック

ワークフローが完了すると `docs/index.html` が更新され、数分後に GitHub Pages に反映されます。

## ローカルでの開発・テスト

```bash
# 依存ライブラリをインストール
pip install -r requirements.txt

# .env ファイルを作成して API キーを設定
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# スクリプトを実行（docs/index.html が生成される）
python scripts/main.py

# ブラウザで確認
open docs/index.html   # Mac
start docs/index.html  # Windows
```

## カテゴリ・フィードの変更

[config/feeds.yml](config/feeds.yml) を編集するだけで反映されます。

```yaml
categories:
  - name: カテゴリ名
    feeds:
      - url: https://example.com/feed.xml
        lang: en  # または ja
```

## ファイル構成

```
.github/workflows/update_news.yml  # 毎朝8時 JST に自動実行
config/feeds.yml                   # RSSフィード設定
scripts/
  main.py      # エントリポイント
  fetch.py     # RSSフィード取得
  process.py   # Claude API による精査・要約
  generate.py  # HTML生成
docs/
  index.html   # GitHub Pages に公開される生成済みHTML
```
