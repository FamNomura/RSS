import calendar
import csv
import datetime
import html
import io
import json
import os
import re
import socket
import sys
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import feedparser
import pytz
from jinja2 import Environment, FileSystemLoader

# ==========================================
# デバッグ設定（完成後に False にするとログ出力を一括無効化できます）
# ==========================================
DEBUG = True
RUN_TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
DEBUG_LOG_PATH = os.path.join("log", f"debug_log_{RUN_TIMESTAMP}.txt")

# ==========================================
# アプリ基本設定
# ==========================================
CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTKtl6lGptpOhDEoIbU-C9RkQttsBxbzeILCnxya-do6uPaRIW1xyHBtwH6HsU4ZDpYIhDc05D52mt4/pub?gid=0&single=true&output=csv"
TEMPLATE_FILE = "template.html"
OUTPUT_DIR = "docs"
MAX_ENTRIES = 10
SUMMARY_MAX_LENGTH = 120
NEW_THRESHOLD_HOURS = 24
TIMEOUT_SECONDS = 15
MAX_WORKERS = 10

# クリーンアップ時に削除せず残すファイル
PROTECTED_FILES = {
    "manifest.json",
    "icon.svg",
    ".nojekyll",
    "CNAME",
    ".gitkeep",
}

socket.setdefaulttimeout(TIMEOUT_SECONDS)


def write_debug_log(message, error_detail=""):
    """デバッグログ出力（DEBUG = False で一括無効化）"""
    if not DEBUG:
        return
    try:
        os.makedirs("log", exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{now_str}] {message}\n")
            if error_detail:
                f.write(f"Details:\n{error_detail}\n")
    except Exception as log_err:
        print(f"Failed to write debug log: {log_err}")


def clean_summary(raw_text, max_len=SUMMARY_MAX_LENGTH):
    """HTMLタグと余分な空白を除去し、指定文字数でトリミングする"""
    if not raw_text:
        return ""
    # script/styleタグを中身ごと除去
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_text, flags=re.DOTALL | re.IGNORECASE)
    # HTMLタグを除去
    text = re.sub(r"<[^>]+>", " ", text)
    # HTML文字参照をデコード（&amp; -> & など）
    text = html.unescape(text)
    # 連続する空白・改行を1つのスペースに正規化
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[:max_len].rstrip() + "…"
    return text


def load_config_from_csv(url):
    print("Loading config from Google Sheets CSV...")
    write_debug_log(f"Fetching CSV config from: {url}")
    config = {"pages": [], "watches": []}

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MyRSSReader/2.0)"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            csv_data = response.read().decode("utf-8-sig")
    except Exception as e:
        err_msg = f"Error fetching CSV: {e}"
        print(err_msg)
        write_debug_log(err_msg, traceback.format_exc())
        sys.exit(1)

    reader = csv.reader(io.StringIO(csv_data))
    _ = next(reader, None)  # ヘッダー行をスキップ

    pages_dict = {}
    watches_dict = {}

    for row in reader:
        if not row or len(row) < 3:
            continue

        row_type = row[0].strip()
        page_title = row[1].strip() if len(row) > 1 else ""
        filename = row[2].strip() if len(row) > 2 else ""
        title_or_kw = row[3].strip() if len(row) > 3 else ""
        rss_url = row[4].strip() if len(row) > 4 else ""
        hidden_str = row[5].strip().upper() if len(row) > 5 else ""
        ng_kw_str = row[6].strip() if len(row) > 6 else ""

        if not filename:
            continue

        is_hidden = hidden_str == "TRUE"
        ng_keywords = [k.strip() for k in ng_kw_str.split(",") if k.strip()]

        if row_type == "Page":
            if page_title not in pages_dict:
                pages_dict[page_title] = {
                    "page_title": page_title,
                    "filename": filename,
                    "hidden": is_hidden,
                    "feeds": [],
                    "ng_keywords": [],
                }
            if rss_url:
                pages_dict[page_title]["feeds"].append({
                    "title": title_or_kw,
                    "url": rss_url,
                })
            if ng_keywords:
                for kw in ng_keywords:
                    if kw not in pages_dict[page_title]["ng_keywords"]:
                        pages_dict[page_title]["ng_keywords"].append(kw)

        elif row_type == "Watch":
            keywords = [k.strip() for k in title_or_kw.split(",") if k.strip()]
            if filename not in watches_dict:
                watches_dict[filename] = {
                    "page_title": page_title,
                    "filename": filename,
                    "hidden": is_hidden,
                    "keywords": keywords,
                    "always_feeds": [],
                    "ng_keywords": [],
                }
            else:
                watches_dict[filename]["page_title"] = page_title
                watches_dict[filename]["hidden"] = is_hidden
                watches_dict[filename]["keywords"] = keywords
            if ng_keywords:
                for kw in ng_keywords:
                    if kw not in watches_dict[filename]["ng_keywords"]:
                        watches_dict[filename]["ng_keywords"].append(kw)

        elif row_type.lower() in ["watch-feed", "watchfeed", "watch_feed", "watch feed"]:
            if filename not in watches_dict:
                watches_dict[filename] = {
                    "page_title": page_title,
                    "filename": filename,
                    "hidden": is_hidden,
                    "keywords": [],
                    "always_feeds": [],
                    "ng_keywords": [],
                }
            if rss_url:
                watches_dict[filename]["always_feeds"].append({
                    "title": title_or_kw,
                    "url": rss_url,
                })
            if ng_keywords:
                for kw in ng_keywords:
                    if kw not in watches_dict[filename]["ng_keywords"]:
                        watches_dict[filename]["ng_keywords"].append(kw)

    config["pages"] = list(pages_dict.values())
    config["watches"] = list(watches_dict.values())
    write_debug_log(f"Loaded config: {len(config['pages'])} pages, {len(config['watches'])} watches.")
    return config


def get_domain(url):
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except Exception:
        return ""


def parse_date(entry):
    """環境タイムゾーンに依存しないUTC日時パース"""
    parsed_tuple = None
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        parsed_tuple = entry.published_parsed
    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
        parsed_tuple = entry.updated_parsed

    if parsed_tuple:
        try:
            ts = calendar.timegm(parsed_tuple)
            return datetime.datetime.fromtimestamp(ts, tz=pytz.utc)
        except Exception:
            return None
    return None


def format_relative_time(dt_obj, now_utc):
    if not dt_obj:
        return ""
    diff = now_utc - dt_obj
    seconds = max(0, int(diff.total_seconds()))
    if seconds < 60:
        return "たった今"
    elif seconds < 3600:
        return f"{seconds // 60}分前"
    elif seconds < 86400:
        return f"{seconds // 3600}時間前"
    elif seconds < 172800:
        return "昨日"
    else:
        return f"{seconds // 86400}日前"


def extract_image(entry):
    if "media_content" in entry:
        for media in entry.media_content:
            if "image" in media.get("type", "") or media.get("medium") == "image":
                if media.get("url"):
                    return media["url"]
    if "media_thumbnail" in entry and entry.media_thumbnail:
        if entry.media_thumbnail[0].get("url"):
            return entry.media_thumbnail[0]["url"]
    if "links" in entry:
        for link in entry.links:
            if link.get("rel") == "enclosure" and "image" in link.get("type", ""):
                if link.get("href"):
                    return link["href"]
    raw_content = entry.get("summary", "") + entry.get("content", [{"value": ""}])[0].get("value", "")
    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_content, flags=re.IGNORECASE)
    if img_match:
        src = img_match.group(1)
        if src.startswith("http://") or src.startswith("https://"):
            return src
    return None


def is_ng_content(entry, ng_keywords):
    if not ng_keywords:
        return False
    text = (entry.get("title", "") + " " + entry.get("search_text", "")).lower()
    for keyword in ng_keywords:
        if keyword.lower() in text:
            return True
    return False


def process_entry(entry, feed_title, now_utc):
    dt = parse_date(entry)
    is_new = False
    rel_time = ""
    timestamp = 0

    if dt:
        diff_sec = (now_utc - dt).total_seconds()
        if 0 <= diff_sec < (NEW_THRESHOLD_HOURS * 3600):
            is_new = True
        rel_time = format_relative_time(dt, now_utc)
        timestamp = dt.timestamp()

    raw_summary = entry.get("summary", entry.get("description", ""))
    raw_content = entry.get("content", [{"value": ""}])[0].get("value", "")
    longer_raw = raw_content if len(raw_content) > len(raw_summary) else raw_summary

    # キーワード判定用に長めのプレーンテキストを内部保持し、表示用は120文字にトリミング
    plain_full = clean_summary(longer_raw, max_len=1000)
    short_summary = clean_summary(longer_raw, max_len=SUMMARY_MAX_LENGTH)
    image_url = extract_image(entry)

    return {
        "title": html.unescape(entry.get("title", "No Title")).strip(),
        "link": entry.get("link", "#").strip(),
        "is_new": is_new,
        "relative_time": rel_time,
        "summary": short_summary,
        "search_text": plain_full,
        "image": image_url,
        "timestamp": timestamp,
        "source_title": feed_title,
    }


def fetch_single_feed(url, title_override, now_utc):
    user_agent = "Mozilla/5.0 (compatible; MyRSSReader/2.0)"
    try:
        d = feedparser.parse(url, agent=user_agent)
        if d.bozo and isinstance(d.bozo_exception, (socket.timeout, socket.error, OSError)):
            raise d.bozo_exception

        feed_title = title_override if title_override else d.feed.get("title", "Unknown Feed")
        domain = get_domain(d.feed.get("link", url))
        favicon = f"https://www.google.com/s2/favicons?domain={domain}&sz=32" if domain else ""

        entries = []
        for entry in d.entries[:MAX_ENTRIES]:
            entries.append(process_entry(entry, feed_title, now_utc))

        return url, {
            "title": feed_title,
            "favicon": favicon,
            "entries": entries,
        }
    except Exception as e:
        err_msg = f"Error fetching {url}: {e}"
        print(f"  {err_msg}")
        write_debug_log(err_msg, traceback.format_exc())
        return url, None


def fetch_all_feeds(config):
    # URLごとに重複排除（最初に指定されたタイトルを優先）
    url_map = {}
    for page in config.get("pages", []):
        for feed in page.get("feeds", []):
            clean_url = feed["url"].strip()
            if clean_url and clean_url not in url_map:
                url_map[clean_url] = feed.get("title", "")

    for watch in config.get("watches", []):
        for feed in watch.get("always_feeds", []):
            clean_url = feed["url"].strip()
            if clean_url and clean_url not in url_map:
                url_map[clean_url] = feed.get("title", "")

    total = len(url_map)
    print(f"Fetching {total} unique feeds in parallel (workers={MAX_WORKERS})...")
    write_debug_log(f"Start parallel fetch: {total} feeds.")

    now_utc = datetime.datetime.now(pytz.utc)
    results = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_single_feed, url, title, now_utc): url
            for url, title in url_map.items()
        }
        for future in as_completed(futures):
            url, data = future.result()
            results[url] = data

    success_count = sum(1 for v in results.values() if v is not None)
    print(f"Fetched {success_count}/{total} feeds successfully.")
    write_debug_log(f"Finished parallel fetch: {success_count}/{total} succeeded.")
    return results


def generate_pwa_assets(output_dir):
    """PWA用の manifest.json と軽量SVGアイコンを自動生成"""
    manifest = {
        "name": "RSS News Portal",
        "short_name": "RSS News",
        "description": "スマホ最適化された個人用RSSニュースリーダー",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f4f6f9",
        "theme_color": "#1e293b",
        "icons": [
            {
                "src": "./icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    icon_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="112" fill="#2563eb"/>
  <circle cx="152" cy="360" r="44" fill="#ffffff"/>
  <path d="M108 232a172 172 0 0 1 172 172" fill="none" stroke="#ffffff" stroke-width="44" stroke-linecap="round"/>
  <path d="M108 124a280 280 0 0 1 280 280" fill="none" stroke="#ffffff" stroke-width="44" stroke-linecap="round"/>
</svg>
"""
    icon_path = os.path.join(output_dir, "icon.svg")
    with open(icon_path, "w", encoding="utf-8") as f:
        f.write(icon_svg)


def cleanup_old_files(output_dir, generated_files):
    """スプレッドシートに存在しない古いファイルや専用ページを自動削除"""
    keep_set = set(generated_files) | PROTECTED_FILES
    removed = []
    for name in os.listdir(output_dir):
        full_path = os.path.join(output_dir, name)
        if os.path.isfile(full_path) and name not in keep_set:
            try:
                os.remove(full_path)
                removed.append(name)
            except Exception as e:
                write_debug_log(f"Failed to remove old file {name}: {e}")
    if removed:
        print(f"Cleaned up {len(removed)} obsolete files: {', '.join(removed)}")
        write_debug_log(f"Cleaned up obsolete files: {removed}")


def main():
    start_time = datetime.datetime.now()
    write_debug_log("=== Build started ===")
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        config = load_config_from_csv(CSV_URL)

        navigation = []
        for watch in config.get("watches", []):
            if not watch.get("hidden", False):
                navigation.append({
                    "page_title": watch["page_title"],
                    "filename": watch["filename"],
                })
        for page in config.get("pages", []):
            if not page.get("hidden", False):
                navigation.append({
                    "page_title": page["page_title"],
                    "filename": page["filename"],
                })

        all_feeds_data = fetch_all_feeds(config)

        jst = pytz.timezone("Asia/Tokyo")
        now_str = datetime.datetime.now(jst).strftime("%m/%d %H:%M")

        env = Environment(loader=FileSystemLoader(".", encoding="utf-8"), autoescape=True)
        template = env.get_template(TEMPLATE_FILE)

        generated_files = set()

        # 1. 通常ページの生成
        for page_config in config.get("pages", []):
            target_filename = page_config["filename"]
            print(f"Building Page: {target_filename}")

            page_config["is_topic"] = False
            ng_keywords = page_config.get("ng_keywords", [])

            page_entries = []
            page_feeds = []
            seen_links = set()

            for feed_conf in page_config.get("feeds", []):
                url = feed_conf["url"].strip()
                source_data = all_feeds_data.get(url)
                if not source_data:
                    continue

                valid_entries = [e for e in source_data["entries"] if not is_ng_content(e, ng_keywords)]
                if valid_entries:
                    feed_entries_clean = []
                    for e in valid_entries:
                        e_copy = e.copy()
                        e_copy["favicon"] = source_data["favicon"]
                        e_copy["source_title"] = source_data["title"]
                        feed_entries_clean.append(e_copy)
                        if e_copy["link"] not in seen_links:
                            seen_links.add(e_copy["link"])
                            page_entries.append(e_copy)

                    page_feeds.append({
                        "title": source_data["title"],
                        "favicon": source_data["favicon"],
                        "entries": feed_entries_clean,
                        "total_count": len(feed_entries_clean),
                        "new_count": sum(1 for e in feed_entries_clean if e["is_new"]),
                        "has_new": any(e["is_new"] for e in feed_entries_clean),
                    })

            page_entries.sort(key=lambda x: x["timestamp"], reverse=True)

            output_path = os.path.join(OUTPUT_DIR, target_filename)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(
                    template.render(
                        navigation=navigation,
                        current_page=page_config,
                        entries=page_entries,
                        feeds=page_feeds,
                        topics=[],
                        last_updated=now_str,
                    )
                )
            generated_files.add(target_filename)

        # 2. ウォッチページの生成
        for watch_config in config.get("watches", []):
            target_filename = watch_config["filename"]
            print(f"Building Watch Page: {target_filename}")

            watch_config["is_topic"] = True
            keywords = watch_config.get("keywords", [])
            always_feeds = watch_config.get("always_feeds", [])
            ng_keywords = watch_config.get("ng_keywords", [])

            watch_entries = []
            watch_topics = []
            site_data_dict = {}
            seen_links = set()

            # (1) キーワードによる抽出
            for kw in keywords:
                kw_entries = []
                kw_lower = kw.lower()
                for url, source_data in all_feeds_data.items():
                    if not source_data:
                        continue
                    for entry in source_data["entries"]:
                        if is_ng_content(entry, ng_keywords):
                            continue
                        text_to_search = (entry["title"] + " " + entry["search_text"]).lower()
                        if kw_lower in text_to_search:
                            e_copy = entry.copy()
                            e_copy["favicon"] = source_data["favicon"]
                            e_copy["source_title"] = source_data["title"]
                            kw_entries.append(e_copy)

                            if entry["link"] not in seen_links:
                                seen_links.add(entry["link"])
                                watch_entries.append(e_copy)
                                if url not in site_data_dict:
                                    site_data_dict[url] = {
                                        "title": source_data["title"],
                                        "favicon": source_data["favicon"],
                                        "entries": [],
                                    }
                                site_data_dict[url]["entries"].append(e_copy)

                if kw_entries:
                    kw_entries.sort(key=lambda x: x["timestamp"], reverse=True)
                    watch_topics.append({
                        "title": f"キーワード: {kw}",
                        "favicon": "",
                        "entries": kw_entries,
                        "total_count": len(kw_entries),
                        "new_count": sum(1 for e in kw_entries if e["is_new"]),
                        "has_new": any(e["is_new"] for e in kw_entries),
                    })

            # (2) 常時表示フィード（Watch-Feed）の全記事を追加
            for feed_conf in always_feeds:
                url = feed_conf["url"].strip()
                source_data = all_feeds_data.get(url)
                if not source_data:
                    continue

                feed_entries = []
                for entry in source_data["entries"]:
                    if is_ng_content(entry, ng_keywords):
                        continue
                    e_copy = entry.copy()
                    e_copy["favicon"] = source_data["favicon"]
                    e_copy["source_title"] = source_data["title"]
                    feed_entries.append(e_copy)

                    if entry["link"] not in seen_links:
                        seen_links.add(entry["link"])
                        watch_entries.append(e_copy)
                        if url not in site_data_dict:
                            site_data_dict[url] = {
                                "title": source_data["title"],
                                "favicon": source_data["favicon"],
                                "entries": [],
                            }
                        site_data_dict[url]["entries"].append(e_copy)

                if feed_entries:
                    feed_entries.sort(key=lambda x: x["timestamp"], reverse=True)
                    display_title = feed_conf.get("title") or source_data["title"]
                    watch_topics.append({
                        "title": f"📌 固定: {display_title}",
                        "favicon": source_data["favicon"],
                        "entries": feed_entries,
                        "total_count": len(feed_entries),
                        "new_count": sum(1 for e in feed_entries if e["is_new"]),
                        "has_new": any(e["is_new"] for e in feed_entries),
                    })

            watch_entries.sort(key=lambda x: x["timestamp"], reverse=True)

            watch_feeds = []
            for _, data in site_data_dict.items():
                data["entries"].sort(key=lambda x: x["timestamp"], reverse=True)
                watch_feeds.append({
                    "title": data["title"],
                    "favicon": data["favicon"],
                    "entries": data["entries"],
                    "total_count": len(data["entries"]),
                    "new_count": sum(1 for e in data["entries"] if e["is_new"]),
                    "has_new": any(e["is_new"] for e in data["entries"]),
                })

            output_path = os.path.join(OUTPUT_DIR, target_filename)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(
                    template.render(
                        navigation=navigation,
                        current_page=watch_config,
                        entries=watch_entries,
                        feeds=watch_feeds,
                        topics=watch_topics,
                        last_updated=now_str,
                    )
                )
            generated_files.add(target_filename)

        # 3. PWAアセット生成 & 不要ファイル削除
        generate_pwa_assets(OUTPUT_DIR)
        cleanup_old_files(OUTPUT_DIR, generated_files)

        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        msg = f"All pages generated successfully in {elapsed:.2f}s ({len(generated_files)} pages)."
        print(msg)
        write_debug_log(msg)

    except Exception as e:
        err_msg = f"Unexpected error in main: {e}"
        print(err_msg)
        write_debug_log(err_msg, traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
