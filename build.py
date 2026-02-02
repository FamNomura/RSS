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
    """RSSエントリから安全に画像URLを抽出する"""
    # 1. media_content (多くのニュースサイトで使用)
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'image' in media.get('type', '') or 'medium' in media and media['medium'] == 'image':
                return media['url']
    
    # 2. media_thumbnail (YouTubeや一部ブログ)
    if 'media_thumbnail' in entry:
        return entry.media_thumbnail[0]['url']
    
    # 3. links (enclosureタグ / PodcastやIT系サイト)
    if 'links' in entry:
        for link in entry.links:
            if link.get('rel') == 'enclosure' and 'image' in link.get('type', ''):
                return link['href']
    
    # 4. content/summary内のimgタグ (正規表現でsrcを抽出)
    # ※アクセスは発生しないテキスト処理のみなので安全
    content = entry.get('summary', '') + entry.get('content', [{'value': ''}])[0]['value']
    img_match = re.search(r'<img[^>]+src=["\'](.*?)["\']', content)
    if img_match:
        return img_match.group(1)
        
    return None

def fetch_feed(url, title_override=None):
    print(f"  Fetching: {url}...")
    try:
        d = feedparser.parse(url)
        
        feed_title = title_override if title_override else d.feed.get('title', 'Unknown Feed')
        domain = get_domain(d.feed.get('link', url))
        favicon = f"https://www.google.com/s2/favicons?domain={domain}"

        entries_data = []
        now_utc = datetime.datetime.now(pytz.utc)
        has_new = False

        for entry in d.entries[:max_entries]:
            dt = parse_date(entry)
            is_new = False
            rel_time = ""
            
            if dt:
                if (now_utc - dt).total_seconds() < (new_threshold_hours * 3600):
                    is_new = True
                    has_new = True
                rel_time = format_relative_time(dt, now_utc)
            
            # コンテンツ処理
            summary = entry.get('summary', entry.get('description', ''))
            content = entry.get('content', [{'value': ''}])[0]['value']
            text_content = content if len(content) > len(summary) else summary
            
            # 画像抽出 (ここを追加)
            image_url = extract_image(entry)

            entries_data.append({
                'title': entry.get('title', 'No Title'),
                'link': entry.get('link', '#'),
                'is_new': is_new,
                'relative_time': rel_time,
                'summary': text_content,
                'image': image_url, # 画像URLを追加
                'timestamp': dt.timestamp() if dt else 0
            })
        
        entries_data.sort(key=lambda x: x['timestamp'], reverse=True)

        return {
            'title': feed_title,
            'favicon': favicon,
            'has_new': has_new,
            'entries': entries_data
        }

    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None

def main():
    pages_config = load_config(feeds_file)
    
    navigation = []
    for page in pages_config:
        navigation.append({
            'page_title': page['page_title'],
            'filename': page['filename']
        })
    
    jst = pytz.timezone('Asia/Tokyo')
    now_str = datetime.datetime.now(jst).strftime('%m/%d %H:%M')
    
    env = Environment(loader=FileSystemLoader('.', encoding='utf-8'))
    template = env.get_template(template_file)
    
    for page_config in pages_config:
        target_filename = page_config['filename']
        print(f"Building page: {target_filename}")
        
        page_feeds_data = []
        for feed in page_config['feeds']:
            data = fetch_feed(feed['url'], feed.get('title'))
            if data:
                page_feeds_data.append(data)
        
        try:
            html_output = template.render(
                navigation=navigation,
                current_page=page_config,
                feeds_data=page_feeds_data,
                last_updated=now_str
            )
            
            with open(target_filename, 'w', encoding='utf-8') as f:
                f.write(html_output)
                
        except Exception as e:
            print(f"Error rendering {target_filename}: {e}")
            sys.exit(1)

    print("All pages generated successfully.")

if __name__ == "__main__":
    main()