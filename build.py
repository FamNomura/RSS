import json
import feedparser
import datetime
import pytz
import sys
import time
import re
from urllib.parse import urlparse
from jinja2 import Environment, FileSystemLoader

# 設定
feeds_file = 'feeds.json'
template_file = 'template.html'
max_entries = 10 
new_threshold_hours = 24

def load_config(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

def get_domain(url):
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except:
        return ""

def parse_date(entry):
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime.datetime.fromtimestamp(time.mktime(entry.published_parsed), pytz.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        return datetime.datetime.fromtimestamp(time.mktime(entry.updated_parsed), pytz.utc)
    return None

def format_relative_time(dt_obj, now_utc):
    if not dt_obj:
        return ""
    diff = now_utc - dt_obj
    seconds = diff.total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}分前"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}時間前"
    elif seconds < 172800:
        return "昨日"
    else:
        return f"{int(seconds // 86400)}日前"

def extract_image(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'medium' in media and media['medium'] == 'image':
                return media['url']
    if 'media_thumbnail' in entry:
        return entry.media_thumbnail[0]['url']
    if 'links' in entry:
        for link in entry.links:
            if link.get('rel') == 'enclosure' and 'image' in link.get('type', ''):
                return link['href']
    content = entry.get('summary', '') + entry.get('content', [{'value': ''}])[0]['value']
    img_match = re.search(r'<img[^>]+src=["\'](.*?)["\']', content)
    if img_match:
        return img_match.group(1)
    return None

def is_ng_content(entry, ng_keywords):
    if not ng_keywords:
        return False
    text = (entry.get('title', '') + entry.get('summary', '')).lower()
    for keyword in ng_keywords:
        if keyword.lower() in text:
            return True
    return False

def process_entry(entry, feed_title, feed_link, now_utc):
    """RSSエントリを共通の辞書形式に変換"""
    dt = parse_date(entry)
    is_new = False
    rel_time = ""
    timestamp = 0
    
    if dt:
        if (now_utc - dt).total_seconds() < (new_threshold_hours * 3600):
            is_new = True
        rel_time = format_relative_time(dt, now_utc)
        timestamp = dt.timestamp()
    
    summary = entry.get('summary', entry.get('description', ''))
    content = entry.get('content', [{'value': ''}])[0]['value']
    text_content = content if len(content) > len(summary) else summary
    image_url = extract_image(entry)

    return {
        'title': entry.get('title', 'No Title'),
        'link': entry.get('link', '#'),
        'is_new': is_new,
        'relative_time': rel_time,
        'summary': text_content,
        'image': image_url,
        'timestamp': timestamp,
        'source_title': feed_title # 抽出時に元サイト名を表示するために保持
    }

def fetch_all_feeds(config):
    """設定にある全URLを一度だけ取得して辞書に格納"""
    url_map = {} # url -> feed_data
    
    # 全ページの全URLを収集（重複排除）
    all_urls = set()
    for page in config.get('pages', []):
        for feed in page.get('feeds', []):
            all_urls.add((feed['url'], feed.get('title'))) # URLとタイトルのセット
    
    print(f"Fetching {len(all_urls)} unique feeds...")
    
    now_utc = datetime.datetime.now(pytz.utc)
    
    # 取得処理
    results = {} # url -> {meta_info, entries_list}
    
    for url, title_override in all_urls:
        print(f"  Fetching: {url}...")
        try:
            d = feedparser.parse(url)
            feed_title = title_override if title_override else d.feed.get('title', 'Unknown Feed')
            domain = get_domain(d.feed.get('link', url))
            favicon = f"https://www.google.com/s2/favicons?domain={domain}"
            
            entries = []
            for entry in d.entries[:max_entries]:
                processed = process_entry(entry, feed_title, url, now_utc)
                entries.append(processed)
            
            results[url] = {
                'title': feed_title,
                'favicon': favicon,
                'entries': entries
            }
        except Exception as e:
            print(f"  Error fetching {url}: {e}")
            results[url] = None
            
    return results

def main():
    config = load_config(feeds_file)
    
    # 1. ナビゲーション作成
    navigation = []
    # 通常ページ
    for page in config.get('pages', []):
        navigation.append({'page_title': page['page_title'], 'filename': page['filename']})
    # ウォッチページ
    for watch in config.get('watches', []):
        navigation.append({'page_title': watch['page_title'], 'filename': watch['filename']})
    
    # 2. 全フィード取得
    all_feeds_data = fetch_all_feeds(config)
    
    jst = pytz.timezone('Asia/Tokyo')
    now_str = datetime.datetime.now(jst).strftime('%m/%d %H:%M')
    
    env = Environment(loader=FileSystemLoader('.', encoding='utf-8'))
    template = env.get_template(template_file)
    
    # 3. 通常ページの生成
    for page_config in config.get('pages', []):
        target_filename = page_config['filename']
        print(f"Building Page: {target_filename}")
        
        ng_keywords = page_config.get('ng_keywords', [])
        page_feeds_display = []
        
        for feed_conf in page_config['feeds']:
            url = feed_conf['url']
            source_data = all_feeds_data.get(url)
            
            if source_data:
                # NGフィルタリング
                valid_entries = [e for e in source_data['entries'] if not is_ng_content(e, ng_keywords)]
                
                # 表示用にデータを整形
                has_new = any(e['is_new'] for e in valid_entries)
                
                page_feeds_display.append({
                    'title': source_data['title'],
                    'favicon': source_data['favicon'],
                    'has_new': has_new,
                    'entries': valid_entries
                })
        
        # レンダリング
        with open(target_filename, 'w', encoding='utf-8') as f:
            f.write(template.render(
                navigation=navigation,
                current_page=page_config,
                feeds_data=page_feeds_display,
                last_updated=now_str
            ))

    # 4. ウォッチページの生成
    for watch_config in config.get('watches', []):
        target_filename = watch_config['filename']
        print(f"Building Watch Page: {target_filename}")
        
        keywords = watch_config.get('keywords', [])
        ng_keywords = watch_config.get('ng_keywords', [])
        
        watch_feeds_display = []
        
        # 全取得済みデータから検索
        # キーワードごとに「仮想的なフィード」を作るイメージ
        for kw in keywords:
            matched_entries = []
            
            # 全フィードを走査
            for url, source_data in all_feeds_data.items():
                if not source_data: continue
                
                for entry in source_data['entries']:
                    # NGチェック
                    if is_ng_content(entry, ng_keywords):
                        continue
                        
                    # キーワードマッチ確認 (タイトル or サマリー)
                    text_to_search = (entry['title'] + entry['summary']).lower()
                    if kw.lower() in text_to_search:
                        # 記事を複製してリストに追加
                        matched_entries.append(entry)
            
            # 日付順にソート
            matched_entries.sort(key=lambda x: x['timestamp'], reverse=True)
            
            # 1件以上ヒットしたら表示リストに追加
            if matched_entries:
                has_new = any(e['is_new'] for e in matched_entries)
                # 虫眼鏡アイコンなどをfaviconの代わりに使う
                search_icon = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png" # 簡易的にGoogleロゴを使用（または空文字でも可）
                
                watch_feeds_display.append({
                    'title': f"Keyword: {kw}", # タイトルを「Keyword: Python」のようにする
                    'favicon': '', # アイコンなし
                    'has_new': has_new,
                    'entries': matched_entries
                })

        # レンダリング
        with open(target_filename, 'w', encoding='utf-8') as f:
            f.write(template.render(
                navigation=navigation,
                current_page=watch_config,
                feeds_data=watch_feeds_display,
                last_updated=now_str
            ))

    print("All pages generated successfully.")

if __name__ == "__main__":
    main()