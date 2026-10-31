"""Source-separated Quest dates. Adapted from the validated catalog enrichment pipeline."""
import calendar
import collections
import copy
import datetime as dt
import re
import unicodedata
import urllib.parse

def norm(value):
    value = value.translate(str.maketrans('', '', '™®©'))
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().casefold()
    value = value.replace('+', ' plus ').replace('&', ' and ')
    return re.sub(r'[^a-z0-9]', '', value)

def date_range(value, precision):
    if precision == 'day':
        day = dt.date.fromisoformat(value)
        return day, day
    if precision == 'month':
        year, month = map(int, value.split('-'))
        return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])
    if precision == 'year':
        year = int(value)
        return dt.date(year, 1, 1), dt.date(year, 12, 31)
    if precision == 'quarter':
        year, quarter = int(value[:4]), int(value[-1])
        month = (quarter - 1) * 3 + 1
        return dt.date(year, month, 1), dt.date(year, month + 2, calendar.monthrange(year, month + 2)[1])
    return None

def parsed_igdb_date(item):
    """Use the declared IGDB precision, never a timestamp's placeholder day."""
    fmt = item.get('date_format', {})
    fmt = fmt.get('format', '') if isinstance(fmt, dict) else ''
    fmt = str(fmt).upper()
    human = item.get('human', '')
    epoch = item.get('date')
    day = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).date() if epoch is not None else None
    year = item.get('y') or (day.year if day else None)
    month = item.get('m') or (day.month if day else None)
    # Explicit formats come from IGDB's current date_formats table.
    if fmt in {'YYYYMMMMDD', 'YYYYMMDD'}:
        if day:
            return {'date': day.isoformat(), 'precision': 'day'}
        if year and month and item.get('d'):
            try:
                return {'date': dt.date(year, month, item['d']).isoformat(), 'precision':'day'}
            except (ValueError, TypeError):
                pass
        return {'date':None,'precision':'unknown'}
    if fmt in {'YYYYMMMM', 'YYYYMM'} and year and month:
        return {'date': f'{year:04d}-{month:02d}', 'precision': 'month'}
    if fmt == 'YYYY' and year:
        return {'date': f'{year:04d}', 'precision': 'year'}
    quarter = re.fullmatch(r'YYYYQ([1-4])', fmt)
    if quarter and year:
        return {'date': f'{year:04d}-Q{quarter[1]}', 'precision': 'quarter'}
    if 'TBD' in fmt or 'TBA' in str(human).upper():
        return {'date': None, 'precision': 'unknown'}
    # Legacy category values are used only if supplied and no expanded format exists.
    category = item.get('category')
    if not fmt and category is not None:
        formats = {0: 'YYYYMMMMDD', 1: 'YYYYMMMM', 2: 'YYYY', 3: 'YYYYQ1', 4: 'YYYYQ2', 5: 'YYYYQ3', 6: 'YYYYQ4', 7: 'TBD'}
        return parsed_igdb_date({**item, 'date_format': {'format': formats.get(category, 'UNKNOWN')}})
    return {'date': None, 'precision': 'unknown'}

def public_link(value):
    if isinstance(value, str) and value.startswith('//'):
        return 'https:' + value
    if isinstance(value, dict):
        return {key: public_link(child) for key, child in value.items()}
    if isinstance(value, list):
        return [public_link(child) for child in value]
    return value

def meta_ids(record):
    found = set()
    values = [w.get('url', '') for w in record.get('websites', [])]
    values += [x.get('url', '') for x in record.get('external_games', [])]
    for url in values:
        hostname = (urllib.parse.urlsplit(url).hostname or '').lower()
        if any(hostname == domain or hostname.endswith('.'+domain) for domain in ('meta.com','oculus.com')):
            match = re.search(r'/(\d{10,})(?:/|[?#]|$)', url)
            if match:
                found.add(match[1])
    for external in record.get('external_games', []):
        source = external.get('external_game_source', {})
        name = source.get('name', '') if isinstance(source, dict) else ''
        uid = str(external.get('uid', ''))
        if re.search(r'(oculus|meta quest)', name, re.I) and re.fullmatch(r'\d{10,}', uid):
            found.add(uid)
    return found

def enrich_igdb(record, provenance, quest_ids):
    if not record:
        return None, []
    result = public_link(copy.deepcopy(record))
    result['source'] = 'IGDB'
    result['source_url'] = record.get('url')
    result['retrieved_at'] = provenance.get('retrieved_at')
    result['developers'] = [item['company'] for item in record.get('involved_companies', []) if item.get('developer')]
    result['publishers'] = [item['company'] for item in record.get('involved_companies', []) if item.get('publisher')]
    result['official_websites'] = [w['url'] for w in record.get('websites', []) if 'official' in str(w.get('type', {}).get('type', '')).lower()]
    images = ([result['cover']] if result.get('cover') else []) + result.get('screenshots', []) + result.get('artworks', [])
    for media in images:
        if media.get('image_id'):
            media['large_url'] = 'https://images.igdb.com/igdb/image/upload/t_1080p/' + media['image_id'] + '.jpg'
    for video in result.get('videos', []):
        if video.get('video_id'):
            video['url'] = 'https://www.youtube.com/watch?v=' + video['video_id']
    result['rating_scales'] = {'rating': 'IGDB user rating, 0–100', 'aggregated_rating': 'critic rating, 0–100', 'total_rating': 'combined rating, 0–100'}
    normalized_dates, quest = [], []
    for item in record.get('release_dates', []):
        parsed = parsed_igdb_date(item)
        platform = item.get('platform', {})
        platform_id = platform.get('id') if isinstance(platform, dict) else platform
        evidence = {**parsed, 'source': 'IGDB', 'source_url': record.get('url'),
            'retrieved_at': provenance.get('retrieved_at'), 'platform': platform,
            'region': item.get('release_region', item.get('region')), 'release_status': item.get('status'),
            'human': item.get('human'), 'source_date_format': item.get('date_format'),
            'scope': 'quest_platform_release' if platform_id in quest_ids else 'other_platform_release',
            'igdb_release_date_id': item['id']}
        normalized_dates.append(evidence)
        if platform_id in quest_ids:
            quest.append(evidence)
    result['release_dates_normalized'] = normalized_dates
    # Raw first_release_date is retained but never promoted to the Quest release date.
    if record.get('first_release_date') is not None:
        first = dt.datetime.fromtimestamp(record['first_release_date'], dt.timezone.utc).date()
        corresponding = [item for item in normalized_dates if item['date'] and date_range(item['date'], item['precision'])[0] <= first <= date_range(item['date'], item['precision'])[1]]
        weakest = max(corresponding, key=lambda x:(date_range(x['date'],x['precision'])[1]-date_range(x['date'],x['precision'])[0]).days) if corresponding else None
        result['original_release_all_platforms'] = {'source': 'IGDB', 'source_url': record.get('url'),
            'retrieved_at': provenance.get('retrieved_at'), 'scope': 'earliest_recorded_release_across_all_platforms',
            'date': weakest['date'] if weakest else None, 'precision': weakest['precision'] if weakest else 'unknown',
            'timestamp_date': first.isoformat(), 'note': 'Separate from Quest; timestamp is retained without inventing precision if no matching release-date format is available.'}
    return result, quest

def source_date(record, provider):
    if not record:
        return None
    date_obj = record.get('release_date') or record.get('reported_release_date')
    if not isinstance(date_obj, dict):
        return None
    value = date_obj.get('date', date_obj.get('value'))
    return {'date': value, 'precision': date_obj.get('precision', 'unknown'),
        'source': provider, 'source_url': record.get('source_index_url') or record.get('url') or record.get('source_url') or record.get('_provenance', {}).get('source_url'),
        'entity_url': record.get('url') or record.get('source_url') or record.get('_provenance', {}).get('source_url'),
        'retrieval_url': record.get('source_index_url') or record.get('_provenance', {}).get('retrieval_url') or record.get('source_url'),
        'retrieved_at': record.get('retrieved_at') or record.get('_provenance', {}).get('retrieved_at'),
        'scope': 'quest_store_release_milestone_unspecified',
        'human': record.get('release_date_text') or record.get('release_month_label'),
        'source_issues': date_obj.get('issues', [])}

def reconcile_dates(quest_dates, store_dates):
    evidence = quest_dates + [item for item in store_dates if item]
    def phase(item):
        status = item.get('release_status') or {}
        return status.get('name', 'unspecified') if isinstance(status, dict) else 'unspecified'
    def is_launch(item):
        return norm(phase(item)) in {'unspecified','fullrelease','earlyaccess'}
    dated_igdb = [x for x in quest_dates if x.get('date') and date_range(x['date'], x['precision'])]
    known_igdb = [x for x in dated_igdb if is_launch(x)]
    known_store = [x for x in store_dates if x and x.get('date') and date_range(x['date'], x['precision'])]
    conflicts, notes = [], []
    for item in evidence:
        if item.get('source_issues'):
            conflicts.append({'kind':'source_raw_and_cleaned_date_disagree', 'evidence':[item]})
    quest_launch = dt.date(2019,5,21)
    for item in dated_igdb + known_store:
        if date_range(item['date'],item['precision'])[1] < quest_launch:
            conflicts.append({'kind':'quest_date_predates_hardware_public_launch','evidence':[item],
                'basis_url':'https://developers.meta.com/vr/blog/down-the-rabbit-hole-w-oculus-quest-the-hardware-software/',
                'note':'Quest hardware launched May 21, 2019; source date retained without substitution.'})
            item['scope_warning'] = 'Date predates Quest hardware; it may describe a listing or another platform. It does not establish public Quest launch.'
    known_igdb = [x for x in known_igdb if date_range(x['date'],x['precision'])[1] >= quest_launch]
    groups = collections.defaultdict(list)
    for item in known_igdb:
        platform = item['platform']
        groups[(platform.get('id') if isinstance(platform, dict) else platform, str(item.get('region')), phase(item))].append(item)
    for same_scope in groups.values():
        if len(same_scope) > 1 and max(date_range(x['date'],x['precision'])[0] for x in same_scope) > min(date_range(x['date'],x['precision'])[1] for x in same_scope):
            conflicts.append({'kind': 'igdb_same_platform_region_disagreement', 'evidence': same_scope,
                'note':'Same platform, region, and release milestone have incompatible dates.'})
    earliest = min(known_igdb, key=lambda x:date_range(x['date'],x['precision'])[0]) if known_igdb else None
    if len({(x['date'], x['precision']) for x in known_igdb}) > 1:
        notes.append('Distinct Quest platforms, regions, Early Access and Full Release milestones are retained separately.')
    if any(not is_launch(x) for x in dated_igdb):
        notes.append('Alpha, Beta, Advanced Access, Offline, and optimization-patch milestones do not establish initial non-test Quest release.')
    # Compare store dates to a documented release milestone, not necessarily the
    # earliest one: a store's full release can follow a valid early-access launch.
    for store_item in known_store:
        overlaps = [item for item in known_igdb if max(date_range(item['date'],item['precision'])[0],date_range(store_item['date'],store_item['precision'])[0]) <= min(date_range(item['date'],item['precision'])[1],date_range(store_item['date'],store_item['precision'])[1])]
        if overlaps:
            store_item['corroborates_igdb_milestones'] = sorted({phase(item) for item in overlaps})
        elif earliest:
            conflicts.append({'kind':'quest_source_date_or_milestone_disagreement','evidence':[earliest,store_item],
                'note':'Store date does not overlap any documented non-test Quest release milestone; no launch date selected.'})
    if known_store:
        start = max(date_range(x['date'],x['precision'])[0] for x in known_store)
        end = min(date_range(x['date'],x['precision'])[1] for x in known_store)
        if start > end:
            conflicts.append({'kind':'quest_source_date_or_milestone_disagreement','evidence':known_store,
                'note':'Quest store sources have incompatible dates; no launch date selected.'})
    # A store milestone must never refine the precision of an IGDB Quest date.
    initial = earliest if earliest and not conflicts else None
    store_selected = None
    if known_store:
        start = max(date_range(x['date'],x['precision'])[0] for x in known_store)
        end = min(date_range(x['date'],x['precision'])[1] for x in known_store)
        if start <= end:
            store_selected = min(known_store, key=lambda x:(date_range(x['date'],x['precision'])[1]-date_range(x['date'],x['precision'])[0]).days)
    status = 'conflict' if conflicts else ('reported_quest_release' if initial else ('store_milestone_only' if store_selected else 'unknown'))
    result = {'status': status, 'date': initial['date'] if initial else None,
        'precision': initial['precision'] if initial else 'unknown',
        'scope': 'earliest_documented_quest_release' if initial else 'initial_quest_release_unresolved',
        'milestone':phase(initial) if initial else None,
        'reported_store_milestone': {'date': store_selected['date'], 'precision': store_selected['precision'], 'scope': 'quest_store_release_milestone_unspecified'} if store_selected else None,
        'initial_launch_independently_verified': False, 'evidence': evidence, 'conflicts': conflicts, 'notes': notes}
    if store_selected and store_selected.get('scope_warning'):
        result['reported_store_milestone']['scope_warning'] = store_selected['scope_warning']
    if initial and date_range(initial['date'],initial['precision'])[0] > dt.datetime.now(dt.timezone.utc).date():
        result['status'] = 'reported_future_quest_release'
        result['future_release'] = True
    return result
