// Exercises the actual selection controller with real map/policy calculations and a minimal DOM.
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), vm = require('vm');
const X = require('../optibus_update_trips.js');
const source = fs.readFileSync(path.join(__dirname, '../optibus_export_ui.js'), 'utf8');
const controller = source.slice(source.indexOf('function mountTripSelection('), source.indexOf('/* ---------- screen 4 ---------- */'));
function fixture() {
  const trip = (id, makat, dep, arr) => ({id, sign:makat, directionName:'1', pattern:'0', optibusRouteId:makat+'-1-0', vehicleTypeIds:['urban'], stops:[{id:'A',time:dep,isTimePoint:true},{id:'B',time:arr,isTimePoint:true}]});
  return {optibusId:'m1',service:{daysOfWeek:[1]},vehicleTypes:[{id:'urban'}],stops:[{id:'A'},{id:'B'}],events:{trips:[trip('extend','111','07:00:00','07:40:00'),trip('missing','112','08:00:00','08:40:00'),trip('blocked','277','09:00:00','09:40:00'),trip('night','113','24:30:00','25:10:00'),trip('cut','114','10:00:00','10:40:00')],deadheads:[]},vehicles:[],duties:[]};
}
function harness(recOverrides = {}) {
  const nodes = new Map(), listeners = new Map(), map = fixture(), calls = []; let pause = null, downloads = 0;
  function el() { return {value:'',style:{},children:[],listeners:{},disabled:false,hidden:false,
    set innerHTML(v) { this.html=v; if(v.startsWith('<option'))this.value=''; }, get innerHTML(){return this.html||'';},
    set textContent(v){this.text=v;},get textContent(){return this.text||'';},
    querySelector(q){const id=q.slice(1);if(!nodes.has(id))nodes.set(id,el());return nodes.get(id);},
    addEventListener(k,fn){(this.listeners[k] ||= []).push(fn);},insertBefore(p){this.children.push(p);nodes.set(p.id,p);},
    querySelectorAll(){return [];}, focus(){this.focused=true;},
    click(){downloads++;}}; }
  const box=el(),sel=el();sel.value='m1';
  const state={b:0,day:0,p80Basis:'latest',cluster:false};
  const DC={index:()=>({}),recsForTrips:trips=>{calls.push(trips.map(t=>t.id));return trips.map(t=>({trip_id:t.id,tier:t.id==='missing'?'D':'A',minutes:t.id==='missing'?null:t.id==='cut'?30:50,p80:t.id==='missing'?null:50,n_hour:t.id==='missing'?null:40,n_days:t.id==='missing'?null:10,period:'2026-08',reason:t.id==='missing'?'אין בסיס ראיות':'מקור בדיקה',...recOverrides[t.id]}));}};
  const context={X,DC,state,CELLS:[],M:{built:'test',seasons:['לימודים']},BR:['אונו','כפר סבא'],DAYS:['חול','שישי'],MONTHS:[],usable:()=>true,esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    document:{createElement:el,getElementById:id=>nodes.get(id),addEventListener(k,fn){if(!listeners.has(k))listeners.set(k,[]);listeners.get(k).push(fn);}},setTimeout:fn=>fn(),
    asset:async file=>{if(pause&&file==='map.json')await pause.promise;return file==='index.json'?{templates:[{sid:'m1',file:'map.json'}],policy:{}}:file==='map.json'?map:{blocked:[{makat:'277',direction:'1',reason:'סתירת מקורות פתוחה'}]};},download(){downloads++;}};
  vm.runInNewContext(controller+'\nthis.mount=mountTripSelection;',context);
  context.mount(box,sel,()=>JSON.stringify(state),()=>[{sid:'m1'}]);
  const get=id=>{assert.ok(nodes.has(id),id);return nodes.get(id);};
  return {get,state,map,calls,sel,downloads:()=>downloads,load:()=>get('tsLoad').onclick(),run:()=>get('tsRun').onclick(),choose(id,checked=true){get('tsList').listeners.change[0]({target:{checked,getAttribute:()=>id}});},change(){(listeners.get('dashboard-scope-change')||[]).forEach(fn=>fn({}));},delay(){let resolve;const promise=new Promise(r=>resolve=r);pause={promise};return resolve;}};
}
test('load lists all identities and preserves 24+ departure time',async()=>{const h=harness();await h.load();assert.equal(h.get('tsControls').hidden,false);assert.match(h.get('tsList').innerHTML,/24:30/);assert.equal((h.get('tsList').innerHTML.match(/data-trip=/g)||[]).length,5);assert.equal(h.get('tsRun').disabled,true);assert.equal(h.downloads(),0);});
test('two checkbox selections evaluate exactly those trips without Excel or source changes',async()=>{const h=harness(),before=JSON.stringify(h.map);await h.load();h.choose('extend');h.choose('night');h.run();assert.deepEqual(h.calls,[['extend','night']]);assert.match(h.get('tsStatus').textContent,/נבדקו 2 נסיעות/);assert.equal((h.get('tsResult').innerHTML.match(/<small>/g)||[]).length,2);assert.match(h.get('tsResult').innerHTML,/24:30/);assert.equal(h.downloads(),0);assert.equal(JSON.stringify(h.map),before);});
test('missing evidence, policy block and cuts retain existing duration',async()=>{const h=harness();await h.load();['missing','blocked','cut'].forEach(id=>h.choose(id));h.run();const html=h.get('tsResult').innerHTML;assert.match(html,/אין בסיס ראיות/);assert.match(html,/סתירת מקורות פתוחה/);assert.equal((html.match(/זמן שנשאר בלוח: <b>40<\/b>/g)||[]).length,3);assert.match(html,/—/);assert.match(h.get('tsStatus').textContent,/0 הצעות שינוי/);assert.equal(h.downloads(),0);});
test('24+ exact minute filter and clearing selections',async()=>{const h=harness();await h.load();h.get('tsFrom').value='24:30';h.get('tsTo').value='24:30';h.get('tsFilter').onclick();assert.equal((h.get('tsList').innerHTML.match(/data-trip=/g)||[]).length,1);h.get('tsAll').onclick();h.run();assert.deepEqual(h.calls,[['night']]);h.get('tsClear').onclick();assert.equal(h.get('tsRun').disabled,true);assert.equal(h.get('tsResult').innerHTML,'');});
test('filtering does not silently remove an already selected trip',async()=>{const h=harness();await h.load();h.choose('extend');h.get('tsFrom').value='24:30';h.get('tsFilter').onclick();h.run();assert.deepEqual(h.calls,[['extend']]);});
test('scope changes clear result, hide old list and prevent stale evaluation',async()=>{const h=harness();await h.load();h.choose('extend');h.run();h.state.b=1;h.change();assert.equal(h.get('tsControls').hidden,true);assert.equal(h.get('tsList').innerHTML,'');assert.equal(h.get('tsResult').innerHTML,'');h.run();assert.equal(h.calls.length,1);assert.equal(h.downloads(),0);});
test('async load resolving after scope change cannot restore previous data',async()=>{const h=harness(),release=h.delay();const pending=h.load();await Promise.resolve();h.state.day=1;h.change();release();await pending;assert.equal(h.get('tsControls').hidden,true);assert.equal(h.get('tsList').innerHTML,'');assert.match(h.get('tsStatus').textContent,/החתך השתנה/);assert.equal(h.downloads(),0);});
test('duplicate map trip identity blocks selection instead of ambiguous joining',async()=>{const h=harness();h.map.events.trips.push({...h.map.events.trips[0]});await h.load();assert.equal(h.get('tsControls').hidden,true);assert.match(h.get('tsStatus').textContent,/כפולים/);});
for (const invalidId of [undefined, null, '', '   ']) {
  test(`missing or blank identity ${JSON.stringify(invalidId)} cannot fall back to route matching`, async () => {
    const h = harness(); h.map.events.trips[0].id = invalidId;
    await h.load();
    assert.equal(h.get('tsControls').hidden, true);
    assert.match(h.get('tsStatus').textContent, /מזה|ריק|חסר/);
    assert.equal(h.calls.length, 0);
    assert.equal(h.downloads(), 0);
  });
}
test('invalid filter clears old filtered rows and cannot select that former list', async () => {
  const h = harness(); await h.load();
  h.choose('night');
  h.get('tsFrom').value = '25:99';
  h.get('tsFilter').onclick();
  assert.match(h.get('tsStatus').textContent, /שעה/);
  assert.equal((h.get('tsList').innerHTML.match(/data-trip=/g) || []).length, 0);
  assert.equal(h.get('tsAll').disabled, true);
  // Even invoking the handler directly must not reselect the previously visible rows.
  h.get('tsAll').onclick();
  h.run();
  assert.deepEqual(h.calls, [['night']]);
  assert.equal(h.downloads(), 0);
});
test('reversed hour range invalidates filtered rows without discarding explicit selection', async () => {
  const h = harness(); await h.load(); h.choose('extend');
  h.get('tsFrom').value = '09:00'; h.get('tsTo').value = '08:00';
  h.get('tsFilter').onclick();
  assert.equal(h.get('tsAll').disabled, true);
  assert.equal((h.get('tsList').innerHTML.match(/data-trip=/g) || []).length, 0);
  assert.match(h.get('tsStatus').textContent, /מוקדמת/);
  h.run(); assert.deepEqual(h.calls, [['extend']]);
});
test('same line can select both directions while evidence remains separate per trip', async () => {
  const h = harness({extend:{p80:50,minutes:50,n_hour:40},reverse:{p80:63,minutes:63,n_hour:75}});
  const reverse = JSON.parse(JSON.stringify(h.map.events.trips[0]));
  reverse.id='reverse'; reverse.directionName='2'; reverse.optibusRouteId='111-2-0';
  reverse.stops[0].time='07:20:00'; reverse.stops[1].time='08:00:00';
  h.map.events.trips.push(reverse);
  await h.load();
  assert.match(h.get('tsRoute').innerHTML, /value="line:111"/);
  h.get('tsRoute').value='line:111'; h.get('tsFilter').onclick();
  assert.equal((h.get('tsList').innerHTML.match(/data-trip=/g)||[]).length,2);
  h.get('tsAll').onclick(); h.run();
  assert.deepEqual(h.calls,[['extend','reverse']]);
  const cards = h.get('tsResult').innerHTML.split('<article class="tsDecision"').slice(1);
  assert.equal(cards.length,2);
  assert.match(cards[0], /כיוון 1/); assert.match(cards[0], /P80 בראיות:<\/b> 50 דקות/); assert.match(cards[0], /40 תצפיות/);
  assert.match(cards[1], /כיוון 2/); assert.match(cards[1], /P80 בראיות:<\/b> 63 דקות/); assert.match(cards[1], /75 תצפיות/);
  assert.doesNotMatch(h.get('tsResult').innerHTML, /115 תצפיות/);
});
test('chosen basket survives route filters and removing one trip updates evaluation', async () => {
  const h = harness(); await h.load(); h.choose('extend'); h.choose('night');
  h.get('tsRoute').value='line:113'; h.get('tsFilter').onclick();
  assert.match(h.get('tsChosen').innerHTML,/data-remove="extend"/);
  assert.match(h.get('tsChosen').innerHTML,/data-remove="night"/);
  assert.match(h.get('tsCount').textContent,/1 נבחרות מחוץ לסינון/);
  h.get('tsChosen').listeners.click[0]({target:{getAttribute:()=> 'extend'}});
  assert.doesNotMatch(h.get('tsChosen').innerHTML,/data-remove="extend"/);
  assert.match(h.get('tsChosen').innerHTML,/data-remove="night"/);
  h.run(); assert.deepEqual(h.calls,[['night']]);
  h.state.b=1;h.change();assert.doesNotMatch(h.get('tsChosen').innerHTML,/data-remove=/);
});
test('result explains Hebrew source months, observation coverage and IQR review gates', async () => {
  const h = harness({extend:{stability:{prev_month:'2026-07'}}});
  await h.load();h.choose('extend');h.run();const html=h.get('tsResult').innerHTML;
  assert.match(html,/אוגוסט 2026/);assert.match(html,/בדיקת יציבות מול יולי 2026/);
  assert.match(html,/אין פירושו שכל ימי החודש נכללו/);
  assert.match(html,/IQR גבוה או פער גבוה בין P80 ל־P90/);
  assert.match(html,/href="#g-spread"/);
  assert.match(html,/<details><summary>פירוט הנימוק והמקור<\/summary>/);
});
