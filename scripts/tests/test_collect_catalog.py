import contextlib
import copy
import io
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from collect_catalog import refresh, vrdb_date
from provider_collectors import CollectionError, IgdbClient, PublicFetcher, load_igdb
from public_catalog import identity_snapshot, public_url, sanitize_catalog, validate_public
from queststoredb_cache import parse_cards
from source_dates import enrich_igdb, parsed_igdb_date, reconcile_dates

QUEST = {'id': 385, 'name': 'Meta Quest'}
META_ID = '1234567890123456'
META_URL = 'https://www.meta.com/experiences/game/' + META_ID + '/'


def seed(title='Example Quest Game', package='org.example.game'):
    return {'schemaVersion': 1, 'games': [{
        'id': 'wishlist-stable-id', 'title': title, 'packageName': package,
        'releases': [{'id': 'release-stable-id', 'index': 9, 'releaseName': title,
                      'versionCode': '123', 'sizeMB': 42, 'updatedAt': '2026-10-01'}],
        'metaUrl': META_URL, 'sources': [], 'questStatus': 'unknown',
        'dateEvidence': [], 'imageCandidates': [], 'screenshots': [],
    }]}


def record(ident=1, title='Example Quest Game', meta=META_ID, releases=()):
    return {'id': ident, 'name': title, 'url': 'https://www.igdb.com/games/example-' + str(ident),
            'platforms': [QUEST], 'websites': [{'url': META_URL.replace(META_ID, meta)}] if meta else [],
            'release_dates': list(releases)}


def release(date, precision='YYYYMMDD', platform=QUEST, milestone='Full Release', ident=1):
    import datetime
    return {'id': ident, 'platform': platform, 'date_format': {'format': precision},
            'date': int(datetime.datetime.fromisoformat(date).replace(tzinfo=datetime.timezone.utc).timestamp()),
            'status': {'name': milestone}, 'release_region': {'region': 'Worldwide'}}


class CredentialTests(unittest.TestCase):
    def test_oauth_uses_https_post_body_and_keeps_token_in_memory(self):
        with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"access_token":"dummy-token"}')) as urlopen:
            client = IgdbClient('dummy-id', 'dummy-secret')
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'https://id.twitch.tv/oauth2/token')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(urllib.parse.parse_qs(request.data.decode())['client_secret'], ['dummy-secret'])
        self.assertNotIn('dummy-secret', request.full_url)
        self.assertEqual(client.access_token, 'dummy-token')
        client.forget()
        self.assertIsNone(client.access_token)
        self.assertIsNone(client.client_id)

    def test_missing_secret_and_authentication_errors_do_not_echo_values(self):
        with mock.patch.dict('os.environ', {'IGDB_CLIENT_ID': 'dummy-secret-value'}, clear=True):
            with self.assertRaises(CollectionError) as caught:
                IgdbClient.from_env()
        self.assertNotIn('dummy-secret-value', str(caught.exception))
        error = urllib.error.HTTPError('https://id.twitch.tv/oauth2/token', 403, 'dummy-secret', {}, io.BytesIO(b'dummy-token'))
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(CollectionError) as caught:
                IgdbClient('dummy-id', 'dummy-secret')
        self.assertEqual(str(caught.exception), 'Twitch authentication failed (HTTP 403).')

    def test_public_fetch_stops_at_rate_limit_without_echoing_body(self):
        fetcher = PublicFetcher.__new__(PublicFetcher)
        fetcher.last_request = 0
        fetcher.minimum_interval = 1.1
        fetcher.robot = mock.Mock()
        fetcher.robot.can_fetch.return_value = True
        error = urllib.error.HTTPError('https://vrdb.app/', 429, 'private-response', {}, io.BytesIO(b'private-body'))
        with mock.patch('urllib.request.urlopen', side_effect=error) as urlopen, mock.patch('time.sleep'):
            with self.assertRaises(CollectionError) as caught:
                fetcher.get('https://vrdb.app/public')
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn('HTTP 429', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))


class PublicCatalogTests(unittest.TestCase):
    def test_downloads_and_nested_credential_fields_are_removed(self):
        private = seed()
        card = private['games'][0]
        card['releases'][0]['downloadUrl'] = 'https://srcdl.example/download/private.apk'
        card['clientSecret'] = 'dummy-secret'
        card['websites'] = [{'label': 'unsafe', 'url': 'https://example.org/' + '#' + 'clientSecret' + '=dummy-secret'}]
        card['collectionStatus'] = {'client_secret': 'dummy-secret', 'igdbMatch': {
            'status': 'refreshed', 'clientSecret': 'dummy-secret', 'method': {'client_secret': 'dummy-secret'}}}
        result = sanitize_catalog(private)
        encoded = json.dumps(result)
        self.assertNotIn('dummy-secret', encoded)
        self.assertNotIn('srcdl', encoded)
        self.assertNotIn('downloadUrl', encoded)
        self.assertEqual(result['publication'], {'visibility': 'public', 'downloadLinksIncluded': False})
        self.assertEqual(identity_snapshot(result), identity_snapshot(sanitize_catalog(seed())))

    def test_query_and_fragment_credentials_are_rejected(self):
        for key in ['clientSecret', 'client_secret', 'access-token', 'apiKey']:
            for separator in ['?', '#', '#/path?']:
                self.assertIsNone(public_url('https://example.org/' + separator + key + '=dummy'))
        self.assertEqual(public_url('//images.igdb.com/cover.jpg'), 'https://images.igdb.com/cover.jpg')
        self.assertIsNone(public_url('https://example.org/game.apk'))
        self.assertIsNone(public_url('https://example.org/download/file'))

    def test_recursive_validator_normalizes_credential_key_spelling(self):
        for key in ['clientSecret', 'client_secret', 'ACCESS-TOKEN', 'download_directory_url']:
            with self.assertRaises(ValueError):
                validate_public({'games': [], 'nested': {'deep': [{key: 'dummy'}]}})


class MatchingTests(unittest.TestCase):
    def test_refresh_preserves_favorite_ids_and_all_release_metadata(self):
        original = seed()
        original['games'][0]['title'] = 'Example  Quest Game'
        original['games'][0]['releases'][0]['releaseName'] = 'Example  Quest Game v123'
        game = record(releases=[release('2024-04-23')])
        game.update(summary='A game.', themes=[{'name': 'Fantasy'}], videos=[{'name': 'Trailer', 'video_id': 'abc123_DEF-0'}])
        result = refresh(original, [game], [QUEST], {'retrieved_at': '2026-10-31'})
        self.assertEqual(identity_snapshot(result), identity_snapshot(sanitize_catalog(original)))
        self.assertEqual(result['totals']['games'], 1)
        self.assertEqual(result['totals']['releases'], 1)
        self.assertEqual(result['games'][0]['questDate'], '2024-04-23')
        self.assertEqual(result['games'][0]['themes'], ['Fantasy'])
        self.assertTrue(any(w['label'].startswith('Video:') for w in result['games'][0]['websites']))

    def test_meta_id_and_catalog_title_contradiction_remains_unresolved(self):
        result = refresh(seed(), [record(1, 'Other Game'), record(2, meta='9999999999999999')], [QUEST])
        match = result['games'][0]['collectionStatus']['igdbMatch']
        self.assertIsNone(match['method'])
        self.assertIn('store_id_and_catalog_title_point_to_different_igdb_records', match['reasons'])
        self.assertFalse(result['games'][0].get('igdbUrl'))

    def test_shared_meta_id_does_not_fall_back_to_an_exact_title(self):
        result = refresh(seed(), [record(1), record(2, 'Another Edition')], [QUEST])
        match = result['games'][0]['collectionStatus']['igdbMatch']
        self.assertIsNone(match['method'])
        self.assertIn('multiple_igdb_records_share_meta_store_id', match['reasons'])

    def test_new_base_game_link_does_not_set_collection_or_renamed_game_date(self):
        for title, base in [('GOLF+', 'Topgolf with Pro Putt'),
                            ('Jurassic World Aftermath Collection', 'Jurassic World Aftermath')]:
            result = refresh(seed(title), [record(title=base, releases=[release('2020-01-01')])], [QUEST])
            self.assertIsNone(result['games'][0].get('questDate'))
            self.assertFalse(result['games'][0].get('igdbUrl'))

    def test_unmarked_demo_package_cannot_match_title_alone(self):
        original = seed(package='org.example.gamedemo')
        original['games'][0].pop('metaUrl')
        result = refresh(original, [record(meta=None)], [QUEST])
        self.assertFalse(result['games'][0].get('igdbUrl'))

    def test_catalog_and_store_titles_cannot_resolve_to_different_games(self):
        vrdb = {'id': META_ID, 'name': 'Other Game', 'slug': 'other-game', 'platforms': ['Meta Quest']}
        result = refresh(seed(), [record(meta=None), record(2, 'Other Game', meta=None)], [QUEST], vrdb_records=[vrdb])
        match = result['games'][0]['collectionStatus']['igdbMatch']
        self.assertIsNone(match['method'])
        self.assertIn('catalog_and_store_titles_point_to_different_igdb_records', match['reasons'])


class DateTests(unittest.TestCase):
    def test_igdb_declared_precision_is_preserved(self):
        for fmt, expected, precision in [('YYYYMMDD', '2024-05-31', 'day'), ('YYYYMM', '2024-05', 'month'),
                                          ('YYYY', '2024', 'year'), ('YYYYQ2', '2024-Q2', 'quarter')]:
            self.assertEqual(parsed_igdb_date(release('2024-05-31', fmt)), {'date': expected, 'precision': precision})

    def test_pc_and_first_release_dates_never_become_quest_dates(self):
        game = record(releases=[release('2016-01-01', platform={'id': 6, 'name': 'PC (Microsoft Windows)'})])
        game['first_release_date'] = game['release_dates'][0]['date']
        _, dates = enrich_igdb(game, {}, {385})
        self.assertEqual(dates, [])
        result = refresh(seed(), [game], [QUEST])
        self.assertIsNone(result['games'][0]['questDate'])

    def test_store_day_does_not_refine_igdb_month(self):
        _, dates = enrich_igdb(record(releases=[release('2024-05-31', 'YYYYMM')]), {}, {385})
        result = reconcile_dates(dates, [{'date': '2024-05-07', 'precision': 'day', 'source': 'VRDB'}])
        self.assertEqual((result['date'], result['precision']), ('2024-05', 'month'))

    def test_early_access_and_full_release_remain_separate(self):
        _, dates = enrich_igdb(record(releases=[release('2024-01-01', milestone='Early Access'),
                                              release('2024-02-01', ident=2)]), {}, {385})
        result = reconcile_dates(dates, [{'date': '2024-02-01', 'precision': 'day', 'source': 'VRDB'}])
        self.assertEqual((result['date'], result['milestone']), ('2024-01-01', 'Early Access'))
        self.assertNotEqual(result['status'], 'conflict')

    def test_prehardware_and_optimization_patch_do_not_establish_launch(self):
        for value, status in [('2018-01-01', 'Full Release'), ('2024-01-01', 'Optimization Patch')]:
            _, dates = enrich_igdb(record(releases=[release(value, milestone=status)]), {}, {385})
            self.assertIsNone(reconcile_dates(dates, [])['date'])

    def test_vrdb_clean_timestamp_does_not_invent_precision(self):
        self.assertEqual(vrdb_date({'release_date_text': 'May 2024', 'release_date_clean': '2024-05-01T00:00:00Z'})['precision'], 'month')
        self.assertIsNone(vrdb_date({'release_date_clean': '2024-05-01T00:00:00Z'})['value'])

    def test_partial_refresh_cannot_clear_existing_conflict_or_warning(self):
        original = seed()
        original['games'][0].update(questStatus='conflict', questDate=None, dateEvidence=[
            {'date': '2024-01-01', 'precision': 'day', 'source': 'IGDB', 'milestone': 'Full Release', 'note': 'Existing scope warning'},
            {'date': '2024-02-01', 'precision': 'day', 'source': 'IGDB', 'milestone': 'Full Release'}])
        vrdb = {'id': META_ID, 'name': 'Example Quest Game', 'slug': 'example', 'platforms': ['Meta Quest'],
                'release_date_text': 'February 1, 2024'}
        game = refresh(original, vrdb_records=[vrdb])['games'][0]
        self.assertEqual(game['questStatus'], 'conflict')
        self.assertIsNone(game['questDate'])
        self.assertIn('Existing scope warning', [item.get('note') for item in game['dateEvidence']])


class CacheTests(unittest.TestCase):
    def test_quest_store_db_parser_captures_every_card(self):
        raw = ''.join('<a href="/game/example-' + str(n) + '/"><div class="card-title"><div>Game ' + str(n) +
                      '</div></div><span class="card-extra-info-release-date">May 2024</span></a>' for n in range(100))
        rows = parse_cards(raw, '2026-10-31', 'https://queststoredb.com/all_apps/')
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[-1]['meta_id'], '99')
        self.assertEqual(rows[0]['release_date']['precision'], 'month')

    def test_igdb_manifest_must_be_complete_and_count_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(temp) / 'igdb-games.json'
            path.write_text(json.dumps([record()]))
            manifest = path.with_name('igdb-provenance.json')
            for invalid in [{'complete': False}, {'complete': True, 'record_count': 2}, {'expected_count_at_start': 2}]:
                manifest.write_text(json.dumps(invalid))
                with self.assertRaises(CollectionError):
                    load_igdb(path)
            manifest.write_text(json.dumps({'complete': True, 'record_count': 1}))
            self.assertEqual(len(load_igdb(path)[0]), 1)
            path.write_text(json.dumps([record(), record()]))
            with self.assertRaises(CollectionError):
                load_igdb(path)


if __name__ == '__main__':
    unittest.main()
