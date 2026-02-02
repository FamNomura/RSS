import json
import feedparser
import datetime
import pytz
import sys
from jinja2 import Environment, FileSystemLoader

# 設定
feeds_file = 'feeds.json'
template_file = 'template.html'
max_entries = 5  # 各フィードからの最大取得記事数

def load_config(path):
    """設定ファイルを読み込む"""
    try:
        # 修正: encoding='utf-8' を 'utf-8-sig' に変更
        # これによりBOM付き/なし両方のUTF-8ファイルを正しく読み込めます
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

def fetch_feed(url, title_override=None):
    """RSSフィードを取得してパースする"""
    print(f"  Fetching: {url}...")
    try:
        d = feedparser.parse(url)
        
        feed_data = {
            'title': title_override if title_override else d.feed.get('title', 'Unknown Feed'),
            'entries': []
        }

        for entry in d.entries[:max_entries]:
            published = entry.get('published', entry.get('updated', ''))
            summary = entry.get('summary', entry.get('description', ''))
            
            feed_data['entries'].append({
                'title': entry.get('title', 'No Title'),
                'link': entry.get('link', '#'),
                'published': published,
                'summary': summary
            })
        
        return feed_data
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None

def main():
    # 1. 設定の読み込み
    pages_config = load_config(feeds_file)
    
    # 2. ナビゲーション情報の作成（テンプレートに渡す用）
    navigation = []
    for page in pages_config:
        navigation.append({
            'page_title': page['page_title'],
            'filename': page['filename']
        })
    
    # 3. 現在時刻 (JST)
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.datetime.now(jst).strftime('%Y/%m/%d %H:%M:%S JST')
    
    # 4. ページごとの生成処理
    env = Environment(loader=FileSystemLoader('.', encoding='utf-8'))
    # テンプレート読み込み時も安全のため utf-8-sig を指定する手もありますが、
    # Jinja2のLoaderはデフォルトutf-8です。通常テンプレートはコードエディタで触るためそのままで行きます。
    template = env.get_template(template_file)
    
    for page_config in pages_config:
        target_filename = page_config['filename']
        print(f"Building page: {target_filename} ({page_config['page_title']})")
        
        page_feeds_data = []
        for feed in page_config['feeds']:
            data = fetch_feed(feed['url'], feed.get('title'))
            if data:
                page_feeds_data.append(data)
        
        # レンダリング
        try:
            html_output = template.render(
                navigation=navigation,
                current_page=page_config,
                feeds_data=page_feeds_data,
                last_updated=now
            )
            
            with open(target_filename, 'w', encoding='utf-8') as f:
                f.write(html_output)
                
        except Exception as e:
            print(f"Error rendering {target_filename}: {e}")
            sys.exit(1)

    print("All pages generated successfully.")

if __name__ == "__main__":
    main()