"""エントリポイント：RSS取得 → API精査 → HTML生成 を順に実行する"""

from __future__ import annotations

import argparse
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from fetch import fetch_all
from process import process_all
from generate import generate_html, generate_archive_page, generate_archive_index

CONFIG_PATH  = os.path.join(os.path.dirname(__file__), "..", "config", "feeds.yml")
OUTPUT_PATH  = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")
CACHE_DIR    = os.path.join(os.path.dirname(__file__), "..", "cache")
ARCHIVE_DIR  = os.path.join(os.path.dirname(__file__), "..", "docs", "archive")


def _category_names() -> dict:
    """feeds.yml からカテゴリ名だけを取得する（dry-run 用）"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return {cat["name"]: [] for cat in config["categories"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="ニュースキュレーションサイト生成")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="RSSもAPIも使わずダミーデータでHTMLだけ生成する（UI確認用）",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("=== [DRY-RUN] ダミーデータでHTML生成 ===")
        category_input = _category_names()
        processed = process_all(category_input, dry_run=True)
        generate_html(processed, output_path=OUTPUT_PATH, cache_dir=CACHE_DIR)
        generate_archive_page(processed, archive_dir=ARCHIVE_DIR)
        generate_archive_index(archive_dir=ARCHIVE_DIR)
        print("完了しました（APIは呼んでいません）。")
        return

    print("=== Step 1: RSS フィード取得 ===")
    articles_by_category = fetch_all(config_path=CONFIG_PATH)
    total = sum(len(v) for v in articles_by_category.values())
    print(f"取得合計: {total} 件\n")

    if total == 0:
        print("記事が0件のため処理を終了します。")
        return

    print("=== Step 2: Claude API による精査・要約 ===")
    processed = process_all(articles_by_category, cache_dir=CACHE_DIR)
    selected_total = sum(len(v) for v in processed.values())
    print(f"選別合計: {selected_total} 件\n")

    print("=== Step 3: HTML 生成 ===")
    generate_html(processed, output_path=OUTPUT_PATH, cache_dir=CACHE_DIR)

    print("=== Step 4: アーカイブ生成 ===")
    generate_archive_page(processed, archive_dir=ARCHIVE_DIR)
    generate_archive_index(archive_dir=ARCHIVE_DIR)

    print("\n完了しました。")


if __name__ == "__main__":
    main()
