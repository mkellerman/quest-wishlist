# Quest Wishlist

A public VR game catalog with preview images, search, genre/game-mode filters, Quest release year, rating thresholds, and a personal favorites list.

**Website:** https://mkellerman.github.io/quest-wishlist/

The public snapshot contains 2,979 game cards representing 2,988 releases. It deliberately excludes download-directory URLs from the JSON, interface, and favorites exports. Store pages, official websites, preview images, and metadata-source links remain available.

## Favorites and filters

- Check a game to save it to Favorites in the current browser.
- Export Favorites before moving from the original site, then use Restore on this site. The game IDs are unchanged. Favorites are stored locally and do not synchronize between devices.
- Ratings round down. The **4+** filter requires a store average of at least **4.0**; 3.8 does not qualify. Displayed ratings also truncate rather than round up.
- Quest release year uses resolved Quest release dates only. **Unknown** covers unresolved dates; store dates and original PC/Rift releases are not substituted.

## GitHub Pages

The site is buildless HTML, CSS, JavaScript, and JSON in `site/`. GitHub Actions validates and deploys that directory on pushes to `main`, and can also be run manually. The site has no API credentials or server-side dependency.

For local viewing:

```sh
python3 -m http.server 8000 --directory site
```

Then open http://localhost:8000/.

## Data collection and GitHub Secrets

IGDB authenticates using a Twitch **client ID and client secret**, rather than a single permanent API key. In this repository, open **Settings → Secrets and variables → Actions → New repository secret**, and add:

| Repository secret | Value |
|---|---|
| `IGDB_CLIENT_ID` | The Twitch application's client ID |
| `IGDB_CLIENT_SECRET` | The Twitch application's client secret |

Do not commit these values, an access token, or a credentials file. The collector exchanges the credentials with Twitch over HTTPS and holds the resulting access token only in process memory. Secrets are used by the manual collection workflow and are never bundled into the website.

Once both secrets are configured, run the **Refresh catalog** workflow from the Actions tab. Refreshing is manual; there is no recurring schedule.

The collection CLI is:

```sh
python3 scripts/collect_catalog.py --seed site/catalog.json --output site/catalog.json --igdb
```

Add `--vrdb` to collect supplementary public VRDB metadata. Local raw caches belong in ignored directories; only the sanitized public catalog should be committed. Run `python3 scripts/collect_catalog.py --help` for cached/offline inputs and additional options.

## Data and provenance

Metadata comes from [IGDB](https://www.igdb.com/), [Quest Store DB](https://queststoredb.com/), and [VRDB](https://vrdb.app/). Each matched record retains source links and retrieval timestamps. Images are links to provider-hosted media; missing images use placeholders.

The collectors preserve date precision and separate Quest release milestones from store dates and releases on other platforms. Conflicting dates, uncertain editions, and unresolved matches remain flagged. Favorites and stable catalog IDs survive metadata refreshes.

Download URLs, credentials, original private exports, and the former site's Git history are intentionally absent from this repository.
