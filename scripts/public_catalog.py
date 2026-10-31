"""Allowlisted browser catalog schema. Download locations never enter public output."""
import copy
import html
import math
import re
import urllib.parse

GAME_FIELDS = {
    'id', 'title', 'packageName', 'releases', 'cover', 'imageCandidates',
    'imageProvenance', 'screenshots', 'hasImage', 'summary', 'storyline',
    'genres', 'themes', 'modes', 'perspectives', 'developers', 'publishers',
    'storeRating', 'storeRatingCount', 'igdbRating', 'igdbRatingCount',
    'questDate', 'questPrecision', 'questMilestone', 'questStatus',
    'storeDate', 'storePrecision', 'dateNote', 'dateEvidence', 'updatedAt',
    'sizeMB', 'igdbUrl', 'metaUrl', 'vrdbUrl', 'questStoreDbUrl', 'websites',
    'ageRatings', 'sources', 'collectionStatus',
}
RELEASE_FIELDS = {'id', 'index', 'releaseName', 'versionCode', 'sizeMB', 'updatedAt'}
URL_FIELDS = {'cover', 'igdbUrl', 'metaUrl', 'vrdbUrl', 'questStoreDbUrl'}
LIST_FIELDS = {'genres', 'themes', 'modes', 'perspectives', 'developers', 'publishers', 'ageRatings'}
def key_name(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


SENSITIVE_KEYS = {'accesstoken', 'refreshtoken', 'clientsecret', 'apikey', 'password', 'authorization', 'token', 'apitoken', 'githubtoken'}
FORBIDDEN_KEYS = SENSITIVE_KEYS | {'downloadurl', 'downloaddirectoryurl', 'downloadlinks'}


def public_url(value):
    if not isinstance(value, str):
        return None
    if value.startswith('//'):
        value = 'https:' + value
    try:
        url = urllib.parse.urlsplit(value)
        host = (url.hostname or '').lower()
        if url.scheme not in {'https', 'http'} or not host or url.username or url.password:
            return None
        if 'srcdl' in host or host in {'localhost', '127.0.0.1', '::1'}:
            return None
        parameter_text = urllib.parse.unquote(url.query + '&' + url.fragment)
        if any(key_name(part.partition('=')[0]) in SENSITIVE_KEYS for part in re.split(r'[?&#;]', parameter_text)):
            return None
        path = urllib.parse.unquote(url.path).lower()
        if re.search(r'\.(?:apk|obb|zip|rar|7z|exe|dmg|tar|gz)(?:$|/)', path):
            return None
        if re.search(r'/(?:downloads?|mirrors?)(?:/|$)', path):
            return None
        return value
    except ValueError:
        return None


def text(value, limit=None):
    value = '' if value is None else str(value)
    value = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', value)
    value = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', value)
    value = html.unescape(re.sub(r'<[^>]*>', ' ', value))
    value = re.sub(r'https?://\S+', '', value)
    value = re.sub(r'(?:/Users/|/home/|file://)\S+', '', value)
    value = re.sub(r'(?m)^\s*#{1,6}\s+', '', value).replace('**', '').replace('__', '')
    value = re.sub(r'[ \t]+', ' ', value).strip()
    if limit and len(value) > limit:
        value = value[:limit - 1].rsplit(' ', 1)[0] + '…'
    return value


def names(values):
    if isinstance(values, str):
        values = values.split(',')
    return list(dict.fromkeys(text(x.get('name') if isinstance(x, dict) else x) for x in (values or []) if x))


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return (int(value) if value.is_integer() else value) if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def sanitize_catalog(data):
    result = {'schemaVersion': 1, 'generatedAt': data.get('generatedAt'),
              'publication': {'visibility': 'public', 'downloadLinksIncluded': False}, 'games': []}
    for original in data.get('games', []):
        card = {key: copy.deepcopy(value) for key, value in original.items() if key in GAME_FIELDS}
        # Wishlist identifiers and original release metadata are copied exactly.
        # Unsafe identity values fail validation rather than being silently edited.
        for key in ('summary', 'storyline', 'dateNote'):
            if key in card:
                card[key] = text(card[key], 1500 if key in {'summary', 'storyline'} else None)
        card['releases'] = [{key: copy.deepcopy(value)
                             for key, value in release.items() if key in RELEASE_FIELDS}
                            for release in original.get('releases', [])]
        for key in URL_FIELDS:
            if key in card:
                card[key] = public_url(card[key])
        for key in ('imageCandidates', 'screenshots'):
            card[key] = list(dict.fromkeys(url for url in map(public_url, original.get(key, [])) if url))[:4 if key == 'screenshots' else 3]
        if not card.get('cover'):
            card['cover'] = next(iter(card['imageCandidates']), None)
        card['hasImage'] = bool(card['cover'])
        for key in LIST_FIELDS:
            card[key] = names(original.get(key))
        card['imageProvenance'] = [{'url': public_url(item.get('url')), 'source': text(item.get('source'))}
                                   for item in original.get('imageProvenance', []) if public_url(item.get('url'))][:3]
        card['websites'] = [{'label': text(item.get('label')), 'url': public_url(item.get('url'))}
                            for item in original.get('websites', []) if public_url(item.get('url'))]
        card['sources'] = [{'source': text(item.get('source')), 'url': public_url(item.get('url')),
                            'retrievedAt': item.get('retrievedAt')}
                           for item in original.get('sources', []) if public_url(item.get('url'))]
        card['dateEvidence'] = [{key: (public_url(value) if key == 'url' else text(value) if key in {'source', 'milestone', 'note'} and value is not None else value)
                                 for key, value in item.items() if key in {'date', 'precision', 'source', 'url', 'milestone', 'note'}}
                                for item in original.get('dateEvidence', [])][:8]
        if isinstance(original.get('collectionStatus'), dict):
            source_status = original['collectionStatus']
            match = source_status.get('igdbMatch')
            card['collectionStatus'] = {
                key: text(source_status[key]) for key in ('vrdbMatch', 'questStoreDbMatch')
                if isinstance(source_status.get(key), str)}
            if isinstance(match, dict):
                card['collectionStatus']['igdbMatch'] = {
                    key: text(match[key]) if match[key] is not None else None
                    for key in ('method', 'confidence', 'status') if key in match and (match[key] is None or isinstance(match[key], str))}
                if isinstance(match.get('reasons'), list):
                    card['collectionStatus']['igdbMatch']['reasons'] = [text(x) for x in match['reasons'] if isinstance(x, str)]
        else:
            card.pop('collectionStatus', None)
        result['games'].append(card)
    cards = result['games']
    result['totals'] = {'games': len(cards), 'releases': sum(len(g.get('releases', [])) for g in cards),
                        'packages': len({g.get('packageName') for g in cards}),
                        'gamesWithImages': sum(bool(g.get('hasImage')) for g in cards),
                        'gamesWithoutImages': sum(not g.get('hasImage') for g in cards),
                        'releaseEntriesWithImages': sum(len(g.get('releases', [])) for g in cards if g.get('hasImage'))}
    if 'collection' in data:
        result['collection'] = {key: text(data['collection'][key])
                                for key in ('retrievedAt', 'matchingPolicy') if isinstance(data['collection'].get(key), str)}
        result['collection'].update({key: number(data['collection'][key])
                                     for key in ('igdbRecords', 'vrdbRecords', 'questStoreDbRecords', 'matchedIgdb', 'matchedVrdb')
                                     if number(data['collection'].get(key)) is not None})
    validate_public(result)
    return result


def validate_public(data):
    """Fail closed before publication if forbidden URL values or fields survive."""
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key_name(key) in FORBIDDEN_KEYS:
                    raise ValueError('A private location or credential field remains in public output.')
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            if 'srcdl' in value.lower() or '/Users/' in value or 'file://' in value:
                raise ValueError('A private location remains in public output.')
            for url in re.findall(r'https?://[^\s<>"\x27]+', value):
                if not public_url(url):
                    raise ValueError('An unsafe or download URL remains in public output.')
    walk(data)
    cards = data.get('games', [])
    if len(cards) != len({g['id'] for g in cards}):
        raise ValueError('Duplicate game IDs.')
    releases = [r for g in cards for r in g.get('releases', [])]
    if len(releases) != len({r['id'] for r in releases}):
        raise ValueError('Duplicate release IDs.')
    return True


def identity_snapshot(data):
    return [(g['id'], g['title'], g['packageName'], copy.deepcopy(g.get('releases', []))) for g in data['games']]
