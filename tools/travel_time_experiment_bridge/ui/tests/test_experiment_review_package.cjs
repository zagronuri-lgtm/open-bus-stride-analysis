const test=require('node:test'), assert=require('node:assert/strict');
const P=require('../experiment_review_package.js');
function fixture() {
 const trips=[['out','1',450,490],['back','2',1500,1540],['blocked','1',600,640]].map(([id,dir,dep,arr])=>({id,makat:'10277',dir,alt:'0',dep,arr,row:['277']}));
 const recommendations=trips.slice(0,2).map((t,i)=>({trip_id:t.id,makat:t.makat,direction:t.dir,alt:t.alt,minutes:50+i*10,tier:'R',descriptiveTier:'A',inferencePending:true,n_hour:40+i,n_days:7,period:'2026-06',monthlyEvidence:[{month:'2026-06',n:40,days:7,p80:50}],spread_review:{passed:false,reason:'review'},stability:{prev_month:'2026-05'}}));
 return {tpl:{source:{sid:'map-1'},trips},headers:['Sign'],source:{asset:'map-1.json',sid:'map-1',sha256:'a'.repeat(64),policy_sha256:'b'.repeat(64)},context:{branch:'branch',day_type:'weekday',basis:'school'},selectedIds:['out','back'],recommendations,decisions:trips.slice(0,2).map(t=>({trip:t,code:'review',action:'keep',reason:'review only'})),createdAt:'2026-09-30T12:00:00.000Z'};
}
test('full source unchanged; directions remain separate; 24+ candidate only',()=>{
 const f=fixture(), before=JSON.stringify(f), p=P.build(f);
 assert.equal(p.package_kind,'review_only');assert.equal(p.trips.length,3);
 assert.ok(p.trips.every(t=>t.before_arrival===t.after_arrival));
 assert.deepEqual(p.candidates.map(c=>c.candidate_duration),[50,60]);assert.equal(p.candidates[1].candidate_arrival,1560);
 assert.equal(p.trips[1].departure,1500);assert.deepEqual(p.trips.slice(0,2).map(t=>t.direction),['1','2']);
 assert.equal(JSON.stringify(f),before);assert.equal(p.candidates[0].evidence.tier,'R');assert.equal(p.candidates[0].evidence.descriptiveTier,'A');assert.equal(p.candidates[0].evidence.inferencePending,true);
});
test('operator and absent provenance stay null; no makat fallback for line',()=>{
 const f=fixture(); f.tpl.trips[0].row=[];const p=P.build(f);
 assert.equal(p.trips[0].operator,null);assert.equal(p.trips[0].line_number,null);
 for(const key of ['operator','revision','dataset_id','service_dates']) {assert.equal(p.source[key],null);assert.ok(p.missing_source_fields.includes(key));}
 assert.equal(p.trips[1].line_number,'277');assert.equal(p.trips[1].makat,'10277');
});
test('blocked recommendation is never applied or promoted',()=>{
 const f=fixture();f.decisions[0].code='blocked';f.recommendations[0].tier='D';const p=P.build(f);
 assert.equal(p.candidates[0].decision_code,'blocked');assert.equal(p.candidates[0].evidence.tier,'D');assert.equal(p.trips[0].after_arrival,490);
});
test('invalid, duplicate or unknown selection rejected',()=>{
 for(const ids of [[],['out','out'],['alien'],[null]]) {const f=fixture();f.selectedIds=ids;assert.throws(()=>P.build(f));}
 const f=fixture();f.tpl.trips.push(f.tpl.trips[0]);assert.throws(()=>P.build(f));
});
test('source identity and hashes checked, known references retained',()=>{
 const f=fixture(), p=P.build(f);assert.equal(p.source.asset,'map-1.json');assert.equal(p.source.sha256,f.source.sha256);assert.equal(p.source.policy_sha256,f.source.policy_sha256);
 f.source.sid='other';assert.throws(()=>P.build(f));f.source.sid='map-1';f.source.sha256='bad';assert.throws(()=>P.build(f));
});
test('recommendations require one exact selected identity each',()=>{
 const f=fixture();f.recommendations[0].direction='2';assert.throws(()=>P.build(f));
 const g=fixture();g.recommendations[1]=g.recommendations[0];assert.throws(()=>P.build(g));
 const h=fixture();h.decisions.pop();assert.throws(()=>P.build(h));
});
test('missing evidence remains null and nonfinite values do not leak',()=>{
 const f=fixture();delete f.recommendations[0].n_hour;delete f.recommendations[0].monthlyEvidence;f.recommendations[0].minutes=NaN;
 const p=P.build(f);assert.equal(p.candidates[0].evidence.n,null);assert.equal(p.candidates[0].evidence.monthlyEvidence,null);assert.equal(p.candidates[0].candidate_arrival,null);assert.equal(p.candidates[0].candidate_duration,null);
});
// Reuse the existing DOM fixture without editing its ownership or running its tests.
const fs=require('fs'), path=require('path'), vm=require('vm');
const existing=fs.readFileSync(path.join(__dirname,'test_selected_trips.cjs'),'utf8');
const setup=existing.slice(0,existing.indexOf("test('load lists"))
 .replace('const context={X,DC,','const context={window:{ExperimentReviewPackage:reviewModule},Blob,URL:{createObjectURL:()=>"blob:review",revokeObjectURL:url=>revoked.push(url)},X,DC,')
 .replace('insertBefore(p){', 'appendChild(p){this.children.push(p);},insertBefore(p){')
 .replace('set textContent(v){this.text=v;}', 'set textContent(v){this.text=v;this.children=[];}');
const revoked=[];
const sandbox={require,__dirname,reviewModule:P,Blob,revoked};vm.runInNewContext(setup+'\nthis.makeHarness=harness;',sandbox);
test('UI enables after check only; download is local; changes invalidate',async()=>{
 const h=sandbox.makeHarness();await h.load();assert.equal(h.get('tsExperiment').disabled,true);h.choose('extend');assert.equal(h.get('tsExperiment').disabled,true);h.run();assert.equal(h.get('tsExperiment').disabled,false);
 h.get('tsExperiment').onclick();assert.equal(h.downloads(),0);assert.match(h.get('tsStatus').textContent,/החבילה מוכנה להורדה/);
 const link=h.get('tsStatus').children[0];assert.equal(link.href,'blob:review');assert.match(link.download,/\.json$/);assert.equal(link.textContent,'הורד את חבילת הסקירה');link.click();assert.equal(h.downloads(),1);
 h.choose('night');assert.deepEqual(revoked,['blob:review']);assert.equal(h.get('tsStatus').children.length,0);assert.equal(h.get('tsExperiment').disabled,true);h.run();assert.equal(h.get('tsExperiment').disabled,false);
 h.get('tsExperiment').onclick();assert.equal(h.get('tsStatus').children.length,1);
 h.state.day=1;h.change();assert.equal(revoked.length,2);assert.equal(h.get('tsStatus').children.length,0);assert.equal(h.get('tsExperiment').disabled,true);h.get('tsExperiment').onclick();assert.equal(h.downloads(),1);
});
