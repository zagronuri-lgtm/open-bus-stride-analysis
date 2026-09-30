/* Review-only JSON: preserves the full source; candidate evidence never authorizes a change. */
(function(root) {
'use strict';
const text = v => (typeof v === 'string' || typeof v === 'number') && String(v).trim() ? String(v) : null;
const number = v => typeof v === 'number' && Number.isFinite(v) ? v : null;
function clean(v) {
  if (v == null) return null;
  if (typeof v === 'number') return number(v);
  if (typeof v === 'string' || typeof v === 'boolean') return v;
  if (Array.isArray(v)) return v.map(clean);
  if (typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k,x]) => [k,clean(x)]));
  return null;
}
function build({tpl, headers, source, context, selectedIds, recommendations, decisions, createdAt}) {
  if (!tpl || !Array.isArray(tpl.trips) || !tpl.trips.length || !Array.isArray(headers)) throw new Error('חסר מקור נסיעות מלא');
  if (!source || !text(source.sid) || text(tpl.source?.sid) !== text(source.sid)) throw new Error('זהות מקור אינה תואמת');
  if (typeof createdAt !== 'string' || !Number.isFinite(Date.parse(createdAt))) throw new Error('זמן יצירה אינו תקין');
  for (const k of ['sha256','policy_sha256']) if (source[k] != null && !/^[0-9a-f]{64}$/.test(source[k])) throw new Error('חתימת מקור אינה תקינה');
  if (!Array.isArray(selectedIds) || !selectedIds.length || selectedIds.some(x=>typeof x !== 'string' || !x.trim()) || new Set(selectedIds).size !== selectedIds.length) throw new Error('בחירה ריקה או כפולה');
  const ids = new Set(), signIndex = headers.indexOf('Sign');
  const trips = tpl.trips.map(t => {
    if (typeof t.id !== 'string' || !t.id.trim() || ids.has(t.id)) throw new Error('מזהה מקור חסר או כפול');
    ids.add(t.id);
    if (![t.dep,t.arr].every(v=>Number.isInteger(v) && v>=0 && v<=4320) || t.arr<t.dep) throw new Error('זמני מקור אינם תקינים');
    return {id:t.id, operator:null, makat:text(t.makat), line_number:signIndex>=0?text(t.row?.[signIndex]):null, direction:text(t.dir), alternative:text(t.alt), departure:t.dep, before_arrival:t.arr, after_arrival:t.arr};
  });
  if (selectedIds.some(id=>!ids.has(id))) throw new Error('נסיעה נבחרת אינה במקור');
  function keyed(items, getId) {
    if (!Array.isArray(items) || items.length !== selectedIds.length) throw new Error('תוצאת בדיקה חסרה');
    const map=new Map();
    for (const item of items) { const id=getId(item); if (!selectedIds.includes(id) || map.has(id)) throw new Error('תוצאת בדיקה כפולה או זרה'); map.set(id,item); }
    return map;
  }
  const recs=keyed(recommendations,r=>r.trip_id), checks=keyed(decisions,d=>d.trip?.id), byId=new Map(trips.map(t=>[t.id,t]));
  const candidates=selectedIds.map(id=>{
    const r=recs.get(id), d=checks.get(id), t=byId.get(id);
    for (const [a,b] of [['makat','makat'],['direction','direction'],['alt','alternative']]) if (r[a]!=null && text(r[a])!==t[b]) throw new Error('זהות המלצה אינה תואמת לנסיעה');
    const duration=number(r.minutes), arrival=duration!=null && duration>=0 && t.departure+duration<=4320?t.departure+duration:null;
    return {trip_id:id, candidate_duration:duration, candidate_arrival:arrival, decision_code:text(d.code), decision_action:text(d.action), reason:text(d.reason), recommendation_reason:text(r.reason), evidence:{n:number(r.n_hour ?? r.n), days:number(r.n_days), period:text(r.period), monthlyEvidence:clean(r.monthlyEvidence), tier:text(r.tier), descriptiveTier:text(r.descriptiveTier), inferencePending:typeof r.inferencePending==='boolean'?r.inferencePending:null, spread:clean(r.spread_review ?? r.spread), stability:clean(r.stability)}};
  });
  const src={asset:text(source.asset), sid:text(source.sid), sha256:text(source.sha256), policy_sha256:text(source.policy_sha256), dataset_id:null, revision:null, operator:null, service_dates:null};
  return {package_kind:'review_only', schema_version:1, created_at:createdAt, authorization:'none', source:src, missing_source_fields:Object.keys(src).filter(k=>src[k]===null), context:{branch:text(context?.branch), day_type:text(context?.day_type), basis:text(context?.basis)}, trips, selected_trip_ids:selectedIds.slice(), candidates, limitations:['סקירה בלבד; המקור נשמר ללא שינוי והמלצות אינן הרשאה.', 'זהות מפעיל, דאטהסט, גרסת מקור ותאריכי יעד חסרים ודורשים אימות שרתי.']};
}
const api={build};
if(typeof module!=='undefined' && module.exports) module.exports=api;
else root.ExperimentReviewPackage=api;
})(typeof window!=='undefined'?window:globalThis);
