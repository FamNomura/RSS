import json
import feedparser
import datetime
import pytz
import sys
from jinja2 import Environment, FileSystemLoader

# 設定
feeds_file = 'feeds.json'
template_file = 'template.html'
max_entries = 10  # 画像を取得しないので、記事数を増やしても高速です

def load_config(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

def fetch_feed(url, title_override=None):
    print(f"  Fetching: {url}...")
    try:
        d = feedparser.parse(url)
        
        feed_data = {
            'title': title_override if title_override else d.feed.get('title', 'Unknown Feed'),
            'entries': []
        }

        for entry in d.entries[:max_entries]:
            published = entry.get('published', entry.get('updated', ''))
            # descriptionかsummaryの長い方を採用するロジック
            summary = entry.get('summary', entry.get('description', ''))
            content = entry.get('content', [{'value': ''}])[0]['value']
            
            # content（全文に近いもの）があればそれを優先、なければsummaryを使う
            text_content = content if len(content) > len(summary) else summary

            feed_data['entries'].append({
                'title': entry.get('title', 'No Title'),
                'link': entry.get('link', '#'),
                'published': published,
                'summary': text_content  # ここに一番リッチなテキストが入ります
            })
        
        return feed_data
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
    now = datetime.datetime.now(jst).strftime('%Y/%m/%d %H:%M:%S JST')
    
    env = Environment(loader=FileSystemLoader('.', encoding='utf-8'))
    template = env.get_template(template_file)
    
    for page_config in pages_config:
        target_filename = page_config['filename']
        print(f"Building page: {target_filename} ({page_config['page_title']})")
        
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