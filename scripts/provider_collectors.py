"""Public provider collection; authentication values stay in process memory only."""
import datetime as dt
import gzip
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

USER_AGENT = 'PersonalQuestWishlistCatalog/1.0'
IGDB_FIELDS = ','.join([
    'id', 'name', 'slug', 'url', 'alternative_names.name', 'summary', 'storyline',
    'first_release_date', 'platforms.name', 'release_dates.*',
    'release_dates.platform.name', 'release_dates.date_format.format',
    'release_dates.release_region.region', 'release_dates.status.name', 'release_dates.status.description',
    'genres.name', 'themes.name', 'game_modes.name', 'player_perspectives.name',
    'involved_companies.company.name', 'involved_companies.company.url',
    'involved_companies.developer', 'involved_companies.publisher',
    'cover.url', 'cover.image_id', 'screenshots.url', 'screenshots.image_id',
    'artworks.url', 'artworks.image_id', 'videos.name', 'videos.video_id',
    'websites.url', 'websites.type.type', 'rating', 'rating_count',
    'aggregated_rating', 'aggregated_rating_count', 'total_rating', 'total_rating_count',
    'age_ratings.organization.name', 'age_ratings.rating_category.rating',
    'game_engines.name', 'franchises.name', 'collections.name',
    'external_games.*', 'external_games.external_game_source.name',
    'language_supports.language.name', 'language_supports.language_support_type.name',
    'multiplayer_modes.*', 'updated_at', 'created_at',
])


class CollectionError(Exception):
    """Safe errors contain provider/HTTP status, never request bodies or headers."""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temporary.replace(path)


def quest_platform(name):
    return bool(re.search(r'(?:meta|oculus) quest(?:\s|$)', str(name), re.I))


class IgdbClient:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.access_token = None
        self.last_request = 0.0
        body = urllib.parse.urlencode({'client_id': client_id, 'client_secret': client_secret,
                                       'grant_type': 'client_credentials'}).encode()
        request = urllib.request.Request('https://id.twitch.tv/oauth2/token', data=body,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                self.access_token = json.load(response)['access_token']
        except urllib.error.HTTPError as exc:
            raise CollectionError(f'Twitch authentication failed (HTTP {exc.code}).') from None
        except Exception:
            raise CollectionError('Twitch authentication failed due to a connection or response error.') from None
        finally:
            client_secret = body = request = None

    @classmethod
    def from_env(cls):
        client_id, secret = os.environ.get('IGDB_CLIENT_ID'), os.environ.get('IGDB_CLIENT_SECRET')
        if not client_id or not secret:
            raise CollectionError('Set both IGDB_CLIENT_ID and IGDB_CLIENT_SECRET in GitHub Secrets or the local environment.')
        return cls(client_id, secret)

    def query(self, endpoint, query):
        time.sleep(max(0, .36 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = urllib.request.Request('https://api.igdb.com/v4/' + endpoint,
                    data=query.encode(), method='POST', headers={'Client-ID': self.client_id,
                    'Authorization': 'Bearer ' + self.access_token, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise CollectionError(f'IGDB {endpoint} failed (HTTP {exc.code}); request details are not logged.') from None
        except Exception:
            raise CollectionError(f'IGDB {endpoint} failed due to a connection or response error.') from None

    def forget(self):
        self.client_id = self.access_token = None


def collect_igdb(cache_dir):
    cache_dir = pathlib.Path(cache_dir)
    client = IgdbClient.from_env()
    try:
        platforms = client.query('platforms', 'fields id,name,slug; search "Quest"; limit 100;')
        platforms = [p for p in platforms if quest_platform(p.get('name'))]
        if not platforms:
            raise CollectionError('No Quest platforms resolved; refusing an unfiltered query.')
        ids = {p['id'] for p in platforms}
        filter_ids = ','.join(map(str, sorted(ids)))
        expected = client.query('games/count', f'where platforms = ({filter_ids});')['count']
        records, cursor = [], 0
        while True:
            page = client.query('games', f'fields {IGDB_FIELDS}; where platforms = ({filter_ids}) & id > {cursor}; sort id asc; limit 500;')
            page_ids = [item['id'] for item in page]
            if page_ids and (page_ids != sorted(set(page_ids)) or page_ids[0] <= cursor):
                raise CollectionError('IGDB pagination was not strictly increasing.')
            if any(not ids.intersection(p.get('id') for p in item.get('platforms', [])) for item in page):
                raise CollectionError('IGDB returned a record without Quest platform membership.')
            records.extend(page)
            if len(page) < 500:
                break
            cursor = page_ids[-1]
        if len(records) != expected:
            raise CollectionError('IGDB record count changed during collection; retry later. Public output was not replaced.')
        provenance = {'source': 'IGDB', 'retrieved_at': now(), 'record_count': len(records),
                      'expected_count': expected, 'complete': True, 'quest_platforms': platforms,
                      'credentials_persisted': False, 'documentation_url': 'https://api-docs.igdb.com/'}
        write_json(cache_dir / 'igdb-games.json', records)
        write_json(cache_dir / 'igdb-platforms.json', platforms)
        write_json(cache_dir / 'igdb-provenance.json', provenance)
        return records, platforms, provenance
    finally:
        client.forget()


def load_igdb(path):
    path = pathlib.Path(path)
    records = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(records, dict):
        records = records['games']
    if len(records) != len({item['id'] for item in records}):
        raise CollectionError('IGDB cache contains duplicate record IDs.')
    platforms = {}
    for record in records:
        values = record.get('platforms', []) + [item.get('platform', {}) for item in record.get('release_dates', [])]
        for item in values:
            if isinstance(item, dict) and quest_platform(item.get('name')):
                platforms[item['id']] = item
    if not platforms:
        raise CollectionError('IGDB cache has no expanded Quest platform identities.')
    if any(not set(platforms).intersection(p.get('id') for p in item.get('platforms', [])) for item in records):
        raise CollectionError('IGDB cache contains records without Quest membership.')
    manifest = path.parent / 'igdb-provenance.json'
    provenance = json.loads(manifest.read_text()) if manifest.exists() else {
        'retrieved_at': dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(),
        'timestamp_basis': 'cache_file_mtime'}
    if provenance.get('complete') is False:
        raise CollectionError('IGDB cache manifest marks collection incomplete.')
    for key in ('record_count', 'expected_count', 'expected_count_at_start'):
        if key in provenance and provenance[key] != len(records):
            raise CollectionError('IGDB cache manifest count does not match the records.')
    return records, list(platforms.values()), provenance


def decode_svelte(values):
    memo = {}
    def ref(index):
        if index < 0:
            return None
        if index in memo:
            return memo[index]
        value = values[index]
        if isinstance(value, dict):
            result = {}; memo[index] = result
            result.update({key: ref(child) for key, child in value.items()})
            return result
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                if value[0] == 'Date':
                    return value[1]
                raise CollectionError('VRDB uses an unsupported serialized type.')
            result = []; memo[index] = result
            result.extend(ref(child) for child in value)
            return result
        return value
    return ref(0)


def parse_vrdb(raw):
    for line in raw.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if value.get('type') == 'chunk':
            data = decode_svelte(value['data'])
            if isinstance(data, dict) and 'applications' in data:
                return data
    raise CollectionError('VRDB public response has no application data chunk.')


class PublicFetcher:
    def __init__(self, base_url, minimum_interval=1.1):
        self.base_url, self.last_request = base_url, 0.0
        self.minimum_interval = minimum_interval
        self.robot = urllib.robotparser.RobotFileParser()
        raw = self.get(base_url + '/robots.txt', policy=False)
        self.robot.parse(raw.splitlines())
        self.minimum_interval = max(minimum_interval, self.robot.crawl_delay(USER_AGENT) or self.robot.crawl_delay('*') or 0)

    def get(self, url, policy=True):
        if policy and not self.robot.can_fetch(USER_AGENT, url):
            raise CollectionError('Provider robots policy disallows this request; collection stopped.')
        time.sleep(max(0, self.minimum_interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/sveltekit-data,text/plain'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            raise CollectionError(f'Public provider returned HTTP {exc.code}; collection stopped without retrying access controls.') from None
        except Exception:
            raise CollectionError('Public provider connection or response failed; collection stopped.') from None


def collect_vrdb(cache_dir):
    cache_dir = pathlib.Path(cache_dir)
    fetcher = PublicFetcher('https://vrdb.app')
    records, pages = {}, []
    for page in range(1, 501):
        url = f'https://vrdb.app/genre/all/__data.json?limit=100&page={page}'
        raw = fetcher.get(url)
        data = parse_vrdb(raw)
        retrieved_at = now()
        cache_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(cache_dir / f'vrdb-page-{page:03}.json.gz', 'wt', encoding='utf-8') as output:
            output.write(raw)
        rows = data['applications']
        for item in rows:
            if item['id'] in records:
                raise CollectionError('VRDB repeated an ID during pagination; public output was not replaced.')
            item['_provenance'] = {'source_url': 'https://vrdb.app/game/' + item['slug'] + '/' + item['id'],
                                    'retrieval_url': url, 'retrieved_at': retrieved_at}
            records[item['id']] = item
        pages.append({'page': page, 'retrieved_at': retrieved_at, 'records': len(rows)})
        if len(rows) < 100:
            expected = data['numTotal']
            if len(records) != expected:
                raise CollectionError('VRDB count changed during collection; retry later. Public output was not replaced.')
            result = list(records.values())
            write_json(cache_dir / 'vrdb-apps.json', result)
            write_json(cache_dir / 'vrdb-provenance.json', {'retrieved_at': now(), 'records': len(result), 'expected_count': expected, 'complete': True, 'pages': pages})
            return result
    raise CollectionError('VRDB pagination did not terminate; public output was not replaced.')
