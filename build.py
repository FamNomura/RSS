import json
import feedparser
import datetime
import pytz
import sys
import time
from urllib.parse import urlparse
from jinja2 import Environment, FileSystemLoader

# 設定
feeds_file = 'feeds.json'
template_file = 'template.html'
max_entries = 10 
new_threshold_hours = 24  # NEWをつける基準（時間）

def load_config(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

def get_domain(url):
    """URLからドメイン名を取得（ファビコン取得用）"""
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except:
        return ""

def parse_date(entry):
    """RSSの日付をdatetimeオブジェクトに変換"""
    # feedparserが解析済みの構造化データ(struct_time)を持っている場合
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime.datetime.fromtimestamp(time.mktime(entry.published_parsed), pytz.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        return datetime.datetime.fromtimestamp(time.mktime(entry.updated_parsed), pytz.utc)
    return None

def format_relative_time(dt_obj, now_utc):
    """現在時刻との差分から相対時間を生成"""
    if not dt_obj:
        return ""
    
    diff = now_utc - dt_obj
    seconds = diff.total_seconds()
    
    if seconds < 3600:
        return f"{int(seconds // 60)}分前"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}時間前"
    elif seconds < 172800: # 48時間以内
        return "昨日"
    else:
        return f"{int(seconds // 86400)}日前"

def fetch_feed(url, title_override=None):
    print(f"  Fetching: {url}...")
    try:
        d = feedparser.parse(url)
        
        feed_title = title_override if title_override else d.feed.get('title', 'Unknown Feed')
        domain = get_domain(d.feed.get('link', url))
        
        # ファビコンURL (GoogleのAPIを使用)
        favicon = f"https://www.google.com/s2/favicons?domain={domain}"

        entries_data = []
        now_utc = datetime.datetime.now(pytz.utc)
        has_new = False # このフィードに新着があるかフラグ

        for entry in d.entries[:max_entries]:
            # 日付処理
            dt = parse_date(entry)
            is_new = False
            rel_time = ""
            
            if dt:
                # NEW判定
                if (now_utc - dt).total_seconds() < (new_threshold_hours * 3600):
                    is_new = True
                    has_new = True
                rel_time = format_relative_time(dt, now_utc)
            
            # コンテンツ処理
            summary = entry.get('summary', entry.get('description', ''))
            content = entry.get('content', [{'value': ''}])[0]['value']
            text_content = content if len(content) > len(summary) else summary

            entries_data.append({
                'title': entry.get('title', 'No Title'),
                'link': entry.get('link', '#'),
                'is_new': is_new,
                'relative_time': rel_time,
                'summary': text_content,
                # ソート用にタイムスタンプを持たせる（日付がない場合は0）
                'timestamp': dt.timestamp() if dt else 0
            })
        
        # 新しい順にソート
        entries_data.sort(key=lambda x: x['timestamp'], reverse=True)

        return {
            'title': feed_title,
            'favicon': favicon,
            'has_new': has_new, # 新着があるフィードを開いたままにするために使う
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