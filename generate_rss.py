import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import json
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ========== 配置区域 ==========

PERIODS = ['daily', 'weekly', 'monthly']

LANGUAGES = {
    'all': '',
}

TOPICS = [
    'ai', 'llm', 'machine-learning', 'deep-learning',
    'devops', 'docker', 'kubernetes',
]

WATCH_REPOS = [
    'langchain-ai/langchain',
    'langgenius/dify',
    'ollama/ollama',
    'n8n-io/n8n',
    'vercel/next.js',
    'anthropics/anthropic-cookbook',
    'openai/openai-cookbook',
]

WATCH_ORGS = [
    'langchain-ai',
    'langgenius',
    'ollama',
    'n8n-io',
    'openai',
    'anthropics',
]

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

# ========== 配置结束 ==========


def github_api_headers():
    h = {'User-Agent': 'trending-rss-bot', 'Accept': 'application/vnd.github.v3+json'}
    if GITHUB_TOKEN:
        h['Authorization'] = f'Bearer {GITHUB_TOKEN}'
    return h


def bj_now():
    return (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')


def to_beijing_time(utc_str):
    if not utc_str:
        return ''
    try:
        utc_time = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
        return (utc_time + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    except:
        return utc_str


def parse_stars_num(stars_text):
    if not stars_text:
        return 0
    m = re.search(r'([\d,]+)', stars_text)
    return int(m.group(1).replace(',', '')) if m else 0


# ==================== 1. Trending ====================

def fetch_trending(period='daily', language=''):
    url = f'https://github.com/trending/{language}?since={period}'
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_trending(html, period='daily'):
    soup = BeautifulSoup(html, 'html.parser')
    repos = []
    period_map = {'daily': 'today', 'weekly': 'this week', 'monthly': 'this month'}
    stars_keyword = period_map.get(period, 'today')

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
        for span in article.select('span'):
            text = span.get_text(strip=True)
            if 'stars' in text and stars_keyword in text:
                stars_text = text
                break

        total_stars = ''
        for link in article.select('a'):
            href = link.get('href', '')
            if '/stargazers' in href:
                total_stars = link.get_text(strip=True).replace(',', '').strip()
                break

        repos.append({
            'name': name,
            'description': desc,
            'language': language,
            'stars_text': stars_text,
            'stars_num': parse_stars_num(stars_text),
            'total_stars': total_stars,
            'url': f'https://github.com/{name}',
            'source': f'trending:{period}',
        })

    repos.sort(key=lambda x: x['stars_num'], reverse=True)
    return repos


# ==================== 2. Topics ====================

def fetch_topics(topic):
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
            'stars_num': item.get('stargazers_count', 0),
            'total_stars': str(item.get('stargazers_count', 0)),
            'url': item['html_url'],
            'source': f'topic:{topic}',
            'created_at': to_beijing_time(item.get('created_at', '')),
            'updated_at': to_beijing_time(item.get('updated_at', '')),
        })
    return repos


# ==================== 3. Release ====================

def fetch_releases(repo_name, count=5):
    url = f'https://api.github.com/repos/{repo_name}/releases?per_page={count}'
    resp = requests.get(url, headers=github_api_headers(), timeout=30)
    resp.raise_for_status()
    releases = []
    for r in resp.json():
        releases.append({
            'name': f"{repo_name} {r.get('tag_name', '')}",
            'repo': repo_name,
            'description': (r.get('body', '') or '')[:500],
            'url': r.get('html_url', ''),
            'published_at': to_beijing_time(r.get('published_at', '')),
            'tag': r.get('tag_name', ''),
            'source': 'release',
        })
    return releases


# ==================== 4. 组织动态 ====================

def fetch_org_repos(org_name, count=10):
    url = f'https://api.github.com/orgs/{org_name}/repos?sort=updated&per_page={count}'
    resp = requests.get(url, headers=github_api_headers(), timeout=30)
    resp.raise_for_status()
    repos = []
    for item in resp.json():
        repos.append({
            'name': item['full_name'],
            'description': item.get('description', '') or '',
            'language': item.get('language', '') or '',
            'stars_num': item.get('stargazers_count', 0),
            'total_stars': str(item.get('stargazers_count', 0)),
            'url': item['html_url'],
            'source': f'org:{org_name}',
            'org': org_name,
            'updated_at': to_beijing_time(item.get('updated_at', '')),
        })
    repos.sort(key=lambda x: x['stars_num'], reverse=True)
    return repos



# ==================== 连续上榜（日/周/月出现>=2次） ====================

def detect_consecutive(all_data):
    """统计每个项目在 daily/weekly/monthly 中出现的次数，>=2 就是连续上榜"""
    project_boards = {}  # name -> set of boards

    board_map = {
        'daily': '🔥今日',
        'weekly': '📊本周',
        'monthly': '📈本月',
    }

    for period in ['daily', 'weekly', 'monthly']:
        for repo in all_data.get(period, []):
            name = repo['name']
            if name not in project_boards:
                project_boards[name] = {'boards': set(), 'max_stars': 0}
            project_boards[name]['boards'].add(board_map[period])
            if repo['stars_num'] > project_boards[name]['max_stars']:
                project_boards[name]['max_stars'] = repo['stars_num']

    consecutive = []
    for name, info in project_boards.items():
        if len(info['boards']) >= 2:
            consecutive.append({
                'name': name,
                'boards': sorted(list(info['boards'])),
                'max_stars': info['max_stars'],
            })

    consecutive.sort(key=lambda x: x['max_stars'], reverse=True)
    return consecutive


# ==================== RSS/JSON 生成 ====================

def generate_rss(repos, title, description):
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    ET.SubElement(channel, 'title').text = title
    ET.SubElement(channel, 'description').text = description
    ET.SubElement(channel, 'pubDate').text = bj_now()
    ET.SubElement(channel, 'link').text = 'https://github.com/trending'

    for repo in repos:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = repo.get('name', '')
        ET.SubElement(item, 'link').text = repo.get('url', '')
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

    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding='unicode', xml_declaration=False)
    try:
        dom = minidom.parseString(xml_str)
        return dom.toprettyxml(indent='  ', encoding=None).replace(
            '<?xml version="1.0" ?>\n', '<?xml version="1.0" encoding="UTF-8"?>\n')
    except:
        return xml_str


def generate_json(repos, title):
    return json.dumps({
        'title': title, 'updated': bj_now(), 'count': len(repos), 'items': repos,
    }, ensure_ascii=False, indent=2)


def write_file(content, file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


# ==================== 汇总 summary.json ====================

def generate_summary(all_data, output_dir):
    today = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d')
    consecutive = detect_consecutive(all_data)

    summary = {
        'title': f'GitHub Trending 日报 - {today}',
        'date': today,
        'updated': bj_now(),
        'consecutive': consecutive,
        'trending': {
            'daily': all_data.get('daily', [])[:10],
            'weekly': all_data.get('weekly', [])[:10],
            'monthly': all_data.get('monthly', [])[:10],
        },
        'topics': all_data.get('topics', {}),
        'releases': all_data.get('releases', []),
        'orgs': all_data.get('orgs', []),
    }

    write_file(json.dumps(summary, ensure_ascii=False, indent=2), os.path.join(output_dir, 'summary.json'))
    print(f'  ✅ summary.json | 连续上榜: {len(consecutive)} 个项目')


# ==================== 主函数 ====================

def main():
    output_dir = 'output'
    all_data = {}

    # ===== 1. Trending =====
    print('=' * 50)
    print('📊 Fetching GitHub Trending...')
    print('=' * 50)
    for period in PERIODS:
        for lang_name, lang_path in LANGUAGES.items():
            print(f'  {period}/{lang_name}...')
            try:
                html = fetch_trending(period, lang_path)
                repos = parse_trending(html, period)
                title = f'GitHub {lang_name.capitalize()} {period.capitalize()} Trending'
                rss = generate_rss(repos, title, f'{period.capitalize()} Trending')
                write_file(rss, os.path.join(output_dir, period, f'{lang_name}.xml'))
                write_file(generate_json(repos, title), os.path.join(output_dir, period, f'{lang_name}.json'))
                if lang_name == 'all':
                    all_data[period] = repos
                print(f'    ✅ {len(repos)} repos')
            except Exception as e:
                print(f'    ❌ {e}')

    # ===== 2. Topics =====
    if GITHUB_TOKEN:
        print('\n🏷️  Topics...')
        all_data['topics'] = {}
        for topic in TOPICS:
            try:
                repos = fetch_topics(topic)
                all_data['topics'][topic] = repos[:10]
                write_file(generate_json(repos, f'Topic: {topic}'), os.path.join(output_dir, 'topics', f'{topic}.json'))
                print(f'  ✅ {topic}: {len(repos)} repos')
            except Exception as e:
                print(f'  ❌ {topic}: {e}')

    # ===== 3. Releases =====
    if GITHUB_TOKEN:
        print('\n📦 Releases...')
        all_releases = []
        for repo_name in WATCH_REPOS:
            try:
                releases = fetch_releases(repo_name, count=3)
                all_releases.extend(releases)
                print(f'  ✅ {repo_name}: {len(releases)} releases')
            except Exception as e:
                print(f'  ❌ {repo_name}: {e}')
        all_data['releases'] = all_releases
        if all_releases:
            write_file(generate_json(all_releases, 'Releases'), os.path.join(output_dir, 'releases', 'all.json'))

    # ===== 4. 组织动态 =====
    if GITHUB_TOKEN:
        print('\n🏢 Organizations...')
        all_org_repos = []
        for org in WATCH_ORGS:
            try:
                repos = fetch_org_repos(org, count=5)
                all_org_repos.extend(repos)
                print(f'  ✅ {org}: {len(repos)} repos')
            except Exception as e:
                print(f'  ❌ {org}: {e}')
        all_data['orgs'] = all_org_repos
        if all_org_repos:
            write_file(generate_json(all_org_repos, 'Orgs'), os.path.join(output_dir, 'orgs', 'all.json'))

    # ===== 5. 汇总 =====
    print('\n📋 Summary...')
    generate_summary(all_data, output_dir)

    print('\n✅ All done!')


if __name__ == '__main__':
    main()
