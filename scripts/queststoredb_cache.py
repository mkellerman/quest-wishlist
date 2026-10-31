"""Rebuild Quest Store DB evidence from cached HTML; makes no network requests.

The site's observed Crawl-delay is 15 seconds. This utility deliberately imports
an existing cache instead of starting a thousands-page detail crawl.
"""
import datetime as dt
import html
import json
import pathlib
import re

BASE = 'https://queststoredb.com'


def clean(value):
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', ' ', value)).split())


def date_value(label):
    for fmt, precision, output in [('%b %d, %Y', 'day', '%Y-%m-%d'),
                                    ('%b %Y', 'month', '%Y-%m'), ('%Y', 'year', '%Y')]:
        try:
            value = dt.datetime.strptime(label, fmt)
            return {'value': value.strftime(output), 'precision': precision,
                    'scope': 'quest_store_release_milestone_unspecified', 'raw': label}
        except ValueError:
            pass
    return None


def parse_cards(raw, retrieved_at, index_url):
    """Find every card's own anchor; avoid the old one-card-per-page parser."""
    records = []
    for card in re.finditer(r'href="(/(?:game|app)/[^"?#]+/)"[^>]*>(.*?)</a>', raw, re.S):
        name = re.search(r'<div class="card-title">\s*<div>(.*?)</div>', card[2], re.S)
        ident = re.search(r'-(\d+)/$', card[1])
        if not name or not ident:
            continue
        date = re.search(r'class="card-extra-info-release-date">(.*?)</span>', card[2], re.S)
        label = clean(date[1]) if date else None
        extra = re.search(r'class="card-extra-info">(.*?)</div>', card[2], re.S)
        genres = clean(extra[1]) if extra else ''
        if label and genres.startswith(label):
            genres = genres[len(label):].strip()
        covers = re.findall(r'srcset="(/media/[^" ]*_cover_[^" ]+)"', card[2])
        screenshots = []
        for embedded in re.findall(r'type="application/json">(.*?)</script>', card[2], re.S):
            screenshots.extend(BASE + '/media/' + value['uri'] for value in json.loads(embedded)
                               if isinstance(value, dict) and 'uri' in value)
        records.append({'meta_id': ident[1], 'name': clean(name[1]), 'url': BASE + card[1],
                        'release_month_label': label, 'release_date': date_value(label) if label else None,
                        'genres': [part.strip() for part in genres.split('∙') if part.strip()],
                        'cover_urls': [BASE + value for value in covers], 'screenshot_urls': screenshots,
                        'retrieved_at': retrieved_at, 'retrieval_timestamp_basis': 'cached_html_file_mtime',
                        'source_index_url': index_url})
    return records


def rebuild(cache_dir):
    cache_dir = pathlib.Path(cache_dir)
    paths = sorted(cache_dir.glob('qsdb-index-*.html'), key=lambda p: int(p.stem.rsplit('-', 1)[1]))
    if not paths:
        raise ValueError('No Quest Store DB cached index pages found.')
    first = paths[0].read_text(encoding='utf-8')
    expected = max(map(int, re.findall(r'max="(\d+)"', first)), default=len(paths))
    pages = {int(path.stem.rsplit('-', 1)[1]) for path in paths}
    if set(range(1, expected + 1)) - pages:
        raise ValueError('Quest Store DB cache is missing index pages.')
    records = {}
    for path in paths:
        page = int(path.stem.rsplit('-', 1)[1])
        url = BASE + '/all_apps/?paginate_by=100' + ('&page=' + str(page) if page != 1 else '')
        retrieved = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()
        for record in parse_cards(path.read_text(encoding='utf-8'), retrieved, url):
            previous = records.get(record['meta_id'])
            if previous and previous['name'] != record['name']:
                raise ValueError('Quest Store DB cache has inconsistent duplicate store IDs.')
            records[record['meta_id']] = record
    for path in cache_dir.glob('qsdb-detail-*.html'):
        raw = path.read_text(encoding='utf-8')
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', raw)
        date = re.search(r'<div class="items-center">\s*<span>(.*?)</span>\s*<div class="app-details-release-year">(.*?)</div>\s*<span[^>]*>(.*?)</span>', raw, re.S | re.I)
        ident = re.search(r'-(\d+)/$', canonical[1]) if canonical else None
        if not date or not ident or ident[1] not in records:
            continue
        raw_date = clean(date[2]) + ' ' + clean(date[3])
        value = None
        for fmt in ('%Y %b %d', '%Y %B %d'):
            try:
                value = dt.datetime.strptime(raw_date, fmt).date().isoformat(); break
            except ValueError:
                pass
        records[ident[1]]['detail_release_date'] = {
            'value': value, 'precision': 'day' if value else 'unknown', 'raw': raw_date,
            'source_label': clean(date[1]), 'source_url': canonical[1],
            'retrieved_at': dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()}
    return list(records.values())
