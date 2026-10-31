import { filterAndSort, parseFavorites, serializeFavorites, ratingBucket, questYear } from './catalog-core.mjs';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => new Intl.NumberFormat('en').format(value);
const STORAGE_KEY = 'quest-wishlist:favorites:v1';
const PAGE_SIZE = 60;
const options = {query:'',genre:'',mode:'',questYear:'',ratings:[],imagesOnly:false,favoritesOnly:false,sort:'reviews-high'};
let games = [], byId = new Map(), favorites = new Set(), addedAt = Object.create(null), shown = PAGE_SIZE, matches = [], activeDetail = null, toastTimer;

function safeUrl(value) {
  try { const url = new URL(value); return ['https:','http:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; }
  catch { return ''; }
}
function size(value) { return Number.isFinite(value) ? (value >= 1024 ? `${(value/1024).toFixed(1)} GB` : `${number(value)} MB`) : 'Size unavailable'; }
function date(value, precision='day') {
  if (!value) return 'Unknown';
  if (precision === 'year') return value;
  if (precision === 'quarter') return value.replace(/^(\d{4})-Q([1-4])$/, 'Q$2 $1');
  const stamp = new Date(value + (precision === 'month' ? '-01' : '') + 'T12:00:00Z');
  return Number.isFinite(stamp.getTime()) ? new Intl.DateTimeFormat('en',{year:'numeric',month:'short',...(precision==='month'?{}:{day:'numeric'}),timeZone:'UTC'}).format(stamp) : value;
}
function updated(value) { return value ? date(value.slice(0,10)) : 'Unknown'; }
function ratingText(value, decimals=1) {
  const text = String(value);
  if (text.includes('e')) return (Math.floor(value * 10 ** decimals) / 10 ** decimals).toFixed(decimals);
  const [whole, fraction=''] = text.split('.');
  return `${whole}.${fraction.slice(0,decimals).padEnd(decimals,'0')}`;
}
function placeholder() { return '<div class="art-placeholder"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M8 17c0-3 3-5 6-5h20c3 0 6 2 6 5v13c0 3-3 5-6 5h-5l-5-5-5 5h-5c-3 0-6-2-6-5zM3 21v8m42-8v8M14 19h7m6 0h7"/></svg><span>No preview available</span></div>'; }
function favoriteInput(game, detail=false) {
  return `<label class="${detail?'check-row detail-favorite':'favorite-check'}"><input type="checkbox" data-favorite="${escape(game.id)}" aria-label="Favorite ${escape(game.title)}" ${favorites.has(game.id)?'checked':''}>${detail?'<span>Save to favorites</span>':''}</label>`;
}
function card(game) {
  const image = safeUrl(game.cover);
  const when = game.questDate ? `Quest · ${date(game.questDate,game.questPrecision)}` : game.storeDate ? `Store · ${date(game.storeDate,game.storePrecision)}` : 'Release date unavailable';
  return `<article class="game-card ${favorites.has(game.id)?'is-favorite':''}" data-card="${escape(game.id)}">
    <button class="art-button" data-open="${escape(game.id)}" aria-label="View ${escape(game.title)}">${image?`<img src="${escape(image)}" alt="${escape(game.title)} preview" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-game-image="${escape(game.id)}">`:placeholder()}</button>
    ${favoriteInput(game)}<div class="card-body"><button class="card-title" data-open="${escape(game.id)}">${escape(game.title)}</button>
    <p class="developer">${escape(game.developers?.join(' · ') || 'Developer unavailable')}</p><div class="card-tags"><span class="genre-tag">${escape(game.genres?.[0] || 'Uncategorized')}</span>${Number.isFinite(game.storeRating)?`<span class="rating" title="Meta store rating: ${escape(ratingText(game.storeRating,2))} out of 5; ${number(game.storeRatingCount??0)} reviews"><span class="star" aria-hidden="true">★</span>${ratingText(game.storeRating)}<small>(${game.storeRatingCount===null?'—':number(game.storeRatingCount)})</small></span>`:'<span class="unrated">Unrated</span>'}</div>
    <div class="card-bottom"><span class="card-date" title="${escape(game.dateNote || when)}">${escape(when)}</span><button class="detail-link" data-open="${escape(game.id)}">Details ↗</button></div></div></article>`;
}
function hydrateImages(root) {
  root.querySelectorAll('[data-game-image]').forEach(img => {
    const game = byId.get(img.dataset.gameImage);
    const candidates = [...new Set([game?.cover,...(game?.imageCandidates??[])].map(safeUrl).filter(Boolean))];
    let attempt = Math.max(0,candidates.indexOf(img.src));
    const fail = () => { attempt++; if (attempt<candidates.length) img.src=candidates[attempt]; else { img.onerror=null; img.replaceWith(document.createRange().createContextualFragment(placeholder())); } };
    img.onerror = fail;
    if (img.complete && img.naturalWidth===0) fail();
  });
  root.querySelectorAll('.gallery img').forEach(img=>{ img.onerror=()=>{img.closest('a').hidden=true;}; });
}
function toast(message) {
  clearTimeout(toastTimer); $('toast').textContent=message; $('toast').hidden=false;
  toastTimer=setTimeout(()=>$('toast').hidden=true,3500);
}
function counts() {
  $('favorite-count').textContent=number(favorites.size);
  $('export').disabled=!favorites.size;
}
function persist() {
  try { localStorage.setItem(STORAGE_KEY,serializeFavorites(favorites,addedAt)); $('save-status').textContent='✓ Saved in this browser'; $('storage-warning').hidden=true; }
  catch { $('storage-warning').hidden=false; $('save-status').textContent='Export to keep your favorites'; }
}
function toggleFavorite(id, checked) {
  if (!byId.has(id)) return;
  if (checked) { favorites.add(id); addedAt[id] ??= Date.now(); }
  else { favorites.delete(id); delete addedAt[id]; }
  persist(); counts();
  document.querySelectorAll('[data-favorite]').forEach(input=>{if(input.dataset.favorite===id)input.checked=checked;});
  document.querySelectorAll('[data-card]').forEach(el=>{if(el.dataset.card===id)el.classList.toggle('is-favorite',checked);});
  if(options.favoritesOnly || options.sort==='favorited-newest') refresh(false);
}
function refresh(resetPage=true) {
  if(resetPage)shown=PAGE_SIZE;
  matches=filterAndSort(games,options,favorites,addedAt);
  const ratingCounts = {'5':0,'4':0,'3':0,'2':0,'1':0,none:0};
  const ratingMatches = options.ratings.length ? filterAndSort(games,{...options,ratings:[]},favorites,addedAt) : matches;
  for(const game of ratingMatches){
    const bucket=ratingBucket(game);
    if(bucket==='none')ratingCounts.none++;
    else for(const threshold of ['5','4','3','2','1'])if(Number(bucket)>=Number(threshold))ratingCounts[threshold]++;
  }
  document.querySelectorAll('[data-rating-count]').forEach(el=>el.textContent=number(ratingCounts[el.dataset.ratingCount]));
  const visible=matches.slice(0,shown);
  $('games').innerHTML=visible.map(card).join(''); hydrateImages($('games'));
  $('games').setAttribute('aria-busy','false'); $('empty').hidden=matches.length>0;
  $('empty-title').textContent=options.favoritesOnly&&!favorites.size?'Your wishlist starts here.':'No games found';
  $('empty-copy').textContent=options.favoritesOnly&&!favorites.size?'Check a game in All games. It will be waiting here when you return.':'Try another search or reset your filters.';
  $('empty-reset').textContent=options.favoritesOnly&&!favorites.size?'Browse all games':'Reset filters';
  $('results').innerHTML=`<strong>${number(matches.length)}</strong> ${options.favoritesOnly?'favorite':'game'}${matches.length===1?'':'s'}${options.query||options.genre||options.mode||options.questYear||options.ratings.length||options.imagesOnly?' matching your filters':''}`;
  $('load-more').hidden=shown>=matches.length;
  $('page-note').textContent=matches.length?`${number(visible.length)} of ${number(matches.length)} shown`:'';
}
function view(favoritesOnly) {
  options.favoritesOnly=favoritesOnly;
  $('browse').classList.toggle('active',!favoritesOnly); $('favorites').classList.toggle('active',favoritesOnly);
  $('browse').setAttribute('aria-pressed',String(!favoritesOnly)); $('favorites').setAttribute('aria-pressed',String(favoritesOnly));
  $('view-title').textContent=favoritesOnly?'Made for your next session.':'Your next Quest.';
  $('intro-copy').textContent=favoritesOnly?'Your picks, saved automatically. Export your list when you’re ready.':'Explore the catalog. Check the games you want to play.';
  refresh(); window.scrollTo({top:0,behavior:'instant'});
}
function reset() {
  options.query='';options.genre='';options.mode='';options.questYear='';options.ratings=[];options.imagesOnly=false;
  document.querySelectorAll('[data-rating]').forEach(input=>input.checked=false);
  $('search').value='';$('genre').value='';$('mode').value='';$('quest-year').value='';$('images-only').checked=false;refresh();
}
function links(game) {
  const source=[['Meta store',game.metaUrl],['IGDB',game.igdbUrl],['VRDB',game.vrdbUrl],['Quest Store DB',game.questStoreDbUrl]];
  const official=(game.websites??[]).filter(w=>/official/i.test(w.label??'')).slice(0,1);
  source.push(...official.map(w=>['Official website',w.url]));
  return source.filter(([,url])=>safeUrl(url)).map(([label,url])=>`<a href="${escape(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${escape(label)} ↗</a>`).join('');
}
function openDetails(id,updateHash=true) {
  const game=byId.get(id);if(!game)return;activeDetail=id;
  const image=safeUrl(game.cover);const questLabel=game.questDate?`${date(game.questDate,game.questPrecision)}${game.questMilestone&&game.questMilestone!=='unspecified'?` · ${game.questMilestone}`:''}`:'Unresolved';
  const fact=(title,value)=>`<div><dt>${escape(title)}</dt><dd>${escape(value)}</dd></div>`;
  $('detail-content').innerHTML=`<div class="detail-header">${image?`<img class="detail-art" src="${escape(image)}" alt="${escape(game.title)} cover" referrerpolicy="no-referrer" data-game-image="${escape(id)}">`:`<div class="detail-art">${placeholder()}</div>`}<div><div class="eyebrow accent">YOUR NEXT WORLD</div><h2 class="detail-title" id="detail-title">${escape(game.title)}</h2><p class="detail-developer">${escape(game.developers?.join(' · ') || 'Developer unavailable')}</p><div class="detail-tags">${(game.genres??[]).slice(0,6).map(g=>`<span class="genre-tag">${escape(g)}</span>`).join('')}</div>${favoriteInput(game,true)}</div></div>
    <div class="detail-inner"><h3>About this game</h3><p class="description">${escape(game.summary||'No description is available for this entry yet.')}</p>
    <dl class="detail-facts">${fact('Quest release',questLabel)}${fact('Store date',game.storeDate?date(game.storeDate,game.storePrecision):'Unavailable')}${fact('Store rating',Number.isFinite(game.storeRating)?`${ratingText(game.storeRating,2)} / 5${Number.isFinite(game.storeRatingCount)?` · ${number(game.storeRatingCount)} reviews`:''}`:'Unrated')}${fact('Game modes',game.modes?.join(', ')||'Unavailable')}${fact('Size',size(game.sizeMB))}${fact('Last updated',updated(game.updatedAt))}${fact('Publishers',game.publishers?.join(', ')||'Unavailable')}${fact('Age ratings',game.ageRatings?.join(' · ')||'Unavailable')}${Number.isFinite(game.igdbRating)?fact('IGDB rating',`${game.igdbRating.toFixed(1)} / 100${Number.isFinite(game.igdbRatingCount)?` · ${number(game.igdbRatingCount)} ratings`:''}`):''}</dl>
    <div class="date-note">${escape(game.dateNote||'The initial Quest release date is unresolved.')} Store dates can reflect early access or a later full release. Last updated describes the catalog file, not the game’s launch.</div>
    ${(game.screenshots??[]).length?`<h3>Take a closer look</h3><div class="gallery">${game.screenshots.filter(safeUrl).map((url,i)=>`<a href="${escape(safeUrl(url))}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escape(game.title)} screenshot ${i+1}"><img src="${escape(safeUrl(url))}" alt="${escape(game.title)} screenshot ${i+1}" loading="lazy" referrerpolicy="no-referrer"></a>`).join('')}</div>`:''}
    <h3>Catalog ${game.releases.length>1?'releases':'release'}</h3><div class="releases">${game.releases.map(release=>`<div class="release-row"><div><strong>${escape(release.releaseName)}</strong><small>Version ${escape(release.versionCode)} · ${escape(size(release.sizeMB))} · Updated ${escape(updated(release.updatedAt))}</small></div></div>`).join('')}</div>
    <div class="source-links">${links(game)}</div><p class="package">${escape(game.packageName)}</p></div>`;
  hydrateImages($('detail-content'));
  if(!$('details').open)$('details').showModal();
  if(updateHash)history.replaceState(null,'',`#game/${encodeURIComponent(id)}`);
}
function exportFavorites() {
  const selected=games.filter(game=>favorites.has(game.id));
  const data={schemaVersion:1,exportedAt:new Date().toISOString(),addedAt,
    favorites:selected.map(game=>({id:game.id,title:game.title,packageName:game.packageName,questDate:game.questDate,questPrecision:game.questPrecision,storeDate:game.storeDate,storePrecision:game.storePrecision,
      releases:game.releases.map(({releaseName,versionCode,sizeMB})=>({releaseName,versionCode,sizeMB}))}))};
  const blob=new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'});
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`quest-favorites-${new Date().toISOString().slice(0,10)}.json`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(`Exported ${number(selected.length)} favorite${selected.length===1?'':'s'}`);
}

document.addEventListener('change',event=>{
  const input=event.target.closest('[data-favorite]');
  if(input?.dataset.favorite){toggleFavorite(input.dataset.favorite,input.checked);return;}
  const rating=event.target.closest('[data-rating]');
  if(rating?.dataset.rating){options.ratings=[...document.querySelectorAll('[data-rating]:checked')].map(el=>el.dataset.rating);refresh();}
});
document.addEventListener('click',event=>{const button=event.target.closest('[data-open]');if(button)openDetails(button.dataset.open);});
$('browse').onclick=()=>view(false);$('favorites').onclick=()=>view(true);$('reset').onclick=reset;
$('empty-reset').onclick=()=>{if(options.favoritesOnly&&!favorites.size)view(false);reset();};
let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{options.query=$('search').value;refresh();},120);};
$('genre').onchange=()=>{options.genre=$('genre').value;refresh();};$('mode').onchange=()=>{options.mode=$('mode').value;refresh();};
$('quest-year').onchange=()=>{options.questYear=$('quest-year').value;refresh();};
$('images-only').onchange=()=>{options.imagesOnly=$('images-only').checked;refresh();};
$('sort').value=options.sort;$('sort').onchange=()=>{options.sort=$('sort').value;refresh();};
$('load-more').onclick=()=>{shown+=PAGE_SIZE;refresh(false);};$('export').onclick=exportFavorites;
$('close-detail').onclick=()=>$('details').close();
$('details').addEventListener('click',event=>{if(event.target===$('details')){const r=$('details').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('details').close();}});
$('details').addEventListener('close',()=>{activeDetail=null;if(location.hash.startsWith('#game/'))history.replaceState(null,'',location.pathname+location.search);});
$('import').onclick=()=>$('import-file').click();
$('import-file').onchange=async()=>{
  const file=$('import-file').files?.[0];if(!file)return;
  try{if(file.size>20*1024*1024)throw new Error('Choose a favorites file smaller than 20 MB.');const loaded=parseFavorites(await file.text(),new Set(byId.keys()));for(const id of loaded.ids){favorites.add(id);addedAt[id]??=loaded.addedAt[id]??Date.now();}persist();counts();refresh();toast(`Restored ${number(loaded.ids.size)} favorite${loaded.ids.size===1?'':'s'}`);}
  catch(error){toast(error.message||'Could not restore this favorites file.');}finally{$('import-file').value='';}
};
document.addEventListener('keydown',event=>{if(event.key==='/'&&!$('details').open&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){event.preventDefault();$('search').focus();}});
window.addEventListener('storage',event=>{if(event.key!==STORAGE_KEY||!games.length)return;try{const loaded=parseFavorites(event.newValue,new Set(byId.keys()));favorites=loaded.ids;addedAt=loaded.addedAt;counts();refresh(false);if(activeDetail)openDetails(activeDetail,false);}catch{}});
function hashDetail(){if(location.hash.startsWith('#game/')){try{openDetails(decodeURIComponent(location.hash.slice(6)),false);}catch{}}}
window.addEventListener('hashchange',hashDetail);

try {
  const response=await fetch('./catalog.json');if(!response.ok)throw new Error('Catalog unavailable');
  const data=await response.json();games=data.games;byId=new Map(games.map(game=>[game.id,game]));
  try{const loaded=parseFavorites(localStorage.getItem(STORAGE_KEY),new Set(byId.keys()));favorites=loaded.ids;addedAt=loaded.addedAt;}catch{$('storage-warning').hidden=false;}
  const addOptions=(id,values)=>{for(const value of [...new Set(values)].filter(Boolean).sort((a,b)=>a.localeCompare(b))){const el=document.createElement('option');el.value=value;el.textContent=value;$(id).append(el);}};
  addOptions('genre',games.flatMap(g=>g.genres??[]));addOptions('mode',games.flatMap(g=>g.modes??[]));
  for(const year of [...new Set(games.map(questYear))].filter(year=>year!=='unknown').sort((a,b)=>Number(b)-Number(a))){const el=document.createElement('option');el.value=year;el.textContent=year;$('quest-year').append(el);}
  const unknownYear=document.createElement('option');unknownYear.value='unknown';unknownYear.textContent='Unknown';$('quest-year').append(unknownYear);
  $('all-count').textContent=number(games.length);$('hero-count').textContent=number(games.length);
  $('catalog-note').textContent=`${number(data.totals.games)} games · ${number(data.totals.releases)} releases · ${number(data.totals.gamesWithImages)} with previews`;
  counts();refresh();hashDetail();
} catch {
  $('games').innerHTML='<div class="loading-state">The catalog could not load. Please reload this page.</div>';
  $('games').setAttribute('aria-busy','false');$('results').textContent='Catalog unavailable';
}
