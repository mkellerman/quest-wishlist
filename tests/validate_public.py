#!/usr/bin/env python3
"""Fail before publication if private download URLs or credentials reach the site.

Messages identify only paths/field names, never the rejected values. Run from any
directory; --history also examines every blob reachable from repository refs.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse


ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN_KEYS = {
    "downloadurl", "downloaddirectoryurl", "downloadurls", "downloaddirectories",
    "clientsecret", "accesstoken", "refreshtoken", "authorization", "apikey", "password",
}
MIRROR_HOST = re.compile(r"(?:[a-z0-9-]+\.)*srcdl\d*\.xyz\b", re.I)
PRIVATE_PATH = re.compile(r"/(?:Users|home)/[\w.-]+/", re.I)
URL_PATTERN = re.compile(r"https?://[^\s<>\"'\\)]+", re.I)
FORBIDDEN_NAMES = {
    "games-with-download-directories.json", "games-enriched.json", "GameList.txt",
    "store-identities-v1.json", "handoff-location.json", "handoff.py",
}
SKIP_DIRS = {".git", ".cache", "__pycache__", ".pytest_cache", ".venv", "node_modules"}


def normalized_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def private_filename(name):
    return name in FORBIDDEN_NAMES or name == ".env" or name.startswith(".env.")


def text_errors(value, label):
    errors = []
    if MIRROR_HOST.search(value):
        errors.append(f"Private download host found in {label}")
    # Public profile URLs can have the same path components as local paths.
    # Inspect filesystem-looking text after excluding complete web URLs.
    if PRIVATE_PATH.search(URL_PATTERN.sub("", value)):
        errors.append(f"Personal filesystem path found in {label}")
    for match in URL_PATTERN.finditer(value):
        try:
            url = urllib.parse.urlsplit(match.group())
            if url.username or url.password:
                errors.append(f"URL with embedded credentials found in {label}")
            parameters = urllib.parse.parse_qsl(url.query) + urllib.parse.parse_qsl(url.fragment)
            if any(normalized_key(key) in FORBIDDEN_KEYS for key, _ in parameters):
                errors.append(f"Credential URL parameter found in {label}")
        except ValueError:
            errors.append(f"Malformed URL found in {label}")
    return errors


def privacy_errors(value, label="catalog"):
    errors = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_label = f"{label}.{key}"
            if normalized_key(key) in FORBIDDEN_KEYS:
                errors.append(f"Forbidden public field: {child_label}")
            errors.extend(privacy_errors(child, child_label))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(privacy_errors(child, f"{label}[{index}]"))
    elif isinstance(value, str):
        errors.extend(text_errors(value, label))
    return errors


def validate_catalog(catalog):
    errors = privacy_errors(catalog)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("games"), list):
        return errors + ["Catalog must be an object with a games array"]
    games = catalog["games"]
    game_ids, release_ids, indexes, packages = set(), set(), [], set()
    releases = 0
    for index, game in enumerate(games):
        label = f"games[{index}]"
        if not isinstance(game, dict):
            errors.append(f"Invalid game object: {label}")
            continue
        ident = game.get("id")
        if not isinstance(ident, str) or not ident or ident in game_ids:
            errors.append(f"Missing/duplicate game ID: {label}")
        game_ids.add(ident)
        if not isinstance(game.get("title"), str) or not game["title"].strip():
            errors.append(f"Missing game title: {label}")
        package = game.get("packageName")
        if not isinstance(package, str) or not package:
            errors.append(f"Missing package name: {label}")
        packages.add(package)
        rows = game.get("releases")
        if not isinstance(rows, list) or not rows:
            errors.append(f"Missing release metadata: {label}")
            continue
        for release in rows:
            releases += 1
            if not isinstance(release, dict):
                errors.append(f"Invalid release object: {label}")
                continue
            release_id = release.get("id")
            if not isinstance(release_id, str) or not release_id or release_id in release_ids:
                errors.append(f"Missing/duplicate release ID: {label}")
            release_ids.add(release_id)
            entry_index = release.get("index")
            if not isinstance(entry_index, int) or isinstance(entry_index, bool):
                errors.append(f"Invalid source entry index: {label}")
            else:
                indexes.append(entry_index)
    if sorted(indexes) != list(range(releases)):
        errors.append("Source entry indexes must retain every release exactly once")
    totals = catalog.get("totals", {})
    for key, actual in (("games", len(games)), ("releases", releases), ("packages", len(packages))):
        if totals.get(key) != actual:
            errors.append(f"Catalog totals.{key} does not match retained data")
    return errors


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False)


def repository_files(root):
    paths = {path for path in root.rglob("*") if (path.is_file() or path.is_symlink()) and not (set(path.relative_to(root).parts) & SKIP_DIRS)}
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode == 0 and pathlib.Path(top.stdout.strip()).resolve() == root.resolve():
        result = git(root, "ls-files", "-z")
        if result.returncode == 0 and result.stdout:
            paths.update(root / name for name in result.stdout.split("\0") if name)
    return sorted(paths)


def validate_repository(root):
    errors = []
    for path in repository_files(root):
        label = str(path.relative_to(root))
        if path.is_symlink():
            errors.append(f"Public repository must not contain symlinks: {label}")
            continue
        if private_filename(path.name):
            errors.append(f"Private/raw file must not be published: {label}")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            errors.append(f"Unexpected binary/unreadable public source: {label}")
            continue
        if path.suffix == ".json":
            try:
                errors.extend(privacy_errors(json.loads(text), label))
            except json.JSONDecodeError:
                errors.append(f"Invalid JSON: {label}")
        else:
            errors.extend(text_errors(text, label))
    return errors


def validate_history(root):
    errors = []
    objects = git(root, "rev-list", "--objects", "--all")
    if objects.returncode:
        return ["Cannot inspect Git history"]
    for line in objects.stdout.splitlines():
        sha, _, name = line.partition(" ")
        kind = git(root, "cat-file", "-t", sha)
        if kind.stdout.strip() != "blob":
            continue
        result = git(root, "cat-file", "blob", sha)
        if result.returncode:
            errors.append("Cannot inspect a historical blob")
            continue
        label = f"history:{name or sha[:12]}"
        if private_filename(pathlib.PurePosixPath(name).name):
            errors.append(f"Private/raw file in history: {label}")
        if name.endswith(".json"):
            try:
                errors.extend(privacy_errors(json.loads(result.stdout), label))
            except json.JSONDecodeError:
                errors.append(f"Invalid JSON in {label}")
        else:
            errors.extend(text_errors(result.stdout, label))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate_repository(root)
    try:
        catalog = json.loads((root / "site/catalog.json").read_text())
        errors.extend(validate_catalog(catalog))
    except (OSError, json.JSONDecodeError):
        errors.append("Cannot read site/catalog.json")
    if not (root / "site/index.html").is_file():
        errors.append("Missing site/index.html")
    if args.history:
        errors.extend(validate_history(root))
    if errors:
        for error in sorted(set(errors)):
            print("ERROR:", error, file=sys.stderr)
        return 1
    print("Public catalog validation passed: schema, release preservation, no private download links, no credential fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
