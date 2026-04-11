import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom

PERIODS = ['daily', 'weekly', 'monthly']
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ========== 配置区域 ==========

# 只抓取全部语言
LANGUAGES = {
    'all': '',
}

# 要抓取的 Topics
TOPICS = [
    'ai', 'llm', 'machine-learning', 'deep-learning',
    'devops', 'docker', 'kubernetes',
]

# 要监控 Release 的仓库
WATCH_REPOS = [
    'langchain-ai/langchain',
    'langgenius/dify',
    'ollama/ollama',
    'n8n-io/n8n',
    'vercel/next.js',
    'anthropics/anthropic-cookbook',
    'openai/openai-cookbook',
]

# 要监控的组织
WATCH_ORGS = [
    'langchain-ai',
    'langgenius',
    'ollama',
    'n8n-io',
    'openai',
    'anthropics',
]

# GitHub Token（从环境变量读取，可选）
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

# ========== 配置结束 ==========

def github_api_headers():
    """GitHub API 请求头"""
    h = {
        'User-Agent': 'trending-rss-bot',
        'Accept': 'application/vnd.github.v3+json',
    }
    if GITHUB_TOKEN:
        h['Authorization'] = f'Bearer {GITHUB_TOKEN}'
    return h

def to_beijing_time(utc_str):
    """UTC 时间字符串转北京时间，输出 YYYY-MM-DD HH:MM"""
    if not utc_str:
        return ''
    try:
        utc_time = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
        bj_time = utc_time + timedelta(hours=8)
        return bj_time.strftime("%Y-%m-%d %H:%M")
    except:
        return utc_str

# ==================== 1. Trending 抓取 ====================

def fetch_trending(period='daily', language=''):
    """抓取 GitHub Trending 页面"""
    url = f'https://github.com/trending/{language}?since={period}'
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text

def parse_trending(html, period='daily'):
    """解析 Trending HTML 提取仓库信息"""
    soup = BeautifulSoup(html, 'html.parser')
    repos = []
    
    for article in soup.select('article.Box-row'):
        h2 = article.select_one('h2 a')
        if not h2:
            continue
        name = h2.get('href', '').strip('/')
        if not name or name.count('/') != 1:
            continue
        
        p = article.select_one('p')
        desc = p.get_text(strip=True) if p else ''
        
        lang_span = article.select_one('[itemprop="programmingLanguage"]')
        language = lang_span.get_text(strip=True) if lang_span else ''
        
        stars_text = ''
        for span in article.select('span.d-inline-block.float-sm-right'):
            text = span.get_text(strip=True)
            if 'stars' in text:
                stars_text = text
                break
        
        total_stars = ''
        star_links = article.select('a.Link--muted.d-inline-block.mr-3')
        for link in star_links:
            href = link.get('href', '')
            if '/stargazers' in href:
                total_stars = link.get_text(strip=True).replace(',', '').strip()
                break
        
        repos.append({
            'name': name,
            'description': desc,
            'language': language,
            'stars_text': stars_text,
            'total_stars': total_stars,
            'url': f'https://github.com/{name}',
            'source': 'trending',
        })
    
    return repos

# ==================== 2. Topics 抓取 ====================

def fetch_topics(topic):
    """通过 GitHub API 获取 Topic 下的热门仓库"""
    url = f'https://api.github.com/search/repositories?q=topic:{topic}&sort=stars&order=desc&per_page=25'
    resp = requests.get(url, headers=github_api_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    repos = []
    for item in data.get('items', [])[:25]:
        repos.append({
            'name': item['full_name'],
            'description': item.get('description', '') or '',
            'language': item.get('language', '') or '',
            'stars_text': '',
            'total_stars': str(item.get('stargazers_count', 0)),
            'url': item['html_url'],
            'source': f'topic:{topic}',
            'created_at': to_beijing_time(item.get('created_at', '')),
            'updated_at': to_beijing_time(item.get('updated_at', '')),
        })
    
    return repos

# ==================== 3. Release 监控 ====================

def fetch_releases(repo_name, count=5):
    """获取仓库最新 Release"""
    url = f'https://api.github.com/repos/{repo_name}/releases?per_page={count}'
    resp = requests.get(url, headers=github_api_headers(), timeout=30)
    resp.raise_for_status()
    
    releases = []
    for r in resp.json():
        releases.append({
            'name': f"{repo_name} {r.get('tag_name', '')}",
            'description': (r.get('body', '') or '')[:500],
            'url': r.get('html_url', ''),
            'published_at': to_beijing_time(r.get('published_at', '')),
            'tag': r.get('tag_name', ''),
            'source': 'release',
        })
    
    return releases

# ==================== 4. 组织动态 ====================

def fetch_org_repos(org_name, count=10):
    """获取组织最近更新的仓库"""
    url = f'https://api.github.com/orgs/{org_name}/repos?sort=updated&per_page={count}'
    resp = requests.get(url, headers=github_api_headers(), timeout=30)
    resp.raise_for_status()
    
    repos = []
    for item in resp.json():
        repos.append({
            'name': item['full_name'],
            'description': item.get('description', '') or '',
            'language': item.get('language', '') or '',
            'stars_text': '',
            'total_stars': str(item.get('stargazers_count', 0)),
            'url': item['html_url'],
            'source': f'org:{org_name}',
            'updated_at': to_beijing_time(item.get('updated_at', '')),
        })
    
    return repos

# ==================== 5. 新星项目（最近创建+Star飙升） ====================

def fetch_rising_stars(days=7, min_stars=100):
    """搜索最近N天创建的、Star超过阈值的项目"""
    since_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
    url = f'https://api.github.com/search/repositories?q=created:>{since_date}+stars:>{min_stars}&sort=stars&order=desc&per_page=25'
    resp = requests.get(url, headers=github_api_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    repos = []
    for item in data.get('items', [])[:25]:
        repos.append({
            'name': item['full_name'],
            'description': item.get('description', '') or '',
            'language': item.get('language', '') or '',
            'stars_text': '',
            'total_stars': str(item.get('stargazers_count', 0)),
            'url': item['html_url'],
            'source': 'rising',
            'created_at': to_beijing_time(item.get('created_at', '')),
        })
    
    return repos

# ==================== RSS 生成 ====================

def generate_rss(repos, title, description):
    """生成 RSS XML"""
    bj_now = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M ')
    
    rss = ET.Element('rss', version='2.0')
    
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = title
    ET.SubElement(channel, 'description').text = description
    ET.SubElement(channel, 'pubDate').text = bj_now
    ET.SubElement(channel, 'link').text = 'https://github.com/trending'
    
    for repo in repos:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = repo['name']
        ET.SubElement(item, 'link').text = repo['url']
        
        desc_parts = []
        if repo.get('description'):
            desc_parts.append(f"<p>{repo['description']}</p>")
        if repo.get('language'):
            desc_parts.append(f"<p>Language: {repo['language']}</p>")
        if repo.get('stars_text'):
            desc_parts.append(f"<p>⭐ {repo['stars_text']}</p>")
        if repo.get('total_stars'):
            desc_parts.append(f"<p>Total Stars: {repo['total_stars']}</p>")
        if repo.get('tag'):
            desc_parts.append(f"<p>Version: {repo['tag']}</p>")
        if repo.get('published_at'):
            desc_parts.append(f"<p>Published: {repo['published_at']}</p>")
        
        ET.SubElement(item, 'description').text = '\n'.join(desc_parts)
    
    xml_str = ET.tostring(rss, encoding='unicode', xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    
    try:
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent='  ', encoding=None).replace(
            '<?xml version="1.0" ?>\n', '<?xml version="1.0" encoding="UTF-8"?>\n'
        )
    except:
        return xml_str

def generate_json(repos, title):
    """生成 JSON 格式（方便 n8n 直接读取）"""
    return json.dumps({
        'title': title,
        'updated': to_beijing_time(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')),
        'count': len(repos),
        'items': repos,
    }, ensure_ascii=False, indent=2)

def write_file(content, file_path):
    """写入文件"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

# ==================== 历史数据归档 ====================

def archive_daily(repos, output_dir):
    """每天保存一份数据用于趋势分析"""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    archive_dir = os.path.join(output_dir, 'archive')
    os.makedirs(archive_dir, exist_ok=True)
    
    file_path = os.path.join(archive_dir, f'{today}.json')
    data = {
        'date': today,
        'trending': repos,
    }
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)
    print(f'  📦 Archived -> {file_path}')

# ==================== 连续上榜检测 ====================

def detect_consecutive(repos, output_dir):
    """对比昨天的数据，标记连续上榜项目"""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
    archive_file = os.path.join(output_dir, 'archive', f'{yesterday}.json')
    
    if not os.path.exists(archive_file):
        return []
    
    with open(archive_file, 'r', encoding='utf-8') as f:
        yesterday_data = json.load(f)
    
    yesterday_names = set()
    for r in yesterday_data.get('trending', []):
        yesterday_names.add(r.get('name', ''))
    
    consecutive = []
    for r in repos:
        if r['name'] in yesterday_names:
            consecutive.append(r['name'])
    
    return consecutive

# ==================== 主函数 ====================

def main():
    output_dir = 'output'
    
    # ===== 1. Trending 榜单 =====
    print('=' * 50)
    print('📊 Fetching GitHub Trending...')
    print('=' * 50)
    
    all_daily_repos = []
    
    for period in PERIODS:
        for lang_name, lang_path in LANGUAGES.items():
            print(f'  Fetching {period}/{lang_name}...')
            try:
                html = fetch_trending(period, lang_path)
                repos = parse_trending(html, period)
                
                title = f'GitHub {lang_name.capitalize()} {period.capitalize()} Trending'
                desc = f'{period.capitalize()} Trending of {lang_name} in GitHub'
                
                # RSS
                rss = generate_rss(repos, title, desc)
                write_file(rss, os.path.join(output_dir, period, f'{lang_name}.xml'))
                
                # JSON
                json_content = generate_json(repos, title)
                write_file(json_content, os.path.join(output_dir, period, f'{lang_name}.json'))
                
                if period == 'daily' and lang_name == 'all':
                    all_daily_repos = repos
                
                print(f'  ✅ {len(repos)} repos')
            except Exception as e:
                print(f'  ❌ Error: {e}')
    
    # ===== 2. Topics 热门 =====
    if GITHUB_TOKEN:
        print('\n' + '=' * 50)
        print('🏷️  Fetching GitHub Topics...')
        print('=' * 50)
        
        for topic in TOPICS:
            print(f'  Fetching topic: {topic}...')
            try:
                repos = fetch_topics(topic)
                title = f'GitHub Topic: {topic}'
                
                rss = generate_rss(repos, title, f'Top repositories for topic: {topic}')
                write_file(rss, os.path.join(output_dir, 'topics', f'{topic}.xml'))
                
                json_content = generate_json(repos, title)
                write_file(json_content, os.path.join(output_dir, 'topics', f'{topic}.json'))
                
                print(f'  ✅ {len(repos)} repos')
            except Exception as e:
                print(f'  ❌ Error: {e}')
    
    # ===== 3. Release 监控 =====
    if GITHUB_TOKEN:
        print('\n' + '=' * 50)
        print('📦 Fetching Releases...')
        print('=' * 50)
        
        all_releases = []
        for repo_name in WATCH_REPOS:
            print(f'  Fetching releases: {repo_name}...')
            try:
                releases = fetch_releases(repo_name, count=3)
                all_releases.extend(releases)
                print(f'  ✅ {len(releases)} releases')
            except Exception as e:
                print(f'  ❌ Error: {e}')
        
        if all_releases:
            rss = generate_rss(all_releases, 'Watched Repos Releases', 'Latest releases from watched repositories')
            write_file(rss, os.path.join(output_dir, 'releases', 'all.xml'))
            json_content = generate_json(all_releases, 'Watched Repos Releases')
            write_file(json_content, os.path.join(output_dir, 'releases', 'all.json'))
    
    # ===== 4. 组织动态 =====
    if GITHUB_TOKEN:
        print('\n' + '=' * 50)
        print('🏢 Fetching Organization Updates...')
        print('=' * 50)
        
        all_org_repos = []
        for org in WATCH_ORGS:
            print(f'  Fetching org: {org}...')
            try:
                repos = fetch_org_repos(org, count=5)
                all_org_repos.extend(repos)
                print(f'  ✅ {len(repos)} repos')
            except Exception as e:
                print(f'  ❌ Error: {e}')
        
        if all_org_repos:
            rss = generate_rss(all_org_repos, 'Watched Orgs Updates', 'Latest updates from watched organizations')
            write_file(rss, os.path.join(output_dir, 'orgs', 'all.xml'))
            json_content = generate_json(all_org_repos, 'Watched Orgs Updates')
            write_file(json_content, os.path.join(output_dir, 'orgs', 'all.json'))
    
    # ===== 5. 新星项目 =====
    if GITHUB_TOKEN:
        print('\n' + '=' * 50)
        print('🌟 Fetching Rising Stars...')
        print('=' * 50)
        
        try:
            repos = fetch_rising_stars(days=7, min_stars=100)
            rss = generate_rss(repos, 'GitHub Rising Stars (7d)', 'New repos created in last 7 days with 100+ stars')
            write_file(rss, os.path.join(output_dir, 'rising', 'weekly.xml'))
            json_content = generate_json(repos, 'GitHub Rising Stars (7d)')
            write_file(json_content, os.path.join(output_dir, 'rising', 'weekly.json'))
            print(f'  ✅ {len(repos)} rising stars')
        except Exception as e:
            print(f'  ❌ Error: {e}')
    
    # ===== 6. 历史归档 + 连续上榜检测 =====
    if all_daily_repos:
        print('\n' + '=' * 50)
        print('📦 Archiving & Detecting Consecutive...')
        print('=' * 50)
        
        consecutive = detect_consecutive(all_daily_repos, output_dir)
        if consecutive:
            print(f'  🔥 连续上榜: {", ".join(consecutive)}')
            write_file(
                json.dumps({'date': datetime.utcnow().strftime('%Y-%m-%d'), 'consecutive': consecutive}, ensure_ascii=False, indent=2),
                os.path.join(output_dir, 'consecutive.json')
            )
        
        archive_daily(all_daily_repos, output_dir)
    
    print('\n✅ All done!')

if __name__ == '__main__':
    main()
