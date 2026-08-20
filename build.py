import csv
import urllib.request
import io
import feedparser
import datetime
import pytz
import sys
import time
import re
import os
import socket
import traceback
from urllib.parse import urlparse
from jinja2 import Environment, FileSystemLoader

# 設定
csv_url = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTKtl6lGptpOhDEoIbU-C9RkQttsBxbzeILCnxya-do6uPaRIW1xyHBtwH6HsU4ZDpYIhDc05D52mt4/pub?gid=0&single=true&output=csv'
template_file = 'template.html'
output_dir = 'docs'
max_entries = 10 
new_threshold_hours = 24
timeout_seconds = 15

socket.setdefaulttimeout(timeout_seconds)

def write_debug_log(message, error_detail=""):
    try:
        os.makedirs('log', exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join('log', f'debug_log_{now_str}.txt')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
            if error_detail:
                f.write(f"Details:\n{error_detail}\n")
    except Exception as log_err:
        print(f"Failed to write debug log: {log_err}")

def load_config_from_csv(url):
    print("Loading config from Google Sheets CSV...")
    config = {'pages': [], 'watches': []}
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            csv_data = response.read().decode('utf-8-sig')
    except Exception as e:
        err_msg = f"Error fetching CSV: {e}"
        print(err_msg)
        write_debug_log(err_msg, traceback.format_exc())
        sys.exit(1)
        
    reader = csv.reader(io.StringIO(csv_data))
    header = next(reader, None)
    
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
        
        is_hidden = (hidden_str == 'TRUE')
        
        if row_type == 'Page':
            if page_title not in pages_dict:
                pages_dict[page_title] = {
                    'page_title': page_title,
                    'filename': filename,
                    'hidden': is_hidden,
                    'feeds': [],
                    'ng_keywords': []
                }
            if rss_url:
                pages_dict[page_title]['feeds'].append({
                    'title': title_or_kw,
                    'url': rss_url
                })
                
        elif row_type == 'Watch':
            keywords = [k.strip() for k in title_or_kw.split(',') if k.strip()]
            if filename not in watches_dict:
                watches_dict[filename] = {
                    'page_title': page_title,
                    'filename': filename,
                    'hidden': is_hidden,
                    'keywords': keywords,
                    'always_feeds': [],
                    'ng_keywords': []
                }
            else:
                watches_dict[filename]['page_title'] = page_title
                watches_dict[filename]['hidden'] = is_hidden
                watches_dict[filename]['keywords'] = keywords
                
        elif row_type.lower() in ['watch-feed', 'watchfeed', 'watch_feed', 'watch feed']:
            # Watch専用の常時表示フィード
            if filename not in watches_dict:
                watches_dict[filename] = {
                    'page_title': page_title,
                    'filename': filename,
                    'hidden': is_hidden,
                    'keywords': [],
                    'always_feeds': [],
                    'ng_keywords': []
                }
            if rss_url:
                watches_dict[filename]['always_feeds'].append({
                    'title': title_or_kw,
                    'url': rss_url
                })
            
    config['pages'] = list(pages_dict.values())
    config['watches'] = list(watches_dict.values())
    return config

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
    all_urls = set()
    for page in config.get('pages', []):
        for feed in page.get('feeds', []):
            clean_url = feed['url'].strip()
            all_urls.add((clean_url, feed.get('title')))
            
    for watch in config.get('watches', []):
        for feed in watch.get('always_feeds', []):
            clean_url = feed['url'].strip()
            all_urls.add((clean_url, feed.get('title')))
    
    print(f"Fetching {len(all_urls)} unique feeds...")
    now_utc = datetime.datetime.now(pytz.utc)
    results = {}
    user_agent = 'Mozilla/5.0 (compatible; MyRSSReader/1.0)'

    for url, title_override in all_urls:
        print(f"  Fetching: {url}...")
        try:
            d = feedparser.parse(url, agent=user_agent)
            if d.bozo:
                if isinstance(d.bozo_exception, (socket.timeout, socket.error)):
                     raise d.bozo_exception

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
            err_msg = f"Error fetching {url}: {e}"
            print(f"  {err_msg}")
            write_debug_log(err_msg, traceback.format_exc())
            results[url] = None
            
    return results

def main():
    try:
        os.makedirs(output_dir, exist_ok=True)
        config = load_config_from_csv(csv_url)
        
        navigation = []
        for watch in config.get('watches', []):
            if not watch.get('hidden', False):
                navigation.append({'page_title': watch['page_title'], 'filename': watch['filename']})
        for page in config.get('pages', []):
            if not page.get('hidden', False):
                navigation.append({'page_title': page['page_title'], 'filename': page['filename']})
        
        all_feeds_data = fetch_all_feeds(config)
        
        jst = pytz.timezone('Asia/Tokyo')
        now_str = datetime.datetime.now(jst).strftime('%m/%d %H:%M')
        
        env = Environment(loader=FileSystemLoader('.', encoding='utf-8'))
        template = env.get_template(template_file)
        
        # 3. 通常ページの生成
        for page_config in config.get('pages', []):
            target_filename = page_config['filename']
            print(f"Building Page: {target_filename}")
            
            page_config['is_topic'] = False 
            ng_keywords = page_config.get('ng_keywords', [])
            
            page_entries = [] 
            page_feeds = []   
            
            for feed_conf in page_config.get('feeds', []):
                url = feed_conf['url'].strip()
                source_data = all_feeds_data.get(url)
                
                if source_data:
                    valid_entries = [e for e in source_data['entries'] if not is_ng_content(e, ng_keywords)]
                    if valid_entries:
                        for e in valid_entries:
                            e_copy = e.copy()
                            e_copy['favicon'] = source_data['favicon']
                            e_copy['source_title'] = source_data['title']
                            page_entries.append(e_copy)
                        
                        page_feeds.append({
                            'title': source_data['title'],
                            'favicon': source_data['favicon'],
                            'entries': valid_entries,
                            'total_count': len(valid_entries),
                            'new_count': sum(1 for e in valid_entries if e['is_new']),
                            'has_new': any(e['is_new'] for e in valid_entries)
                        })
            
            page_entries.sort(key=lambda x: x['timestamp'], reverse=True)
            
            output_path = os.path.join(output_dir, target_filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(template.render(
                    navigation=navigation,
                    current_page=page_config,
                    entries=page_entries,
                    feeds=page_feeds,
                    topics=[], 
                    last_updated=now_str
                ))

        # 4. ウォッチページの生成
        for watch_config in config.get('watches', []):
            target_filename = watch_config['filename']
            print(f"Building Watch Page: {target_filename}")
            
            watch_config['is_topic'] = True
            keywords = watch_config.get('keywords', [])
            always_feeds = watch_config.get('always_feeds', [])
            ng_keywords = watch_config.get('ng_keywords', [])
            
            watch_entries = []
            watch_topics = [] 
            site_data_dict = {} 
            seen_links = set()
            
            # (1) キーワードによる抽出
            for kw in keywords:
                kw_entries = []
                for url, source_data in all_feeds_data.items():
                    if not source_data: continue
                    
                    for entry in source_data['entries']:
                        if is_ng_content(entry, ng_keywords):
                            continue
                        text_to_search = (entry['title'] + entry['summary']).lower()
                        if kw.lower() in text_to_search:
                            e_copy = entry.copy()
                            e_copy['favicon'] = source_data['favicon']
                            e_copy['source_title'] = source_data['title']
                            
                            kw_entries.append(e_copy)
                            
                            # タイムライン・サイト別への追加（重複排除）
                            if entry['link'] not in seen_links:
                                seen_links.add(entry['link'])
                                watch_entries.append(e_copy)
                                
                                if url not in site_data_dict:
                                    site_data_dict[url] = {
                                        'title': source_data['title'],
                                        'favicon': source_data['favicon'],
                                        'entries': []
                                    }
                                site_data_dict[url]['entries'].append(e_copy)
                                
                if kw_entries:
                    kw_entries.sort(key=lambda x: x['timestamp'], reverse=True)
                    watch_topics.append({
                        'title': f"キーワード: {kw}",
                        'favicon': '',
                        'entries': kw_entries,
                        'total_count': len(kw_entries),
                        'new_count': sum(1 for e in kw_entries if e['is_new']),
                        'has_new': any(e['is_new'] for e in kw_entries)
                    })
            
            # (2) 常時表示フィード（Watch-Feed）の全記事を追加（重複排除）
            for feed_conf in always_feeds:
                url = feed_conf['url'].strip()
                source_data = all_feeds_data.get(url)
                if not source_data:
                    continue
                
                feed_entries = []
                for entry in source_data['entries']:
                    if is_ng_content(entry, ng_keywords):
                        continue
                    e_copy = entry.copy()
                    e_copy['favicon'] = source_data['favicon']
                    e_copy['source_title'] = source_data['title']
                    feed_entries.append(e_copy)
                    
                    # タイムライン・サイト別への追加（キーワードと重複した場合はスキップ）
                    if entry['link'] not in seen_links:
                        seen_links.add(entry['link'])
                        watch_entries.append(e_copy)
                        
                        if url not in site_data_dict:
                            site_data_dict[url] = {
                                'title': source_data['title'],
                                'favicon': source_data['favicon'],
                                'entries': []
                            }
                        site_data_dict[url]['entries'].append(e_copy)
                        
                if feed_entries:
                    feed_entries.sort(key=lambda x: x['timestamp'], reverse=True)
                    display_title = feed_conf.get('title') or source_data['title']
                    watch_topics.append({
                        'title': f"📌 固定: {display_title}",
                        'favicon': source_data['favicon'],
                        'entries': feed_entries,
                        'total_count': len(feed_entries),
                        'new_count': sum(1 for e in feed_entries if e['is_new']),
                        'has_new': any(e['is_new'] for e in feed_entries)
                    })
                
            watch_entries.sort(key=lambda x: x['timestamp'], reverse=True)
            
            watch_feeds = []
            for url, data in site_data_dict.items():
                data['entries'].sort(key=lambda x: x['timestamp'], reverse=True)
                watch_feeds.append({
                    'title': data['title'],
                    'favicon': data['favicon'],
                    'entries': data['entries'],
                    'total_count': len(data['entries']),
                    'new_count': sum(1 for e in data['entries'] if e['is_new']),
                    'has_new': any(e['is_new'] for e in data['entries'])
                })

            output_path = os.path.join(output_dir, target_filename)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(template.render(
                    navigation=navigation,
                    current_page=watch_config,
                    entries=watch_entries,
                    feeds=watch_feeds,
                    topics=watch_topics, 
                    last_updated=now_str
                ))

        print("All pages generated successfully.")
    except Exception as e:
        err_msg = f"Unexpected error in main: {e}"
        print(err_msg)
        write_debug_log(err_msg, traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
