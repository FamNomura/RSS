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
        'source_title': feed_title
    }

def fetch_all_feeds(config):
    url_map = {}
    all_urls = set()
    
    # ページ設定とウォッチ設定の両方からURLを収集
    for page in config.get('pages', []):
        for feed in page.get('feeds', []):
            all_urls.add((feed['url'], feed.get('title')))
    
    # ウォッチリストは既存URLからの抽出が主だが、将来的に独立したURLを指定する場合に備えて探索（現状はpages依存でも可）
    
    print(f"Fetching {len(all_urls)} unique feeds...")
    
    now_utc = datetime.datetime.now(pytz.utc)
    results = {}
    
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
    
    # 1. ナビゲーション作成（★修正点：ウォッチリストを先頭に追加）
    navigation = []
    # ウォッチページ（先頭）
    for watch in config.get('watches', []):
        navigation.append({'page_title': watch['page_title'], 'filename': watch['filename']})
    # 通常ページ（後続）
    for page in config.get('pages', []):
        navigation.append({'page_title': page['page_title'], 'filename': page['filename']})
    
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
                valid_entries = [e for e in source_data['entries'] if not is_ng_content(e, ng_keywords)]
                
                # ★修正点：件数を計算
                total_count = len(valid_entries)
                new_count = sum(1 for e in valid_entries if e['is_new'])
                has_new = new_count > 0
                
                page_feeds_display.append({
                    'title': source_data['title'],
                    'favicon': source_data['favicon'],
                    'has_new': has_new,
                    'total_count': total_count, # 追加
                    'new_count': new_count,     # 追加
                    'entries': valid_entries
                })
        
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
        
        for kw in keywords:
            matched_entries = []
            
            for url, source_data in all_feeds_data.items():
                if not source_data: continue
                
                for entry in source_data['entries']:
                    if is_ng_content(entry, ng_keywords):
                        continue
                    text_to_search = (entry['title'] + entry['summary']).lower()
                    if kw.lower() in text_to_search:
                        matched_entries.append(entry)
            
            matched_entries.sort(key=lambda x: x['timestamp'], reverse=True)
            
            if matched_entries:
                # ★修正点：件数を計算
                total_count = len(matched_entries)
                new_count = sum(1 for e in matched_entries if e['is_new'])
                has_new = new_count > 0
                
                # 虫眼鏡アイコン（簡易）
                search_icon = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_92x30dp.png" 
                
                watch_feeds_display.append({
                    'title': f"Keyword: {kw}",
                    'favicon': '', 
                    'has_new': has_new,
                    'total_count': total_count, # 追加
                    'new_count': new_count,     # 追加
                    'entries': matched_entries
                })

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