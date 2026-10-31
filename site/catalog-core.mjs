// Browser-independent catalog filtering, sorting, and favorite-file handling.
const titleCollator = new Intl.Collator('en', { sensitivity: 'base', numeric: true });
const missing = value => value === null || value === undefined;

function searchText(value) {
  return String(value ?? '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('en').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

function titleOrder(a, b) {
  return compareValues(a.title?.trim() || null, b.title?.trim() || null)
    || String(a.id).localeCompare(String(b.id), 'en');
}

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function ratingBucket(game) {
  const score = finiteNumber(game.storeRating);
  if (score === null || score < 0 || score > 5) return 'none';
  return String(Math.floor(score));
}

export function questYear(game) {
  const stamp = dateBoundary(game.questDate, game.questPrecision, false);
  return stamp === null ? 'unknown' : String(new Date(stamp).getUTCFullYear());
}

function timestamp(value) {
  if (typeof value === 'number') return Number.isFinite(value) && value >= 0 ? value : null;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

// Sorting uses interval boundaries without changing the public source precision.
function dateBoundary(value, precision, latest) {
  if (typeof value !== 'string') return null;
  let year, month, day;
  if (precision === 'day' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    [year, month, day] = value.split('-').map(Number);
    const stamp = Date.UTC(year, month - 1, day);
    const parsed = new Date(stamp);
    return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1
      && parsed.getUTCDate() === day ? stamp : null;
  }
  if (precision === 'month' && /^\d{4}-\d{2}$/.test(value)) {
    [year, month] = value.split('-').map(Number);
    if (month < 1 || month > 12) return null;
    return latest ? Date.UTC(year, month, 0) : Date.UTC(year, month - 1, 1);
  }
  if (precision === 'year' && /^\d{4}$/.test(value)) {
    year = Number(value);
    return latest ? Date.UTC(year, 11, 31) : Date.UTC(year, 0, 1);
  }
  if (precision === 'quarter' && /^\d{4}-Q[1-4]$/.test(value)) {
    year = Number(value.slice(0, 4));
    month = (Number(value.at(-1)) - 1) * 3;
    return latest ? Date.UTC(year, month + 3, 0) : Date.UTC(year, month, 1);
  }
  return null;
}

function compareValues(a, b, descending = false) {
  if (missing(a) && missing(b)) return 0;
  if (missing(a)) return 1;
  if (missing(b)) return -1;
  const result = typeof a === 'string' ? titleCollator.compare(a, b) : a - b;
  return descending ? -result : result;
}

export function filterAndSort(games, options = {}, favoriteSet = new Set(), favoriteAddedAt = {}) {
  const tokens = searchText(options.query).split(/\s+/).filter(Boolean);
  const filtered = games.filter(game => {
    if (options.favoritesOnly && !favoriteSet.has(game.id)) return false;
    if (options.imagesOnly && !game.hasImage) return false;
    if (options.ratings?.length) {
      const bucket = ratingBucket(game);
      if (!options.ratings.some(value => value === 'none' ? bucket === 'none' : bucket !== 'none' && Number(bucket) >= Number(value))) return false;
    }
    if (options.questYear && options.questYear !== 'all' && questYear(game) !== options.questYear) return false;
    if (options.genre && options.genre !== 'all' && !(game.genres ?? []).includes(options.genre)) return false;
    if (options.mode && options.mode !== 'all' && !(game.modes ?? []).includes(options.mode)) return false;
    if (!tokens.length) return true;
    const haystack = searchText([game.title, game.packageName, ...(game.developers ?? []),
      ...(game.genres ?? []), ...(game.themes ?? [])].join(' '));
    return tokens.every(token => haystack.includes(token));
  });
  const sort = options.sort ?? 'title-asc';
  let field, descending = false;
  switch (sort) {
    case 'title-desc': return filtered.sort((a, b) => compareValues(a.title?.trim() || null, b.title?.trim() || null, true) || String(a.id).localeCompare(String(b.id), 'en'));
    case 'quest-newest': descending = true; // fall through
    case 'quest-oldest': field = game => dateBoundary(game.questDate, game.questPrecision, descending); break;
    case 'store-newest': descending = true; // fall through
    case 'store-oldest': field = game => dateBoundary(game.storeDate, game.storePrecision, descending); break;
    case 'rating-high': descending = true; field = game => finiteNumber(game.storeRating); break;
    case 'reviews-high': descending = true; field = game => finiteNumber(game.storeRatingCount); break;
    case 'size-high': descending = true; // fall through
    case 'size-low': field = game => finiteNumber(game.sizeMB); break;
    case 'updated-newest': descending = true; field = game => timestamp(game.updatedAt); break;
    case 'developer-asc': field = game => game.developers?.filter(Boolean).join(', ') || null; break;
    case 'favorited-newest': descending = true; field = game => timestamp(favoriteAddedAt[game.id]); break;
    default: return filtered.sort(titleOrder);
  }
  return filtered.sort((a, b) => compareValues(field(a), field(b), descending) || titleOrder(a, b));
}

function validatedIds(values) {
  if (!Array.isArray(values) || values.some(value => typeof value !== 'string' || !value || value.length > 200)) {
    throw new Error('Favorites must contain an array of game IDs.');
  }
  return [...new Set(values)];
}

function selectedTimes(ids, source) {
  if (source !== undefined && (!source || typeof source !== 'object' || Array.isArray(source))) {
    throw new Error('Favorite dates must be an object.');
  }
  const times = Object.create(null);
  for (const id of ids) {
    if (!Object.hasOwn(source ?? {}, id)) continue;
    const value = timestamp(source[id]);
    if (value === null) throw new Error('Favorite dates must be ISO timestamps or milliseconds.');
    times[id] = value;
  }
  return times;
}

export function parseFavorites(raw, validIds) {
  if (raw === null || raw === undefined) return { ids: new Set(), addedAt: Object.create(null) };
  let value = raw;
  if (typeof raw === 'string') {
    try { value = JSON.parse(raw); }
    catch { throw new Error('This is not a valid favorites JSON file.'); }
  }
  let ids, addedAt;
  if (Array.isArray(value)) {
    ids = validatedIds(value);
  } else if (value && typeof value === 'object' && value.version === 1 && Array.isArray(value.ids)) {
    ids = validatedIds(value.ids);
    addedAt = value.addedAt;
  } else if (value && typeof value === 'object' && value.schemaVersion === 1 && Array.isArray(value.favorites)) {
    ids = validatedIds(value.favorites.map(item => item && typeof item === 'object' ? item.id : null));
    addedAt = value.addedAt;
  } else {
    throw new Error('Choose a favorites export from this catalog.');
  }
  const known = validIds instanceof Set ? validIds : new Set(validIds);
  ids = ids.filter(id => known.has(id));
  return { ids: new Set(ids), addedAt: selectedTimes(ids, addedAt) };
}

export function serializeFavorites(ids, addedAt = {}) {
  const values = validatedIds(Array.from(ids)).sort();
  return JSON.stringify({ version: 1, ids: values, addedAt: selectedTimes(values, addedAt) });
}
