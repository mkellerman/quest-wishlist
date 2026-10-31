import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { filterAndSort, parseFavorites, serializeFavorites, ratingBucket, questYear } from '../site/catalog-core.mjs';

const rows = [
  { id:'a', title:'Álpha 2', packageName:'com.alpha.game', developers:['Studio One'], genres:['Puzzle'], themes:['Mystery'], modes:['Single player'], hasImage:true, storeRating:4.2, storeRatingCount:3, sizeMB:100, updatedAt:'2026-10-01T00:00:00Z', questDate:'2024', questPrecision:'year', storeDate:'2024-09', storePrecision:'month' },
  { id:'b', title:'Beta', packageName:'com.beta.game', developers:['Studio Two'], genres:['Action'], themes:[], modes:['Co-op'], hasImage:false, storeRating:null, storeRatingCount:null, sizeMB:null, updatedAt:null, questDate:null, questPrecision:'unknown', storeDate:null, storePrecision:'unknown' },
  { id:'c', title:'Alpha 10', packageName:'org.alpha.ten', developers:[], genres:['Puzzle'], themes:['Space'], modes:['Single player'], hasImage:true, storeRating:4.9, storeRatingCount:200, sizeMB:0, updatedAt:'2026-10-03T00:00:00Z', questDate:'2024-Q2', questPrecision:'quarter', storeDate:'2024-01-15', storePrecision:'day' },
  { id:'d', title:'Delta', packageName:'com.delta.game', developers:['Studio One'], genres:['Action'], themes:[], modes:['Co-op'], hasImage:true, storeRating:4.9, storeRatingCount:10, sizeMB:500, updatedAt:'2026-10-02T00:00:00Z', questDate:'2023-12-31', questPrecision:'day', storeDate:'2024-06', storePrecision:'month' },
];
const known = new Set(rows.map(row => row.id));
const favoriteSet = new Set(['a','c']);
const ids = options => filterAndSort(rows, options, favoriteSet, {a:100,c:200}).map(row => row.id);
let checks = 0;
function check(label, test) { test(); checks++; console.log(`PASS ${label}`); }

check('selection survives JSON persistence roundtrip', () => {
  const result = parseFavorites(serializeFavorites(favoriteSet,{a:100,c:200}),known);
  assert.deepEqual([...result.ids],['a','c']);
  assert.equal(result.addedAt.a,100); assert.equal(result.addedAt.c,200);
});
check('new browser with no persisted value starts empty', () => assert.equal(parseFavorites(null,known).ids.size,0));
check('unknown imported IDs are ignored without losing known selections', () => {
  assert.deepEqual([...parseFavorites('['+'"a","unknown","a"'+']',known).ids],['a']);
  assert.deepEqual([...parseFavorites({schemaVersion:1,favorites:[{id:'c'},{id:'unknown'}]},known).ids],['c']);
});
check('malformed or incompatible favorites fail explicitly', () => {
  for (const value of ['{broken','null','{}','{"version":2,"ids":[]}','{"version":1,"ids":[{}]}','{"schemaVersion":1,"favorites":[{}]}']) assert.throws(()=>parseFavorites(value,known),Error);
  assert.throws(()=>parseFavorites({version:1,ids:['a'],addedAt:{a:'yesterday'}},known),Error);
});
check('favorites-only and image filters combine', () => assert.deepEqual(ids({favoritesOnly:true,imagesOnly:true}),['a','c']));
check('search is accent-insensitive and combines tokens across developer/title', () => {
  assert.deepEqual(ids({query:'alpha studio one'}),['a']);
  assert.deepEqual(ids({query:'COM.BETA'}),['b']);
});
check('genre/mode membership filters are exact', () => {
  assert.deepEqual(ids({genre:'Puzzle',mode:'Single player'}),['a','c']);
  assert.deepEqual(ids({genre:'Puzz'}),[]);
});
check('all nullable fields sort missing values last in both directions', () => {
  for (const sort of ['quest-newest','quest-oldest','store-newest','store-oldest','rating-high','reviews-high','size-low','size-high','updated-newest']) assert.equal(ids({sort}).at(-1),'b',sort);
  for (const sort of ['title-asc','title-desc']) assert.equal(filterAndSort([{id:'empty',title:null},{id:'known',title:'Known'}],{sort}).at(-1).id,'empty');
});
check('partial dates sort by appropriate interval boundary without mutation', () => {
  const before = JSON.stringify(rows);
  assert.deepEqual(ids({sort:'quest-oldest'}),['d','a','c','b']);
  assert.deepEqual(ids({sort:'quest-newest'}),['a','c','d','b']);
  assert.deepEqual(ids({sort:'store-newest'}),['a','d','c','b']);
  assert.equal(JSON.stringify(rows),before);
});
check('numbers retain zero and stable tie breaks', () => {
  assert.deepEqual(ids({sort:'size-low'}),['c','a','d','b']);
  assert.deepEqual(ids({sort:'rating-high'}),['c','d','a','b']);
  assert.deepEqual(ids({sort:'reviews-high'}),['c','d','a','b']);
});
check('developer and favorite timestamps handle missing values', () => {
  assert.equal(ids({sort:'developer-asc'}).at(-1),'c');
  assert.deepEqual(ids({favoritesOnly:true,sort:'favorited-newest'}),['c','a']);
});
check('sorting and filtering do not mutate original rows or selection', () => {
  const before = [...favoriteSet];
  for (const sort of ['title-asc','title-desc','rating-high','quest-newest']) ids({sort});
  assert.deepEqual([...favoriteSet],before);
  assert.deepEqual(rows.map(row=>row.id),['a','b','c','d']);
});
check('rating buckets round down and never promote a sub-threshold average', () => {
  for (const [score,bucket] of [[5,'5'],[4.99,'4'],[4.5,'4'],[4,'4'],[3.999,'3'],[3.8,'3'],[3,'3'],[2.99,'2'],[2,'2'],[1.99,'1'],[1,'1'],[0,'0']]) {
    assert.equal(ratingBucket({storeRating:score}),bucket,`score ${score}`);
  }
});
check('unrated includes missing or invalid scores without conflating valid zero', () => {
  for (const score of [null,undefined,NaN,Infinity,-0.1,5.01,'4.9']) assert.equal(ratingBucket({storeRating:score}),'none');
  assert.equal(ratingBucket({storeRating:0}),'0');
  assert.equal(ratingBucket({igdbRating:98}),'none','IGDB rating does not substitute for missing store rating');
});
check('rating filters are minimum thresholds and support an unrated union', () => {
  assert.deepEqual(ids({ratings:['5']}),[]);
  assert.deepEqual(ids({ratings:['4']}),['a','c','d']);
  assert.deepEqual(ids({ratings:['none']}),['b']);
  assert.deepEqual(ids({ratings:['4','none']}),['a','c','b','d']);
  assert.deepEqual(ids({ratings:['3','4']}),['a','c','d'],'overlapping minimum thresholds are a union');
});
check('four-plus includes exact four and five but excludes 3.8 and 3.999', () => {
  const scores=[3.8,3.999,4,4.99,5,0].map((score,index)=>({id:String(index),title:`Game ${index}`,storeRating:score}));
  assert.deepEqual(filterAndSort(scores,{ratings:['4']}).map(game=>game.storeRating),[4,4.99,5]);
  assert.deepEqual(filterAndSort(scores,{ratings:['5']}).map(game=>game.storeRating),[5]);
  assert.deepEqual(filterAndSort(scores,{ratings:['1']}).map(game=>game.storeRating),[3.8,3.999,4,4.99,5]);
});
check('empty rating selection shows all games and composes with existing filters', () => {
  assert.deepEqual(ids({ratings:[]}),ids({}));
  assert.deepEqual(ids({ratings:['4','5'],genre:'Puzzle',mode:'Single player',query:'alpha',favoritesOnly:true,imagesOnly:true}),['a','c']);
  assert.deepEqual(ids({ratings:['none'],imagesOnly:true}),[]);
});
check('rating filters do not mutate games, selected rating buckets, or favorites', () => {
  const ratings=['4','none'],before=JSON.stringify(rows),saved=[...favoriteSet];
  const selected=filterAndSort(rows,{ratings,sort:'rating-high'},favoriteSet);
  assert.deepEqual(selected.map(row=>row.id),['c','d','a','b']);
  assert.equal(JSON.stringify(rows),before);
  assert.deepEqual(ratings,['4','none']);
  assert.deepEqual([...favoriteSet],saved);
});
check('Quest year accepts validated day, month, year, and quarter precision', () => {
  for(const [questDate,questPrecision] of [['2024-02-29','day'],['2024-02','month'],['2024','year'],['2024-Q3','quarter']]) {
    assert.equal(questYear({questDate,questPrecision}),'2024');
  }
  for(const [questDate,questPrecision] of [['2024-02-30','day'],['2024-13','month'],['2024-Q5','quarter'],['Fall 2024','season'],['2024-01-01','unknown'],[null,'unknown']]) {
    assert.equal(questYear({questDate,questPrecision}),'unknown');
  }
});
check('Quest year never substitutes store, original-platform, or updated dates', () => {
  assert.equal(questYear({questDate:null,storeDate:'2024-05-01',storePrecision:'day',updatedAt:'2026-10-31T00:00:00Z',originalRelease:'2018-01-01'}),'unknown');
});
check('Quest year filters known and unresolved years and combines with all other filters', () => {
  assert.deepEqual(ids({questYear:'2024'}),['a','c']);
  assert.deepEqual(ids({questYear:'2023'}),['d']);
  assert.deepEqual(ids({questYear:'unknown'}),['b']);
  assert.deepEqual(ids({questYear:''}),ids({}));
  assert.deepEqual(ids({questYear:'2024',ratings:['4'],genre:'Puzzle',mode:'Single player',query:'alpha',favoritesOnly:true,imagesOnly:true}),['a','c']);
  assert.deepEqual(ids({questYear:'unknown',ratings:['4']}),[]);
});
console.log(`${checks} core behavior checks passed.`);

// Run the actual app handlers with an isolated DOM/storage adapter. This checks
// browser-independent persistence behavior without launching or controlling UI.
const source = readFileSync(new URL('../site/app.mjs',import.meta.url),'utf8').replace(/^import[^\n]+\n/,'');
const idsInHtml = [...readFileSync(new URL('../site/index.html',import.meta.url),'utf8').matchAll(/id="([^"]+)"/g)].map(match=>match[1]);
const fixtures = [{...rows[0], title:'<img onerror="bad()"> Alpha', cover:'https://images.example.test/one.jpg', imageCandidates:['https://images.example.test/one.jpg','https://images.example.test/two.jpg'], screenshots:[], summary:'<script>bad()</script>', releases:[{releaseName:'Alpha release',versionCode:'1',sizeMB:100,updatedAt:'2026-10-01T00:00:00Z'}], ageRatings:[],publishers:[],websites:[]}];
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const appFactory = new AsyncFunction('filterAndSort','parseFavorites','serializeFavorites','ratingBucket','questYear','document','window','localStorage','fetch','history','location','setTimeout','clearTimeout','URL','Blob',source+'\nreturn {hydrateImages,openDetails,exportFavorites,getState:()=>({favorites:[...favorites],addedAt})};');
function element() {
  const listeners = new Map();
  return {innerHTML:'',textContent:'',value:'',checked:false,hidden:true,disabled:false,files:[],open:false,
    classList:{toggle(){}},dataset:{},setAttribute(){},append(){},remove(){},focus(){},click(){this.clicked=true;},
    addEventListener(type,fn){listeners.set(type,fn);},querySelectorAll(){return [];},
    showModal(){this.open=true;},close(){this.open=false;listeners.get('close')?.();},getBoundingClientRect(){return {left:0,right:1,top:0,bottom:1};}};
}
async function app(storage=new Map(),throwOnSave=false) {
  const elements = Object.fromEntries(idsInHtml.map(id=>[id,element()]));
  const ratingInputs = Object.fromEntries(['5','4','3','2','1','none'].map(bucket=>[bucket,{...element(),dataset:{rating:bucket}}]));
  const ratingCounts = Object.fromEntries(['5','4','3','2','1','none'].map(bucket=>[bucket,{...element(),dataset:{ratingCount:bucket}}]));
  const handlers = new Map();
  const links = [],blobs=[];
  const document = {getElementById:id=>elements[id],activeElement:null,querySelectorAll:selector=>{
      if(selector==='[data-rating]')return Object.values(ratingInputs);
      if(selector==='[data-rating]:checked')return Object.values(ratingInputs).filter(input=>input.checked);
      if(selector==='[data-rating-count]')return Object.values(ratingCounts);
      return [];
    },
    addEventListener:(type,fn)=>handlers.set(type,fn),createElement:()=>{const el=element();links.push(el);return el;},
    body:{append(){}},createRange:()=>({createContextualFragment:html=>({html})})};
  const location={hash:'',pathname:'/',search:''};
  const urlApi=class extends URL {};
  urlApi.createObjectURL=blob=>{blobs.push(blob);return 'blob:local-test';};urlApi.revokeObjectURL=()=>{};
  const runtime=await appFactory(filterAndSort,parseFavorites,serializeFavorites,ratingBucket,questYear,document,{addEventListener(){},scrollTo(){}},
    {getItem:key=>storage.get(key)??null,setItem:(key,value)=>{if(throwOnSave)throw new Error('Quota exceeded');storage.set(key,value);}},
    async()=>({ok:true,json:async()=>({games:fixtures,totals:{games:1,releases:1,gamesWithImages:1}})}),
    {replaceState(_state,_title,path){location.hash=path.startsWith('#')?path:'';}},location,()=>1,()=>{},urlApi,Blob);
  return {runtime,elements,handlers,storage,links,blobs,ratingInputs,ratingCounts};
}
const first = await app();
first.handlers.get('change')({target:{closest:selector=>selector==='[data-favorite]'?{dataset:{favorite:'a'},checked:true}:null}});
assert.deepEqual(first.runtime.getState().favorites,['a']);
assert.equal(first.elements['favorite-count'].textContent,'1');
const reloaded=await app(first.storage);
assert.deepEqual(reloaded.runtime.getState().favorites,['a']);
assert.equal(reloaded.elements.export.disabled,false);
console.log('PASS actual app checkbox save survives reload');

const quota=await app(new Map(),true);
quota.handlers.get('change')({target:{closest:selector=>selector==='[data-favorite]'?{dataset:{favorite:'a'},checked:true}:null}});
assert.equal(quota.elements['storage-warning'].hidden,false);
assert.equal(quota.elements['save-status'].textContent,'Export to keep your favorites');
assert.deepEqual(quota.runtime.getState().favorites,['a']);
console.log('PASS actual app retains in-memory selection and warns when persistence fails');

reloaded.runtime.exportFavorites();
const exported=JSON.parse(await reloaded.blobs[0].text());
assert.equal(exported.favorites.length,1);assert.equal(exported.favorites[0].id,'a');
assert.equal(Object.hasOwn(exported.favorites[0].releases[0],'downloadUrl'),false);
assert.equal(JSON.stringify(exported).includes('download_directory_url'),false);
const restored=await app();
restored.elements['import-file'].files=[{size:200,text:async()=>JSON.stringify(exported)}];
await restored.elements['import-file'].onchange();
assert.deepEqual(restored.runtime.getState().favorites,['a']);
console.log('PASS actual app export/import restores selection and omits download links');

let replacement;
const fakeImage={dataset:{gameImage:'a'},src:fixtures[0].cover,complete:false,naturalWidth:0,replaceWith:value=>{replacement=value;}};
restored.runtime.hydrateImages({querySelectorAll:selector=>selector==='[data-game-image]'?[fakeImage]:[]});
fakeImage.onerror();assert.equal(fakeImage.src,'https://images.example.test/two.jpg');
fakeImage.onerror();assert.match(replacement.html,/No preview available/);
console.log('PASS actual app image fallback retries another candidate then shows placeholder');

assert.ok(!restored.elements.games.innerHTML.includes('<img onerror="bad()">'));
assert.ok(restored.elements.games.innerHTML.includes('&lt;img onerror=&quot;bad()&quot;&gt;'));
restored.runtime.openDetails('a');
assert.ok(!restored.elements['detail-content'].innerHTML.includes('<script>bad()</script>'));
assert.ok(restored.elements['detail-content'].innerHTML.includes('&lt;script&gt;bad()&lt;/script&gt;'));
console.log('PASS actual app escapes source title and description markup');

const ratingApp=await app();
function setRating(runtime,bucket,checked) {
  const input=runtime.ratingInputs[bucket];input.checked=checked;
  runtime.handlers.get('change')({target:{closest:selector=>selector==='[data-rating]'?input:null}});
}
assert.equal(ratingApp.ratingCounts['4'].textContent,'1');
assert.equal(ratingApp.ratingCounts['5'].textContent,'0');
setRating(ratingApp,'5',true);
assert.equal(ratingApp.elements.games.innerHTML,'');
assert.equal(ratingApp.ratingCounts['4'].textContent,'1','counts exclude the current rating selection');
setRating(ratingApp,'4',true);
assert.ok(ratingApp.elements.games.innerHTML.includes('data-card="a"'),'selected buckets combine with OR');
setRating(ratingApp,'5',false);
assert.ok(ratingApp.elements.games.innerHTML.includes('data-card="a"'));
ratingApp.elements.genre.value='Action';ratingApp.elements.genre.onchange();
assert.equal(ratingApp.elements.games.innerHTML,'');
assert.equal(ratingApp.ratingCounts['4'].textContent,'0','counts still respect the genre filter');
console.log('PASS actual rating checkboxes combine and counts respect other filters');

const yearApp=await app();
yearApp.elements['quest-year'].value='2023';yearApp.elements['quest-year'].onchange();
assert.equal(yearApp.elements.games.innerHTML,'');
assert.equal(yearApp.ratingCounts['4'].textContent,'0','rating counts respect Quest year');
yearApp.elements['quest-year'].value='2024';yearApp.elements['quest-year'].onchange();
assert.ok(yearApp.elements.games.innerHTML.includes('data-card="a"'));
assert.equal(yearApp.ratingCounts['4'].textContent,'1');
yearApp.elements['quest-year'].value='unknown';yearApp.elements['quest-year'].onchange();
assert.equal(yearApp.elements.games.innerHTML,'','known Quest year is not shown as unresolved');
console.log('PASS actual Quest year selector filters games and rating counts');

const originalScore=fixtures[0].storeRating;
try {
  for(const [score,display] of [[4.99,'4.9'],[3.99,'3.9'],[3.999,'3.9']]) {
    fixtures[0].storeRating=score;
    const displayApp=await app();
    assert.ok(displayApp.elements.games.innerHTML.includes(`</span>${display}<small>`),`card score ${score} displays ${display}`);
    if(score===3.999) {
      displayApp.runtime.openDetails('a');
      assert.ok(displayApp.elements['detail-content'].innerHTML.includes('3.99 / 5'));
      assert.ok(!displayApp.elements['detail-content'].innerHTML.includes('4.00 / 5'));
    }
  }
} finally { fixtures[0].storeRating=originalScore; }
console.log('PASS actual cards and detail ratings truncate decimals without rounding up');

ratingApp.handlers.get('change')({target:{closest:selector=>selector==='[data-favorite]'?{dataset:{favorite:'a'},checked:true}:null}});
ratingApp.elements['quest-year'].value='2023';ratingApp.elements['quest-year'].onchange();
ratingApp.elements.reset.onclick();
assert.ok(Object.values(ratingApp.ratingInputs).every(input=>!input.checked));
assert.equal(ratingApp.elements.genre.value,'');
assert.equal(ratingApp.elements['quest-year'].value,'');
assert.ok(ratingApp.elements.games.innerHTML.includes('data-card="a"'));
assert.deepEqual(ratingApp.runtime.getState().favorites,['a'],'resetting rating filters preserves favorites');
console.log('PASS actual reset clears rating and Quest year selections and keeps saved favorites');
console.log('9 app integration checks passed.');
