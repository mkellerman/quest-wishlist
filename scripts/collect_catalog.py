#!/usr/bin/env python3
"""Refresh a public, download-free Quest catalog while preserving wishlist IDs.

Examples:
  python scripts/collect_catalog.py --seed site/catalog.json --output site/catalog.json --igdb
  python scripts/collect_catalog.py --seed site/catalog.json --output site/catalog.json --igdb --vrdb
  python scripts/collect_catalog.py --seed site/catalog.json --output .cache/preview.json --igdb-cache .cache/igdb-games.json

IGDB_CLIENT_ID and IGDB_CLIENT_SECRET come from GitHub Secrets/environment. Tokens
are exchanged in an HTTPS POST body and held in memory; no credential file exists.
VRDB collection is optional and observes robots.txt and >=1.1-second spacing.
Quest Store DB imports cached HTML only; its observed crawl delay is 15 seconds.
"""
import argparse
import collections
import copy
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse

from provider_collectors import (CollectionError, collect_igdb, collect_vrdb, load_igdb,
                                 now, quest_platform, write_json)
from public_catalog import identity_snapshot, names, number, public_url, sanitize_catalog, text
from queststoredb_cache import rebuild
from source_dates import enrich_igdb, meta_ids, norm, reconcile_dates, source_date

ROOT = pathlib.Path(__file__).resolve().parent.parent


def identifier_from_url(value, domains):
    url = urllib.parse.urlsplit(value or '')
    host = (url.hostname or '').lower()
    if not any(host == domain or host.endswith('.' + domain) for domain in domains):
        return None
    match = re.search(r'(?:/|-)(\d{10,})(?:/|$)', url.path)
    return match[1] if match else None


def known_store_ids(game):
    values = [identifier_from_url(game.get('metaUrl'), ('meta.com', 'oculus.com')),
              identifier_from_url(game.get('vrdbUrl'), ('vrdb.app',)),
              identifier_from_url(game.get('questStoreDbUrl'), ('queststoredb.com',))]
    return set(filter(None, values))


def source_map(game):
    return {item['source']: item for item in game.get('sources', [])}


def replace_source(game, provider, url, retrieved_at):
    previous = [item for item in game.get('sources', []) if item.get('source') != provider]
    if public_url(url):
        previous.append({'source': provider, 'url': url, 'retrievedAt': retrieved_at})
    game['sources'] = previous


def vrdb_date(row):
    raw, cleaned = row.get('release_date_text'), row.get('release_date_clean')
    result = {'value': None, 'precision': 'unknown', 'raw': raw, 'issues': []}
    if not raw:
        return result
    for fmt, precision, output in [('%B %d, %Y', 'day', '%Y-%m-%d'), ('%b %d, %Y', 'day', '%Y-%m-%d'),
                                   ('%B %Y', 'month', '%Y-%m'), ('%b %Y', 'month', '%Y-%m'), ('%Y', 'year', '%Y')]:
        try:
            value = dt.datetime.strptime(raw.strip(), fmt)
            result.update(value=value.strftime(output), precision=precision); break
        except ValueError:
            pass
    else:
        quarter = re.fullmatch(r'Q([1-4]) (\d{4})', raw.strip(), re.I)
        if quarter:
            result.update(value=quarter[2] + '-Q' + quarter[1], precision='quarter')
        elif re.fullmatch(r'(?:Spring|Summer|Fall|Autumn|Winter) \d{4}', raw.strip(), re.I):
            result.update(value=raw.strip(), precision='season')
    if result['value'] and result['precision'] in {'day', 'month', 'year'} and cleaned and not cleaned.startswith(result['value']):
        result['issues'].append('release_date_text_disagrees_with_cleaned_timestamp')
    return result


def retained_dates(game, exclude=()):
    """Keep unresolved and unrefreshed source evidence, without guessing platforms."""
    sources = source_map(game)
    evidence = []
    for index, item in enumerate(game.get('dateEvidence', [])):
        provider = item.get('source')
        if provider in exclude or (provider == 'Quest Store DB detail' and 'Quest Store DB' in exclude):
            continue
        value = {'date': item.get('date'), 'precision': item.get('precision', 'unknown'),
                 'source': provider, 'source_url': item.get('url'),
                 'retrieved_at': sources.get(provider, {}).get('retrievedAt'),
                 'scope': 'quest_platform_release' if provider == 'IGDB' else 'quest_store_release_milestone_unspecified'}
        if item.get('note'):
            value['scope_warning'] = item['note']
        if provider == 'IGDB':
            # Compact seed evidence omitted platform/region. Distinct unknown
            # scopes must not be fabricated into a same-platform disagreement.
            value['platform'] = {'id': 'retained-scope-' + str(index), 'name': 'Quest (retained source evidence)'}
            value['region'] = None
            value['release_status'] = {'name': item['milestone']} if item.get('milestone') else None
        evidence.append(value)
    if not any(x.get('date') for x in evidence if x['source'] != 'IGDB') and game.get('storeDate') and 'VRDB' not in exclude:
        evidence.append({'date': game['storeDate'], 'precision': game.get('storePrecision', 'unknown'),
                         'source': 'Retained store milestone', 'source_url': game.get('vrdbUrl') or game.get('questStoreDbUrl'),
                         'scope': 'quest_store_release_milestone_unspecified'})
    return evidence


def compact_dates(evidence):
    result, seen = [], set()
    for item in evidence:
        milestone = (item.get('release_status') or {}).get('name')
        key = (item.get('date'), item.get('precision'), item.get('source'), milestone)
        if key in seen:
            continue
        seen.add(key)
        warnings = ([item['scope_warning']] if item.get('scope_warning') else []) + item.get('source_issues', [])
        result.append({'date': item.get('date'), 'precision': item.get('precision'), 'source': item.get('source'),
                       'url': item.get('entity_url') or item.get('source_url'), 'milestone': milestone,
                       **({'note': '; '.join(warnings)} if warnings else {})})
    return result[:8]


def media_url(item):
    return ('https://images.igdb.com/igdb/image/upload/t_1080p/' + item['image_id'] + '.jpg') if item.get('image_id') else public_url(item.get('url'))


def update_metadata(game, igdb, vrdb, qsdb, igdb_manifest, quest_ids):
    # The public seed omits the full platform/region conflict objects. An
    # automated refresh cannot prove that its prior conflict has been resolved;
    # preserve it and its warnings until an explicit evidence review updates it.
    retained_conflict = game.get('questStatus') == 'conflict'
    excluded = [name for name, item in [('IGDB', igdb), ('VRDB', vrdb), ('Quest Store DB', qsdb)] if item]
    evidence = retained_dates(game, () if retained_conflict else excluded)
    new_images, new_shots = [], []
    if igdb:
        enriched, quest_dates = enrich_igdb(igdb, igdb_manifest, quest_ids)
        evidence.extend(quest_dates)
        game['igdbUrl'] = igdb.get('url')
        replace_source(game, 'IGDB', game['igdbUrl'], igdb_manifest.get('retrieved_at'))
        for field, source_field in [('summary', 'summary'), ('storyline', 'storyline')]:
            if igdb.get(source_field): game[field] = text(igdb[source_field], 1500)
        for field, source_field in [('genres', 'genres'), ('themes', 'themes'), ('modes', 'game_modes'),
                                    ('perspectives', 'player_perspectives'), ('developers', 'developers'), ('publishers', 'publishers')]:
            values = names(enriched.get(source_field))
            if values: game[field] = values
        game['igdbRating'], game['igdbRatingCount'] = number(igdb.get('rating')), number(igdb.get('rating_count'))
        ratings = [(item.get('organization') or {}).get('name', '') + ': ' + str((item.get('rating_category') or {}).get('rating', ''))
                   for item in igdb.get('age_ratings', []) if item.get('organization') and item.get('rating_category')]
        if ratings: game['ageRatings'] = ratings
        if igdb.get('cover'):
            new_images.append((media_url(igdb['cover']), 'IGDB'))
        new_shots.extend(media_url(item) for item in igdb.get('screenshots', []))
        game['websites'] = [{'label': (item.get('type') or {}).get('type', 'Website'), 'url': item.get('url')}
                            for item in igdb.get('websites', [])]
        game['websites'].extend({'label': 'Video: ' + item.get('name', 'Trailer'),
                                  'url': 'https://www.youtube.com/watch?v=' + item['video_id']}
                                 for item in igdb.get('videos', []) if re.fullmatch(r'[A-Za-z0-9_-]{6,}', item.get('video_id', '')))
    if vrdb:
        provider = vrdb.get('_provenance', {})
        url = provider.get('source_url') or 'https://vrdb.app/game/' + vrdb['slug'] + '/' + str(vrdb['id'])
        game['vrdbUrl'] = url
        if public_url(vrdb.get('store_link')): game['metaUrl'] = vrdb['store_link']
        replace_source(game, 'VRDB', url, provider.get('retrieved_at'))
        evidence.append(source_date({'release_date': vrdb_date(vrdb), 'source_url': url,
                                      'retrieved_at': provider.get('retrieved_at'),
                                      'release_date_text': vrdb.get('release_date_text')}, 'VRDB'))
        count = number(vrdb.get('rating_count'))
        game['storeRating'], game['storeRatingCount'] = number(vrdb.get('rating_score')) if count else None, count
        if not igdb or not igdb.get('summary'):
            if vrdb.get('description'): game['summary'] = text(vrdb['description'], 1500)
        for field, source_field in [('genres', 'genres'), ('modes', 'game_mode'), ('developers', 'developer'), ('publishers', 'publisher')]:
            if not igdb and vrdb.get(source_field): game[field] = names(vrdb[source_field])
        if vrdb.get('age_rating'):
            game['ageRatings'] = [x for x in game.get('ageRatings', []) if not x.startswith('Meta store:')] + ['Meta store: ' + vrdb['age_rating']]
        for field in ('image_portrait', 'image_square', 'image_landscape'):
            if vrdb.get(field): new_images.append((vrdb[field], 'VRDB'))
        new_shots.extend(item.get('url') for item in vrdb.get('screenshots', []))
    if qsdb:
        game['questStoreDbUrl'] = qsdb['url']
        replace_source(game, 'Quest Store DB', qsdb['url'], qsdb.get('retrieved_at'))
        evidence.append(source_date(qsdb, 'Quest Store DB'))
        detail = qsdb.get('detail_release_date')
        if detail:
            evidence.append(source_date({'release_date': detail, 'source_url': detail.get('source_url'),
                                         'retrieved_at': detail.get('retrieved_at'), 'release_date_text': detail.get('raw')}, 'Quest Store DB detail'))
        new_images.extend((value, 'Quest Store DB') for value in qsdb.get('cover_urls', []))
        new_shots.extend(qsdb.get('screenshot_urls', []))
    quest_evidence = [x for x in evidence if x and x.get('source') == 'IGDB']
    store_evidence = [x for x in evidence if x and x.get('source') != 'IGDB']
    release = reconcile_dates(quest_evidence, store_evidence)
    if retained_conflict:
        release.update(date=None, precision='unknown', milestone=None, status='conflict')
    edition = game.get('questStatus') == 'edition_release_unresolved' or bool(igdb and re.search(r'\bedition\b', game['title'], re.I) and not re.search(r'\bedition\b', igdb['name'], re.I))
    if edition:
        release['date'], release['precision'] = None, 'unknown'
        if release['status'] != 'conflict': release['status'] = 'edition_release_unresolved'
    game.update(questDate=release['date'], questPrecision=release['precision'],
                questMilestone=release['milestone'] if release['date'] else None, questStatus=release['status'])
    milestone = release.get('reported_store_milestone') or {}
    game.update(storeDate=milestone.get('date'), storePrecision=milestone.get('precision', 'unknown'),
                dateEvidence=compact_dates(release['evidence']))
    if retained_conflict: game['dateNote'] = 'Existing release-date conflict retained pending evidence review; initial Quest launch is unresolved.'
    elif release['status'] == 'conflict': game['dateNote'] = 'Conflicting release dates or chronology; initial Quest launch is unresolved.'
    elif edition: game['dateNote'] = 'The named edition’s launch is unresolved; the linked game may describe an older edition.'
    elif release['date']: game['dateNote'] = 'IGDB-reported Quest release' + (' (' + release['milestone'] + ')' if release['milestone'] != 'unspecified' else '') + '.'
    elif milestone.get('date'): game['dateNote'] = 'Store milestone only; this may be App Lab graduation or full release, not initial Quest launch.'
    else: game['dateNote'] = 'Initial Quest launch date unavailable.'
    old_provenance = {item['url']: item.get('source', 'Retained catalog') for item in game.get('imageProvenance', [])}
    new_images.extend((url, old_provenance.get(url, 'Retained catalog')) for url in [game.get('cover')] + game.get('imageCandidates', []))
    selected, seen_providers, seen_urls = [], set(), set()
    # Keep one candidate from each provider before filling remaining slots.
    for url, provider in new_images:
        url = public_url(url)
        if url and url not in seen_urls and provider not in seen_providers:
            selected.append({'url': url, 'source': provider}); seen_urls.add(url); seen_providers.add(provider)
    for url, provider in new_images:
        url = public_url(url)
        if url and url not in seen_urls:
            selected.append({'url': url, 'source': provider}); seen_urls.add(url)
    selected = selected[:3]
    game.update(cover=selected[0]['url'] if selected else None, imageCandidates=[x['url'] for x in selected],
                imageProvenance=selected, hasImage=bool(selected),
                screenshots=list(dict.fromkeys(url for url in map(public_url, new_shots + game.get('screenshots', [])) if url))[:4])


def refresh(seed, igdb_records=None, platforms=None, igdb_manifest=None, vrdb_records=None, qsdb_records=None):
    result = sanitize_catalog(seed)
    identity = identity_snapshot(result)
    games = result['games']
    title_packages = collections.defaultdict(set)
    for game in games: title_packages[norm(game['title'])].add(game['packageName'])
    igdb_records, vrdb_records, qsdb_records = igdb_records or [], vrdb_records or [], qsdb_records or []
    by_url = {item.get('url', '').rstrip('/'): item for item in igdb_records}
    by_igdb_title, by_meta_igdb = collections.defaultdict(dict), collections.defaultdict(dict)
    for item in igdb_records:
        for name in [item['name']] + [x['name'] for x in item.get('alternative_names', [])]: by_igdb_title[norm(name)][item['id']] = item
        for ident in meta_ids(item): by_meta_igdb[ident][item['id']] = item
    vrdb_records = [item for item in vrdb_records if any(quest_platform(name) for name in item.get('platforms', []))]
    by_vrdb = {str(item['id']): item for item in vrdb_records}
    by_qsdb = {str(item['meta_id']): item for item in qsdb_records}
    store_names = collections.defaultdict(set)
    for item in vrdb_records: store_names[norm(item['name'])].add(str(item['id']))
    for item in qsdb_records: store_names[norm(item['name'])].add(str(item['meta_id']))
    matched_igdb = matched_vrdb = 0
    quest_ids = {item['id'] for item in (platforms or [])}
    for game in games:
        title, ids = norm(game['title']), known_store_ids(game)
        generic_block = len(title) < 5 or len(title_packages[title]) > 1 or bool(re.search(r'(demo|trial)(vr)?$', game['packageName'].rsplit('.', 1)[-1], re.I) and not re.search(r'\b(demo|trial)\b', game['title'], re.I))
        if not ids and not generic_block and len(store_names.get(title, set())) == 1: ids = set(store_names[title])
        store_id = next(iter(ids)) if len(ids) == 1 else None
        vrdb, qsdb = by_vrdb.get(store_id), by_qsdb.get(store_id)
        igdb = by_url.get((game.get('igdbUrl') or '').rstrip('/'))
        method = 'existing_catalog_source_url' if igdb else None
        direct = {}
        for ident in ids: direct.update(by_meta_igdb.get(ident, {}))
        named = by_igdb_title.get(title, {})
        reasons = []
        identity_block = False
        if len(ids) > 1:
            identity_block = True; reasons.append('multiple_known_store_ids')
        elif len(direct) > 1:
            identity_block = True; reasons.append('multiple_igdb_records_share_meta_store_id')
        elif direct and named and set(direct).isdisjoint(named):
            identity_block = True; reasons.append('store_id_and_catalog_title_point_to_different_igdb_records')
        if identity_block:
            igdb, method = None, None
        elif not igdb and len(direct) == 1:
            candidate = next(iter(direct.values()))
            if candidate['id'] in named:
                igdb = candidate; method = 'exact_observed_meta_store_id_and_title'
            else:
                identity_block = True
                reasons.append('store_id_without_exact_title_corroboration_may_describe_another_edition')
        if not identity_block and not igdb and not generic_block and len(named) == 1:
            candidate = next(iter(by_igdb_title[title].values()))
            candidate_ids = meta_ids(candidate)
            if not ids or not candidate_ids or not ids.isdisjoint(candidate_ids): igdb = candidate; method = 'unique_exact_normalized_quest_title'
        if igdb and ids and meta_ids(igdb) and ids.isdisjoint(meta_ids(igdb)):
            igdb, method = None, None
            reasons.append('known_store_identity_contradicts_igdb_candidate_links')
        if igdb and method == 'unique_exact_normalized_quest_title':
            store_named = set()
            for row in (vrdb, qsdb):
                if row:
                    store_named.update(by_igdb_title.get(norm(row['name']), {}))
            if store_named and igdb['id'] not in store_named:
                igdb, method = None, None
                reasons.append('catalog_and_store_titles_point_to_different_igdb_records')
        game['collectionStatus'] = {
            'igdbMatch': {'method': method, 'confidence': 'medium' if igdb else None, 'status': 'refreshed' if igdb else 'retained_or_unresolved', 'reasons': reasons},
            'vrdbMatch': 'refreshed_by_store_id' if vrdb else 'retained_or_unresolved',
            'questStoreDbMatch': 'refreshed_by_store_id' if qsdb else 'retained_or_unresolved'}
        if igdb or vrdb or qsdb: update_metadata(game, igdb, vrdb, qsdb, igdb_manifest or {}, quest_ids)
        matched_igdb += bool(igdb); matched_vrdb += bool(vrdb)
    result['generatedAt'] = now()
    result['collection'] = {'retrievedAt': result['generatedAt'], 'igdbRecords': len(igdb_records),
                            'vrdbRecords': len(vrdb_records), 'questStoreDbRecords': len(qsdb_records),
                            'matchedIgdb': matched_igdb, 'matchedVrdb': matched_vrdb,
                            'matchingPolicy': 'Existing observed source identity or unique exact normalized Quest title; no fuzzy matches.'}
    result = sanitize_catalog(result)
    if identity_snapshot(result) != identity:
        raise CollectionError('Refresh changed a game/release identity or original release metadata; public output was not replaced.')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seed', type=pathlib.Path, default=ROOT / 'site/catalog.json')
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'site/catalog.json')
    parser.add_argument('--cache-dir', type=pathlib.Path, default=ROOT / '.cache/catalog')
    parser.add_argument('--igdb', action='store_true', help='Fetch Quest records using IGDB_CLIENT_ID and IGDB_CLIENT_SECRET.')
    parser.add_argument('--vrdb', action='store_true', help='Refresh public VRDB records; honors robots.txt and >=1.1s request spacing.')
    parser.add_argument('--igdb-cache', type=pathlib.Path, help='Use an existing public IGDB game-response JSON instead of the network.')
    parser.add_argument('--vrdb-cache', type=pathlib.Path, help='Use existing public VRDB app-response JSON instead of the network.')
    parser.add_argument('--queststoredb-cache-dir', type=pathlib.Path, help='Rebuild all cards from existing qsdb-index-*.html; no network crawl.')
    args = parser.parse_args(argv)
    if args.igdb and args.igdb_cache: parser.error('Choose --igdb or --igdb-cache, not both.')
    if args.vrdb and args.vrdb_cache: parser.error('Choose --vrdb or --vrdb-cache, not both.')
    try:
        seed = json.loads(args.seed.read_text(encoding='utf-8'))
        igdb, platforms, manifest = (collect_igdb(args.cache_dir) if args.igdb else load_igdb(args.igdb_cache) if args.igdb_cache else (None, None, None))
        vrdb = collect_vrdb(args.cache_dir) if args.vrdb else json.loads(args.vrdb_cache.read_text(encoding='utf-8')) if args.vrdb_cache else None
        qsdb = rebuild(args.queststoredb_cache_dir) if args.queststoredb_cache_dir else None
        result = refresh(seed, igdb, platforms, manifest, vrdb, qsdb)
        write_json(args.output, result)
        print(json.dumps({'ok': True, 'games': result['totals']['games'], 'releases': result['totals']['releases'],
                          'downloadLinksIncluded': False, 'collection': result['collection']}))
        return 0
    except CollectionError as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print('Catalog refresh failed. Public output was not replaced; request and credential details are not logged.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
