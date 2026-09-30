/* Optibus UPDATE TRIPS export: target-schedule template → clean load file + separate change report.
   Browser (window.OptibusUpdateTrips) and Node (module.exports). No network, no formulas.
   Rules (see CONTRACT.md):
   - The load file carries the WHOLE target schedule (a missing trip is deleted by UPDATE TRIPS).
   - Route-level fields (Sign, Direction, Alternative, Origin/Destination, Distance, Route Id, Vehicle Type Ids) are copied
     from the target template verbatim; a modified Sign made Optibus reject 7 routes in task 027.
   - Only Arrival and the LAST StopTimes row of an approved trip change. Trips with more than two stops are never
     modified: no intermediate stop time is derived from a total duration.
   - Departure/Arrival/StopTimes are Excel times modulo 24h; Day Offset derives from departure only (proven Sukkot file). */
(function (root) {
'use strict';
const TRIPS_HEADERS = ['Id', 'Region', 'Catalog Number', 'Sign', 'Direction', 'Alternative', 'Origin Stop id', 'Destination Stop Id', 'Day Offset', 'Departure', 'Arrival', 'Vehicle Type Ids', 'Distance', 'Days', 'Boarding Time', 'Offboarding Time', 'Sub trip index', 'Route Id', 'Vehicle ID'];
const STOP_HEADERS = ['Trip Id', 'Time', 'Point Id', 'distance', 'Sequence', 'Time Point'];
const PLACES_HEADERS = ['Id', 'Description', 'Address', 'Latitude', 'Longitude', 'Type'];
const VT_HEADERS = ['Id', 'Description', 'Family Type'];
const MAP_HEADERS = ['System Id', 'User Id'];
const SHEETS = ['Trips', 'StopTimes', 'Places', 'VehicleTypes', 'TripIdsMapping'];
const T = Object.fromEntries(TRIPS_HEADERS.map((h, i) => [h, i]));

function fail(msg) { throw new Error(msg); }
function str(x) { return x == null ? null : String(x); }
/* HH:MM[:SS] (hours may exceed 23) → whole minutes; seconds ≥30 round up (same rule as the proven 025 builder). */
function clockToMin(s) {
  const m = String(s ?? '').trim().match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if (!m) fail(`שעה לא תקינה: ${s}`);
  return Number(m[1]) * 60 + Number(m[2]) + (Number(m[3] || 0) >= 30 ? 1 : 0);
}
function fracToMin(f) { if (typeof f !== 'number' || !Number.isFinite(f)) fail(`זמן Excel לא תקין: ${f}`); return Math.round(f * 1440); }
function minToFrac(m) { return (((m % 1440) + 1440) % 1440) / 1440; }
function hhmm(m) { const n = ((m % 1440) + 1440) % 1440; return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`; }
function hhmmPlain(m) { return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`; }

/* ---------- templates ---------- */
/* From an Optibus API schedule JSON (the files in data/מפות-פעילות).
   meta.placesOriginal: the Places rows (no header) of a load file already used for the SAME dataset. When given, that catalog is
   kept VERBATIM (row order, text, coordinates); map stops missing from it are appended from the map and recorded. Without it the
   Places sheet is built from map.stops only. Nothing is rewritten or invented. */
function templateFromMap(map, meta = {}) {
  const trips = map?.events?.trips; if (!Array.isArray(trips)) fail('events.trips חסר במפה');
  const days = Array.isArray(map.service?.daysOfWeek) ? map.service.daysOfWeek : [];
  if (days.length !== 1) fail(`Days: צפוי יום שירות יחיד במפה, נמצא ${JSON.stringify(days)}; פורמט Days לכמה ימים לא אומת`);
  const { placesOriginal, ...metaRest } = meta;
  const tpl = { kind: 'map', source: { sid: map.optibusId || null, ...metaRest }, days: String(days[0]), serviceName: map.service?.name ?? null, trips: [], places: [], vehicleTypes: [], mapping: [], warnings: [] };
  for (const t of trips) {
    const rid = String(t.optibusRouteId || ''); const parts = rid.split('-');
    if (parts.length !== 3) fail(`Route Id לא תקין לנסיעה ${t.id}: ${rid}`);
    const [makat, dirName, alt] = parts;
    if (String(t.directionName ?? dirName) !== dirName || String(t.pattern ?? alt) !== alt) tpl.warnings.push(`אי-התאמה בין Route Id לשדות כיוון/חלופה בנסיעה ${t.id}`);
    const stops = t.stops || []; if (stops.length < 2) fail(`נסיעה ${t.id} עם פחות משתי תחנות`);
    const times = stops.map(s => clockToMin(s.time)); for (let i = 1; i < times.length; i++) while (times[i] < times[i - 1]) times[i] += 1440;
    const dep = times[0], arr = times.at(-1);
    const row = [str(t.id), null, makat, str(t.sign), dirName, alt, str(stops[0].id), str(stops.at(-1).id), dep >= 1440 ? 1 : 0, dep, arr,
      (t.vehicleTypeIds || []).map(String).join(','), t.distance, String(days[0]), 0, 0, null, rid, null];
    const stopRows = stops.map((s, i) => [str(t.id), times[i], str(s.id), i === 0 ? 0 : (i === stops.length - 1 ? t.distance : (typeof s.distance === 'number' ? s.distance : null)), i, Boolean(s.isTimePoint)]);
    tpl.trips.push({ id: str(t.id), row, stops: stopRows, makat, dir: dirName, alt, routeId: rid, dep, arr, twoStops: stops.length === 2, vehicleId: str(t.vehicleId), dutyId: str(t.dutyId) });
    tpl.mapping.push([str(t.id), str(t.id)]);
  }
  const mapPlaces = (map.stops || []).map(s => [str(s.id), s.name ?? null, null, typeof s.lat === 'number' ? s.lat : null, typeof s.long === 'number' ? s.long : null, null]);
  if (Array.isArray(placesOriginal) && placesOriginal.length) {
    tpl.places = placesOriginal.map(r => r.slice());
    const have = new Set(tpl.places.map(p => String(p[0])));
    const added = mapPlaces.filter(p => !have.has(p[0])); tpl.places.push(...added);
    const cat = new Map(tpl.places.map(p => [String(p[0]), p]));
    const nameDiff = mapPlaces.filter(p => cat.has(p[0]) && cat.get(p[0])[1] !== p[1]).map(p => p[0]);
    tpl.placesInfo = { mode: 'original', label: meta.placesOriginalLabel || 'קטלוג מקורי', original_rows: placesOriginal.length, added_from_map: added.map(p => p[0]), map_name_differs: nameDiff,
      note: `קטלוג Places המקורי נשמר כמו שהוא (${placesOriginal.length} שורות)` + (added.length ? `; נוספו ${added.length} נקודות מהמפה` : '') + (nameDiff.length ? `; ב-${nameDiff.length} נקודות השם במפה שונה והקטלוג לא שוכתב` : '') };
  } else {
    tpl.places = mapPlaces;
    tpl.placesInfo = { mode: 'map', label: 'map.stops', original_rows: 0, added_from_map: [], map_name_differs: [], note: `אין קטלוג Places מקורי ללוח זה; נבנה מ-${mapPlaces.length} התחנות של המפה` };
  }
  for (const v of map.vehicleTypes || []) tpl.vehicleTypes.push([str(v.id), v.description ?? str(v.id), 'None']);
  tpl.chains = chainsFromMap(map);
  signConsistency(tpl);
  return tpl;
}
/* From the five sheets of an existing UPDATE TRIPS workbook (rows as arrays; header row first; times as Excel day fractions). */
function templateFromUpdateTrips(sheets, meta = {}) {
  for (const n of SHEETS) if (!Array.isArray(sheets[n])) fail(`גיליון חסר: ${n}`);
  const eq = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
  if (!eq(sheets.Trips[0], TRIPS_HEADERS)) fail('כותרות Trips אינן תואמות לתבנית המאומתת');
  if (!eq(sheets.StopTimes[0], STOP_HEADERS)) fail('כותרות StopTimes אינן תואמות');
  if (!eq(sheets.Places[0], PLACES_HEADERS)) fail('כותרות Places אינן תואמות');
  if (!eq(sheets.VehicleTypes[0], VT_HEADERS)) fail('כותרות VehicleTypes אינן תואמות');
  if (!eq(sheets.TripIdsMapping[0], MAP_HEADERS)) fail('כותרות TripIdsMapping אינן תואמות');
  const stopsBy = new Map();
  for (const r of sheets.StopTimes.slice(1)) { const k = str(r[0]); if (!stopsBy.has(k)) stopsBy.set(k, []); stopsBy.get(k).push(r.slice()); }
  const tpl = { kind: 'update_trips', source: { ...meta }, trips: [], places: sheets.Places.slice(1).map(r => r.slice()), vehicleTypes: sheets.VehicleTypes.slice(1).map(r => r.slice()), mapping: sheets.TripIdsMapping.slice(1).map(r => r.slice()), warnings: [], chains: null,
    placesInfo: { mode: 'original', label: 'Places של הקובץ עצמו', original_rows: sheets.Places.length - 1, added_from_map: [], map_name_differs: [], note: 'Places של הקובץ נשמרו כמו שהם' } };
  const dset = new Set();
  for (const r0 of sheets.Trips.slice(1)) {
    const row = r0.slice(); const id = str(row[T.Id]); const st = (stopsBy.get(id) || []).sort((a, b) => a[4] - b[4]);
    if (st.length < 2) fail(`נסיעה ${id} בלי שתי שורות StopTimes`);
    const off = Number(row[T['Day Offset']]) || 0;
    const dep = fracToMin(row[T.Departure]) + 1440 * off;
    let arr = fracToMin(row[T.Arrival]) + 1440 * off; while (arr < dep) arr += 1440;
    let prev = dep; const stops = st.map((s, i) => { let m = fracToMin(s[1]) + 1440 * off; while (m < prev) m += 1440; prev = m; const o = s.slice(); o[1] = m; return o; });
    row[T.Departure] = dep; row[T.Arrival] = arr; dset.add(String(row[T.Days]));
    tpl.trips.push({ id, row, stops, makat: str(row[T['Catalog Number']]), dir: str(row[T.Direction]), alt: str(row[T.Alternative]), routeId: str(row[T['Route Id']]), dep, arr, twoStops: st.length === 2, vehicleId: null, dutyId: null });
  }
  tpl.days = dset.size === 1 ? [...dset][0] : null;
  signConsistency(tpl);
  return tpl;
}
/* Optibus rejected routes whose Sign differed from the route template (task 027): flag mixed Sign within one Route Id. */
function signConsistency(tpl) {
  const by = new Map(); for (const t of tpl.trips) { const s = String(t.row[T.Sign]); if (!by.has(t.routeId)) by.set(t.routeId, new Set()); by.get(t.routeId).add(s); }
  tpl.mixedSign = [...by].filter(([, v]) => v.size > 1).map(([k, v]) => ({ routeId: k, signs: [...v].sort() }));
  for (const m of tpl.mixedSign) tpl.warnings.push(`Route Id ${m.routeId}: ערכי Sign שונים (${m.signs.join('/')}) — סיכון לדחיית ייבוא כמו בקובץ הייצוא הקודם`);
}
/* Sign of the target template vs another file's Trips rows (e.g. the historical load file). Diff is recorded, never "fixed". */
function signDiff(tpl, tripsRows, label) {
  const other = new Map(tripsRows.slice(1).map(r => [String(r[T.Id]), r[T.Sign] == null ? null : String(r[T.Sign])]));
  const diffs = []; let compared = 0, missing = 0;
  for (const t of tpl.trips) {
    if (!other.has(t.id)) { missing++; continue; } compared++;
    const a = String(t.row[T.Sign]), b = other.get(t.id); if (a !== b) diffs.push({ id: t.id, routeId: t.routeId, target: a, other: b });
  }
  return { label, compared, missing_in_other: missing, only_in_other: [...other.keys()].filter(id => !tpl.trips.some(t => t.id === id)).length, diffs };
}
/* Vehicle and duty event chains (minutes) for continuity checks; only a map JSON carries them. */
function chainsFromMap(map) {
  const ev = new Map();
  const put = (type, e) => { const st = (e.stops || []).map(s => clockToMin(s.time)); if (!st.length) return; for (let i = 1; i < st.length; i++) while (st[i] < st[i - 1]) st[i] += 1440; ev.set(`${type}|${e.id}`, { start: st[0], end: st.at(-1) }); };
  for (const type of ['trips', 'deadheads']) for (const e of map.events?.[type] || []) put(type, e);
  const chain = owners => (owners || []).map(o => ({ id: str(o.id), events: (o.events || []).filter(e => ev.has(`${e.type}|${e.id}`)).map(e => ({ type: e.type, id: str(e.id), ...ev.get(`${e.type}|${e.id}`) })) }));
  const fix = c => { for (const o of c) { let prev = null; for (const e of o.events) { if (prev != null) while (e.start < prev - 720) { e.start += 1440; e.end += 1440; } prev = e.start; } } return c; };
  return { vehicles: fix(chain(map.vehicles)), duties: fix(chain(map.duties)) };
}

/* ---------- target eligibility ----------
   A load file may be called a SCENARIO READY FOR LOADING TO A COPY only with evidence for (1) the target dates and (2) that the day
   code (Days) of the target map represents those dates. Otherwise it is an example not for loading. This tool never produces an
   operationally approved schedule: that requires an Optibus re-run (vehicles + duties), Reg-168 and planner approval. */
function eligibility(tpl, ev = {}) {
  const reasons = [];
  const dates = Array.isArray(ev.dates) ? ev.dates : [];
  if (!dates.length) reasons.push('לא נמסרו תאריכי יעד');
  else if (!ev.dates_verified) reasons.push(`תאריכי היעד (${dates.join(', ')}) לא אומתו${ev.dates_source ? ' — מקור: ' + ev.dates_source : ''}`);
  const dc = ev.day_code || {};
  if (!dc.verified) reasons.push(`אין ראיה שקוד היום Days=${tpl.days ?? '?'} במפת היעד מייצג את תאריכי היעד${dc.note ? ' (' + dc.note + ')' : ''}`);
  else if (String(dc.code) !== String(tpl.days)) reasons.push(`קוד היום המאומת (${dc.code}) שונה מ-Days=${tpl.days} במפת היעד`);
  const ready = reasons.length === 0;
  return { ready, status: ready ? 'תרחיש להרצה מחדש — כשיר לטעינה לעותק Optibus' : 'דוגמה — לא לטעינה', tag: ready ? 'תרחיש_להרצה_מחדש' : 'דוגמה_לא_לטעינה', reasons,
    days: tpl.days, service_name: tpl.serviceName ?? null, dates, dates_source: ev.dates_source || null,
    days_note: `בקובץ Days=${tpl.days} לכל הנסיעות: זה קוד היום של מפת היעד${tpl.serviceName ? ` (service "${tpl.serviceName}")` : ''}, והוא נלקח ממנה כמו שהוא. קובץ UPDATE TRIPS אינו נושא תאריכים; ` +
      `באילו תאריכים הלוח פועל נקבע בהפעלת המפה (TBG), מחוץ לקובץ. ${dates.length ? `טווח היעד ${dates[0]}${dates.length > 1 ? '–' + dates.at(-1) : ''} ` : ''}` +
      (dc.verified ? 'קוד היום אומת מול היעד.' : 'לא אומת שקוד היום מייצג את טווח היעד.'),
    separation: 'קובץ הטעינה הוא נתוני תרחיש להרצה ולשיבוץ מחדש באופטיבוס. הוא אינו סידור מאושר להפעלה: שיבוץ הרכבים והנהגים הישן אינו תקף לנסיעות ששונו.' };
}

/* ---------- recommendations ---------- */
const DEFAULT_POLICY = { autoTiers: ['A'], allowCuts: false, minChangeMin: 3, minDurationMin: 3, maxDurationMin: 240, blocked: [], decisionStability: true };
/* P80 values of the stability variants carried by a rec (holiday model: stability.variants; screen 4: previous month). */
function variantP80s(rec) {
  const s = rec && rec.stability; if (!s) return [];
  if (s.variants) return Object.entries(s.variants).map(([k, v]) => [k, v.p80]);
  if (s.prev_p80 != null) return [[`חודש קודם ${s.prev_month || ''}`.trim(), s.prev_p80]];
  return [];
}
function blockedBy(policy, t) { return (policy.blocked || []).find(b => String(b.makat) === t.makat && (b.direction == null || String(b.direction) === t.dir) && (b.alt == null || String(b.alt) === t.alt)); }
/* rec match: {trip_id} or {makat,direction,alt,departure:'HH:MM' (≥24 allowed)} or cell {makat,direction,alt,hour} */
function matchTrips(tpl, rec) {
  if (rec.trip_id) return tpl.trips.filter(t => t.id === String(rec.trip_id));
  const base = t => t.makat === String(rec.makat) && t.dir === String(rec.direction) && t.alt === String(rec.alt);
  if (rec.departure != null) { const d = clockToMin(rec.departure); return tpl.trips.filter(t => base(t) && t.dep === d); }
  if (rec.hour != null) return tpl.trips.filter(t => base(t) && Math.floor((t.dep % 1440) / 60) === Number(rec.hour));
  fail('המלצה ללא מפתח זהות (trip_id / departure / hour)');
}
const recKey = r => `${r.tier}|${Number.isFinite(r.minutes) ? Math.ceil(r.minutes) : 'null'}|${!!r.envelope}`;
function applyRecommendations(tpl, recs, policyIn = {}) {
  const policy = { ...DEFAULT_POLICY, ...policyIn };
  const decisions = []; const byTrip = new Map(); const unmatched = [];
  for (const rec of recs) {
    const ts = matchTrips(tpl, rec);
    if (!ts.length) { unmatched.push({ rec, reason: 'אין נסיעה תואמת בלוח היעד (מק״ט, כיוון, חלופה ושעה)' }); continue; }
    for (const t of ts) { if (!byTrip.has(t.id)) byTrip.set(t.id, { t, recs: [] }); byTrip.get(t.id).recs.push(rec); }
  }
  const conflicts = [];
  for (const { t, recs: rs } of byTrip.values()) {
    const prev = t.arr - t.dep;
    if (rs.length > 1) {
      /* more than one recommendation for the same trip: never pick one silently — block and report */
      const agree = new Set(rs.map(recKey)).size === 1;
      const vals = rs.map(r => `${r.tier}:${Number.isFinite(r.minutes) ? Math.ceil(r.minutes) : '—'}`).join(' / ');
      const d = { trip: t, rec: rs[0], recs: rs, prev, want: null, action: 'unchanged', code: 'rec_conflict', reason: `${rs.length} המלצות לאותה נסיעה (${vals})${agree ? ' — זהות בערכן' : ' — סותרות'}; השינוי נחסם עד הכרעה` };
      decisions.push(d); conflicts.push({ trip: t.id, count: rs.length, agree, values: vals }); continue;
    }
    const rec = rs[0]; const want = Number.isFinite(rec.minutes) ? Math.ceil(rec.minutes) : null;
    const d = { trip: t, rec, prev, want, action: 'unchanged', code: '', reason: '' };
    const blk = blockedBy(policy, t);
    if (blk) { d.code = 'blocked'; d.reason = blk.reason || 'חסום במדיניות'; }
    else if (!t.twoStops) { d.code = 'blocked_intermediate'; d.reason = 'לנסיעה יותר משתי תחנות; אין זמני ביניים מאומתים ולכן אין שינוי'; }
    else if (want == null) { d.code = 'no_evidence'; d.reason = rec.reason || 'אין בסיס ראיות; הלוח הקיים נשמר'; }
    else if (!policy.autoTiers.includes(rec.tier)) { d.code = 'review'; d.reason = `רמת ראיה ${rec.tier || 'לא ידועה'}: לבדיקה, לא אוטומטי` + (rec.reason ? ` · ${rec.reason}` : ''); }
    else if (want < policy.minDurationMin || want > policy.maxDurationMin) { d.code = 'out_of_range'; d.reason = `משך מומלץ ${want} מחוץ לטווח ${policy.minDurationMin}–${policy.maxDurationMin}`; }
    else if (Math.abs(want - prev) < policy.minChangeMin) { d.code = 'negligible'; d.reason = `הפרש קטן מ־${policy.minChangeMin} דק׳`; }
    else if (want < prev && rec.envelope) { d.code = 'cut_envelope'; d.reason = 'פיזור חציוני הימים רחב (מעטפת): קיצור לעולם אינו מוחל'; }
    else if (want < prev && !policy.allowCuts) { d.code = 'cut_review'; d.reason = 'קיצור: מוצג לבדיקה, אינו מוחל אוטומטית'; }
    else {
      /* decision stability (council 23.9): the P80 tolerance (≥5 min) is wider than the minimum change (3 min), so a stable P80 can
         still flip the decision. Every variant must recommend the same direction of change by at least minChangeMin for THIS trip. */
      const vs = policy.decisionStability ? variantP80s(rec) : [];
      const flip = vs.filter(([, p]) => want > prev ? Math.ceil(p) - prev < policy.minChangeMin : prev - Math.ceil(p) < policy.minChangeMin);
      if (flip.length) { d.code = 'unstable_decision'; d.reason = `ההחלטה אינה יציבה: ב-${flip.length} מתוך ${vs.length} וריאנטים השינוי קטן מ-${policy.minChangeMin} דק׳ או הפוך (${flip.slice(0, 3).map(([k, p]) => `${k}: ${Math.ceil(p)}`).join(', ')}; זמן קודם ${prev}). לבדיקה`; }
      else { d.action = 'change'; d.code = want > prev ? 'extend' : 'cut'; d.reason = rec.reason || ''; }
    }
    decisions.push(d);
  }
  /* produce modified copies */
  const out = tpl.trips.map(t => ({ ...t, row: t.row.slice(), stops: t.stops.map(s => s.slice()) }));
  const byId = new Map(out.map(t => [t.id, t]));
  for (const d of decisions) if (d.action === 'change') {
    const t = byId.get(d.trip.id); const arr = t.dep + d.want;
    t.arr = arr; t.row[T.Arrival] = arr; t.stops[t.stops.length - 1][1] = arr;
  }
  const untouched = tpl.trips.filter(t => !byTrip.has(t.id));
  return { trips: out, decisions, unmatched, untouched, conflicts, policy };
}

/* ---------- continuity (scenario diagnostics, never a reason to shorten) ----------
   For every consecutive pair (event → next event) in each vehicle chain and each duty chain: overlap BEFORE (source schedule) and
   AFTER (recommended durations). Categories: existing_unchanged, existing_worsened, existing_eased, new. Vehicle and duty pairs are
   counted separately, and unique trips are counted once across both — rows are never summed as if they were distinct events. */
function continuity(tpl, result) {
  if (!tpl.chains) return { available: false, note: 'אין שיבוץ רכבים/סידורים בלוח המקור (קובץ UPDATE TRIPS); בדיקת רציפות לא בוצעה', rows: [], summary: null };
  const delta = new Map(result.decisions.filter(d => d.action === 'change').map(d => [d.trip.id, d.want - d.prev]));
  const rows = []; const sum = {}; const uniq = { existing_unchanged: new Set(), existing_worsened: new Set(), existing_eased: new Set(), new: new Set() };
  let pairsChecked = 0;
  for (const kind of ['vehicles', 'duties']) {
    const s = sum[kind] = { pairs: 0, before: 0, after: 0, existing_unchanged: 0, existing_worsened: 0, existing_eased: 0, new: 0 };
    for (const o of tpl.chains[kind]) o.events.forEach((e, i) => {
      const next = o.events[i + 1]; if (!next) return; s.pairs++; pairsChecked++;
      const endBefore = e.end, endAfter = e.type === 'trips' && delta.has(e.id) ? e.end + delta.get(e.id) : e.end;
      const ob = Math.max(0, endBefore - next.start), oa = Math.max(0, endAfter - next.start);
      if (ob > 0) s.before++; if (oa > 0) s.after++;
      let cat = null;
      if (ob > 0 && oa === ob) cat = 'existing_unchanged'; else if (ob > 0 && oa > ob) cat = 'existing_worsened'; else if (ob > 0 && oa < ob) cat = 'existing_eased'; else if (ob === 0 && oa > 0) cat = 'new';
      if (!cat) return; s[cat]++; uniq[cat].add(`${e.type}|${e.id}`);
      rows.push({ kind: kind === 'vehicles' ? 'רכב' : 'סידור', owner: o.id, event: e.type === 'trips' ? e.id : `ריקה ${e.id}`, changed: delta.has(e.id), end_before: hhmm(endBefore), end_after: hhmm(endAfter),
        next_event: next.type === 'trips' ? `נסיעה ${next.id}` : 'ריקה/מעבר', next_start: hhmm(next.start), overlap_before: ob, overlap_after: oa, category: cat });
    });
  }
  const summary = { by_kind: sum, unique_events: Object.fromEntries(Object.entries(uniq).map(([k, v]) => [k, v.size])), pairs_checked: pairsChecked };
  return { available: true, rows, summary, conflicts: rows.filter(r => r.category === 'new' || r.category === 'existing_worsened'),
    note: 'השוואת זמנים בלבד: סוף אירוע מול תחילת האירוע הבא באותו רכב/סידור, לפני ואחרי. חפיפה צפויה בתרחיש להרצה מחדש ואינה סיבה לקצר משך מומלץ. אינה אופטימיזציה ואינה בדיקת תקנה 168.' };
}

/* ---------- impact ---------- */
function impact(result, cont) {
  const ch = result.decisions.filter(d => d.action === 'change');
  const byLine = new Map();
  for (const d of ch) { const k = `${d.trip.makat}|${d.trip.dir}|${d.trip.alt}`; const v = byLine.get(k) || { makat: d.trip.makat, dir: d.trip.dir, alt: d.trip.alt, trips: 0, minutes: 0 }; v.trips++; v.minutes += d.want - d.prev; byLine.set(k, v); }
  const codes = {}; for (const d of result.decisions) codes[d.code] = (codes[d.code] || 0) + 1;
  return { trips_total: result.trips.length, changed: ch.length, added_minutes: ch.reduce((s, d) => s + d.want - d.prev, 0), decisions_by_code: codes, unmatched_recs: result.unmatched.length,
    rec_conflicts: result.conflicts.length, untouched_trips: result.untouched.length, continuity: cont.available ? cont.summary : null, by_line: [...byLine.values()].sort((a, b) => b.minutes - a.minutes) };
}

/* ---------- OOXML writer (shared strings, typed cells) ---------- */
const NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main', REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const xmlEsc = x => String(x).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const colName = n => { let s = ''; for (n++; n; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + (n - 1) % 26) + s; return s; };
function crcTable() { return Array.from({ length: 256 }, (_, i) => { for (let j = 0; j < 8; j++) i = i & 1 ? 0xedb88320 ^ (i >>> 1) : i >>> 1; return i >>> 0; }); }
const CRC = crcTable();
function crc32(b) { let x = 0xffffffff; for (const v of b) x = CRC[(x ^ v) & 255] ^ (x >>> 8); return (x ^ 0xffffffff) >>> 0; }
function zip(files) {
  const enc = new TextEncoder(), parts = [], central = []; let off = 0;
  for (const [name, body] of Object.entries(files)) {
    const n = enc.encode(name), b = typeof body === 'string' ? enc.encode(body) : body, c = crc32(b);
    const h = new Uint8Array(30), d = new DataView(h.buffer); d.setUint32(0, 0x04034b50, true); d.setUint16(4, 20, true); d.setUint16(6, 0x800, true); d.setUint16(12, 33, true); d.setUint32(14, c, true); d.setUint32(18, b.length, true); d.setUint32(22, b.length, true); d.setUint16(26, n.length, true);
    parts.push(h, n, b);
    const e = new Uint8Array(46), v = new DataView(e.buffer); v.setUint32(0, 0x02014b50, true); v.setUint16(4, 20, true); v.setUint16(6, 20, true); v.setUint16(8, 0x800, true); v.setUint16(14, 33, true); v.setUint32(16, c, true); v.setUint32(20, b.length, true); v.setUint32(24, b.length, true); v.setUint16(28, n.length, true); v.setUint32(42, off, true);
    central.push(e, n); off += 30 + n.length + b.length;
  }
  const size = central.reduce((s, b) => s + b.length, 0), end = new Uint8Array(22), d = new DataView(end.buffer);
  d.setUint32(0, 0x06054b50, true); d.setUint16(8, Object.keys(files).length, true); d.setUint16(10, Object.keys(files).length, true); d.setUint32(12, size, true); d.setUint32(16, off, true);
  const out = new Uint8Array(off + size + 22); let p = 0; for (const b of [...parts, ...central, end]) { out.set(b, p); p += b.length; } return out;
}
/* styles: 0 General · 1 hh:mm · 2 0.000 · 3 0.000000 · 4 header bold · 5 wrapped text (report) · 6 title (report) · 7 block (report) · 8 0.0 */
const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="${NS}"><numFmts count="4"><numFmt numFmtId="164" formatCode="hh:mm"/><numFmt numFmtId="165" formatCode="0.000"/><numFmt numFmtId="166" formatCode="0.000000"/><numFmt numFmtId="167" formatCode="0.0"/></numFmts><fonts count="4"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="13"/><name val="Arial"/></font><font><b/><sz val="11"/><color rgb="FFC00000"/><name val="Arial"/></font></fonts><fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF31424E"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEEF3F6"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFC7D3DB"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="11"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="1" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="right" vertical="top" wrapText="1" readingOrder="2"/></xf><xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1" readingOrder="2"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment horizontal="right" vertical="top" wrapText="1" readingOrder="2"/></xf><xf numFmtId="167" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top" readingOrder="2"/></xf><xf numFmtId="167" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top" wrapText="1" readingOrder="2"/></xf><xf numFmtId="0" fontId="1" fillId="4" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1" readingOrder="2"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
function workbook(sheets, opts = {}) {
  /* sheets: [{name, rows:[[cell]], styles?:(r,c,v)=>styleId, rtl?, freeze?, widths?, merges?}] ; cell: string|number|boolean|null */
  const sst = [], sstIdx = new Map(); const si = s => { if (!sstIdx.has(s)) { sstIdx.set(s, sst.length); sst.push(s); } return sstIdx.get(s); };
  const files = {};
  sheets.forEach((sh, k) => {
    let data = '';
    sh.rows.forEach((row, r) => {
      let cells = '';
      row.forEach((v, c) => {
        if (v == null || v === '') return; const ref = `${colName(c)}${r + 1}`; const s = sh.styles ? sh.styles(r, c, v) : 0; const sa = s ? ` s="${s}"` : '';
        if (typeof v === 'boolean') cells += `<c r="${ref}"${sa} t="b"><v>${v ? 1 : 0}</v></c>`;
        else if (typeof v === 'number') { if (!Number.isFinite(v)) return; cells += `<c r="${ref}"${sa}><v>${v}</v></c>`; }
        else cells += `<c r="${ref}"${sa} t="s"><v>${si(String(v))}</v></c>`;
      });
      const ht = sh.heights && sh.heights[r] ? ` ht="${sh.heights[r]}" customHeight="1"` : '';
      data += `<row r="${r + 1}"${ht}>${cells}</row>`;
    });
    const view = `<sheetViews><sheetView workbookViewId="0"${sh.rtl ? ' rightToLeft="1"' : ''}${k === 0 ? ' tabSelected="1"' : ''}>${sh.freeze ? `<pane ySplit="${sh.freeze - 1}" topLeftCell="A${sh.freeze}" activePane="bottomLeft" state="frozen"/>` : ''}</sheetView></sheetViews>`;
    const cols = sh.widths ? `<cols>${sh.widths.map((w, i) => `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`).join('')}</cols>` : '';
    const merges = sh.merges && sh.merges.length ? `<mergeCells count="${sh.merges.length}">${sh.merges.map(m => `<mergeCell ref="${m}"/>`).join('')}</mergeCells>` : '';
    const tab = sh.tab ? `<sheetPr><tabColor rgb="FF${sh.tab}"/></sheetPr>` : '';
    files[`xl/worksheets/sheet${k + 1}.xml`] = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="${NS}" xmlns:r="${REL}">${tab}${view}${cols}<sheetData>${data}</sheetData>${merges}</worksheet>`;
  });
  files['xl/sharedStrings.xml'] = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="${NS}" count="${sst.length}" uniqueCount="${sst.length}">${sst.map(s => `<si><t xml:space="preserve">${xmlEsc(s)}</t></si>`).join('')}</sst>`;
  files['xl/styles.xml'] = STYLES;
  files['xl/workbook.xml'] = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="${NS}" xmlns:r="${REL}"><bookViews><workbookView/></bookViews><sheets>${sheets.map((s, i) => `<sheet name="${xmlEsc(s.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('')}</sheets></workbook>`;
  files['xl/_rels/workbook.xml.rels'] = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="${REL}/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('')}<Relationship Id="rId${sheets.length + 1}" Type="${REL}/styles" Target="styles.xml"/><Relationship Id="rId${sheets.length + 2}" Type="${REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>`;
  files['_rels/.rels'] = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
  files['[Content_Types].xml'] = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>${sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`;
  return zip(files);
}

/* Clean load file: exactly the five proven sheets, headers and value types; no report columns. */
function loadSheets(tpl, trips) {
  const tripsRows = [TRIPS_HEADERS, ...trips.map(t => { const r = t.row.slice(); r[T.Departure] = minToFrac(t.dep); r[T.Arrival] = minToFrac(t.arr); return r; })];
  const stopRows = [STOP_HEADERS, ...trips.flatMap(t => t.stops.map(s => { const o = s.slice(); o[1] = minToFrac(s[1]); return o; }))];
  const ids = new Set(); for (const t of trips) { if (ids.has(t.id)) fail(`Trip Id כפול: ${t.id}`); ids.add(t.id); }
  const places = new Set(tpl.places.map(p => String(p[0])));
  for (const t of trips) for (const s of t.stops) if (!places.has(String(s[2]))) fail(`נקודה ${s[2]} של נסיעה ${t.id} חסרה ב-Places`);
  const vts = new Set(tpl.vehicleTypes.map(v => String(v[0])));
  for (const t of trips) for (const v of String(t.row[T['Vehicle Type Ids']] || '').split(',').filter(Boolean)) if (!vts.has(v)) fail(`סוג רכב ${v} חסר ב-VehicleTypes`);
  return [
    { name: 'Trips', rows: tripsRows, freeze: 2, styles: (r, c) => r === 0 ? 0 : (c === T.Departure || c === T.Arrival ? 1 : 0) },
    { name: 'StopTimes', rows: stopRows, freeze: 2, styles: (r, c) => r === 0 ? 0 : (c === 1 ? 1 : c === 3 ? 2 : 0) },
    { name: 'Places', rows: [PLACES_HEADERS, ...tpl.places], freeze: 2, styles: (r, c) => r === 0 ? 0 : (c === 3 || c === 4 ? 3 : 0) },
    { name: 'VehicleTypes', rows: [VT_HEADERS, ...tpl.vehicleTypes] },
    { name: 'TripIdsMapping', rows: [MAP_HEADERS, ...trips.map(t => [t.id, t.id])] },
  ];
}
function buildLoadFile(tpl, trips) { return workbook(loadSheets(tpl, trips)); }

/* Separate formatted change report (Hebrew, RTL). ctx.screen = 'holiday' (screen 5) | 'duty' (screen 4) selects the glossary. */
const GLOSSARY_COMMON = [
  ['P80 — בפשטות', 'האחוזון ה-80 של משכי הנסיעה המדווחים, מחושב באינטרפולציה לינארית. לדוגמה: P80 של 60 דקות הוא הסף שמעליו נמצאות בקירוב הנסיעות הארוכות ביותר; השיעור המדויק של הנסיעות שאינן עולות עליו מחושב בנפרד בכרטיס. זו הצעה לתכנון, לא הבטחה לנסיעה הבאה.'],
  ['איך קוראים את הרמות?', 'A: מספיק נתונים ותוצאה עקבית; שינוי יוחל רק אם עבר גם את יתר הבדיקות. R: העקביות דורשת בדיקה. B או C: לבדיקה בלבד. D: אין בסיס מספיק. זמן שלא אושר לשינוי נשמר כפי שהיה. התנאים המדויקים מפורטים בהמשך המונחון.'],
  ['UPDATE TRIPS', 'טעינת נסיעות מלאה ללוח קיים. נסיעה שחסרה בקובץ נמחקת, ולכן הקובץ כולל את כל הלוח. משתנים רק זמן ההגעה ושורת היעד ב-StopTimes.'],
  ['מעמד הקובץ', '"דוגמה — לא לטעינה" כשאין ראיה לתאריכי היעד ולקוד היום. "תרחיש להרצה מחדש — כשיר לטעינה לעותק" רק עם שתי הראיות. הכלי לעולם אינו מפיק "סידור מאושר להפעלה": זה דורש הרצת רכבים ונהגים באופטיבוס, בדיקת תקנה 168 ואישור מתכנן.'],
  ['Days', 'קוד היום של מפת היעד, מועתק כמו שהוא לכל הנסיעות. הקובץ אינו נושא תאריכים; תאריכי ההפעלה נקבעים בהפעלת המפה (TBG).'],
  ['זמן קודם / מומלץ', 'משך הנסיעה בדקות בלוח היעד / לפי ההמלצה (מעוגל כלפי מעלה לדקה). שינוי שהוחל = מומלץ פחות קודם.'],
  ['חפיפה לפני / אחרי', 'דקות שבהן סוף אירוע עובר את תחילת האירוע הבא באותו רכב או סידור — בשיבוץ הישן (לפני) ועם המשכים המומלצים (אחרי). חפיפה צפויה בתרחיש להרצה מחדש ואינה סיבה לקצר.'],
  ['קטגוריית חפיפה', 'קיימת ללא שינוי / קיימת שהוחמרה / קיימת שהוקלה / חדשה. רכב וסידור נספרים בנפרד; "אירועים ייחודיים" סופרים כל נסיעה פעם אחת.'],
  ['קוד החלטה', 'extend/cut = הוחל · review = רמת ראיה לא אוטומטית · cut_review = קיצור לבדיקה · cut_envelope = קיצור על מעטפת, לעולם לא · blocked = חסום במדיניות · blocked_intermediate = יותר משתי תחנות · negligible = פחות מ-3 דק׳ · out_of_range = מחוץ ל-3–240 · no_evidence = אין בסיס · rec_conflict = יותר מהמלצה אחת לנסיעה, נחסם · unstable_decision = באחד מווריאנטי היציבות השינוי לנסיעה זו קטן מ-3 דק׳ או הפוך, לבדיקה'],
  ['תא ריק', 'חסר או לא רלוונטי. לא אפס.']];
const GLOSSARY = {
  holiday: [
    ['יחידת הראיה (מסך 5, מושהה מהתצוגה כרגע)', 'חלון שעות: שעת היציאה ±1 (למשל 08–10 לשעה 09) של מק״ט·כיוון·חלופה, מנסיעות BI גולמיות בימי אותו שלב חג. זה מודל תכנון נפרד; טבלאות ההשוואה היומיות לא משתנות ולא מאוגמות.'],
    ['n בשעה / n בחלון / ימים', 'נסיעות בשעת היציאה עצמה / בכל החלון (אחרי איחוד ימים דומים ברמה B) / מספר ימי השירות במאגר.'],
    ['P80 חלון', 'האחוזון ה-80 של כל הנסיעות הגולמיות בחלון. אינו P80 של תא שעה בודד ואינו ממוצע אחוזונים.'],
    ['איגום', 'לכל המלצה רשום אילו ימים ושעות אוגמו ולמה. שעות שכנות מאוגמות רק כשחציון השעה בתוך max(5 דק׳, 10%) מחציון החלון ו-P80 יציב.'],
    ['יציבות P80', 'ה-P80 מחושב מחדש: השעה לבדה, החלון בלי השעה הקודמת, בלי השעה הבאה, ובלי כל יום בתורו. יציב = כל הסטיות עד max(5 דק׳, 10% מה-P80). נקבע לפני הרצה; max(3, 5%) מוצג כרגישות בלבד.'],
    ['יציבות החלטה', 'בנוסף, לכל נסיעה: בכל וריאנט, ceil(P80 הווריאנט) פחות הזמן הקודם חייב להיות הארכה של 3 דק׳ לפחות. אחרת unstable_decision, לבדיקה. בדיקת היום-בחוץ על 3 ימים בודקת שאין יום שולט; היא אינה אומדן שונות.'],
    ['פיזור חציוני ימים', 'ההפרש בין חציון היום הגבוה לנמוך (ימים עם 3 נסיעות לפחות). מעל max(8 דק׳, 15%) = "מעטפת": הימים שונים זה מזה וה-P80 מכסה את הגבוהים; קיצור לעולם לא מוחל, והימים אינם הומוגניים.'],
    ['רמה A', 'n בחלון ≥ 30, לפחות 3 ימים עם 3 נסיעות, לפחות 6 נסיעות בשעה עצמה, חציון השעה קרוב לחציון החלון, ו-P80 יציב. מוחל אוטומטית, הארכה בלבד.'],
    ['רמה R', 'עומד בכללי המדגם של A אבל ה-P80 לא יציב. תרחיש לבדיקה, לא אוטומטי.'],
    ['רמה B', 'המדגם של החג לבדו חסר; איחוד עם ימים דומים ממשפחה אחרת (חוה"מ פסח), רק בלי הסטה בין המשפחות. לבדיקה בלבד.'],
    ['רמה C / D', 'C: 10–29 נסיעות, או חציון השעה רחוק מהחלון, או הסטה בין משפחות — לבדיקה בלי ערך. D: פחות מ-10 — הלוח הקיים נשמר.'],
    ['מקור', 'BI (בקרת נסיעות) הוא מקור משלים. Open Bus הוא מקור הייחוס; הצלבה ברמת נסיעה לא בוצעה.']],
  duty: [
    ['יחידת הראיה (מסך 4)', 'תא חודש אחד: מק״ט·כיוון·חלופה·שעת יציאה, ימי שגרה של סוג היום, בחודש הסגור האחרון לפי הבסיס שנבחר במסך (לימודים או אחרון). אין חלון שעות, אין איגום חודשים ואין ימי חג.'],
    ['n בשעה / n בחלון / ימים', 'נסיעות בתא החודש של אותה שעה / לא רלוונטי (אין חלון) / ימי שירות בתא.'],
    ['P80 תא חודש', 'האחוזון ה-80 של הנסיעות בתא, כפי שמוצג באתר. אינו ממוצע אחוזונים.'],
    ['יציבות P80', 'P80 של התא מול אותו תא בחודש הסגור הקודם באותה עונה. יציב = הפרש עד max(5 דק׳, 10%). התאים באתר מצטברים, ולכן בדיקת השארת יום בחוץ אינה אפשרית כאן.'],
    ['יציבות החלטה', 'בנוסף, לכל נסיעה: גם לפי P80 של החודש הקודם ההארכה חייבת להיות 3 דק׳ לפחות. אחרת unstable_decision, לבדיקה.'],
    ['פיזור ימים', 'לא זמין בתא האתר.'],
    ['רמה A', 'לפחות 30 נסיעות ב-5 ימי שירות לפחות בתא החודש, P80 יציב מול החודש הקודם, ומדדי הפיזור החודשיים עברו את שערי הבירור. הארכה בלבד בתרחיש.'],
    ['רמה R', 'מדגם מספיק, אבל P80 לא יציב, אין חודש קודם או שהפיזור גבוה/חסר. תרחיש לבדיקה, ללא החלת זמן חדש.'],
    ['הסקה סטטיסטית בשגרה', 'ההסקה עדיין חקרנית. מועמד P80 אינו שינוי מאושר; גם כששערי המדגם והפיזור עוברים, הזמן הקיים נשמר. במסך אפשר לנתח את כל שעות הקו עם רווחי אי־ודאות ומבחן חליפיות ימים ותיקון Holm. אין הסקת סיבה.'],
    ['בירור פיזור בשגרה', 'IQR/חציון מעל 25% או P90−P80 מעל max(5 דקות, 10% מ־P80) עוצרים החלה; חסר עוצר גם הוא. ספי מדיניות ניסיוניים ולא מכוילים. אין מחיקת חריגים או תוספת דקות לפי IQR. פירוט המדדים והסיבה בנימוק.'],
    ['רמה B / D', 'B: 10–29 נסיעות — לבדיקה. D: אין תא שמיש — הלוח הקיים נשמר.']] };
const CAT_HE = { existing_unchanged: 'קיימת ללא שינוי', existing_worsened: 'קיימת שהוחמרה', existing_eased: 'קיימת שהוקלה', new: 'חדשה' };
function stabCell(r) {
  const s = r && r.stability; if (!s) return r && r.tier && ['A', 'R', 'B'].includes(r.tier) ? 'לא נבדקה' : null;
  if (s.max_abs_delta != null) return `סטייה מרבית ${s.max_abs_delta} (${s.worst}); סובלנות ${s.tol}; טווח P80 ${s.p80_range[0]}–${s.p80_range[1]}`;
  return `${s.prev_month}: P80 ${s.prev_p80} (הפרש ${s.delta >= 0 ? '+' : ''}${s.delta}; סובלנות ${s.tol})`;
}
function changeReportSheets(ctx, tpl, result, cont) {
  const imp = impact(result, cont); const el = ctx.eligibility || eligibility(tpl, {});
  const blockRows = (title, measure, how, legend, look) => [[title], [measure], [how], [legend], [look], []];
  const mk = (name, tab, head, rows, block, widths) => {
    const all = [...blockRows(...block), head, ...rows];
    return { name, tab, rtl: true, freeze: 8, rows: all, widths, merges: [1, 2, 3, 4, 5].map(i => `A${i}:${colName(Math.max(head.length, 4) - 1)}${i}`),
      heights: Object.fromEntries([[0, 26], [1, 34], [2, 34], [3, 34], [4, 34], [6, 44]]), styles: (r, c, v) => r === 0 ? 6 : r < 5 ? 7 : r === 6 ? 10 : (typeof v === 'number' && !Number.isInteger(v) ? 8 : 5) };
  };
  const ev = d => d.rec || {};
  const yes = b => b == null ? null : b ? 'כן' : 'לא';
  const decRow = d => [d.trip.id, d.trip.makat, d.trip.dir, d.trip.alt, hhmm(d.trip.dep), d.trip.dep >= 1440 ? 1 : 0, d.prev, d.want, Number.isFinite(ev(d).p80) ? 'P80' : null, d.action === 'change' ? d.want - d.prev : null,
    ev(d).tier || null, ev(d).n_hour ?? null, ev(d).n_window ?? null, ev(d).n_days ?? null, ev(d).window || null, ev(d).pooling || null, ev(d).spread ?? null, yes(ev(d).envelope),
    stabCell(ev(d)), yes(ev(d).stable), ev(d).source || null, ev(d).period || null, ev(d).basis || null, d.reason || null, d.code];
  const decHead = ['Trip Id', 'מק״ט', 'כיוון', 'חלופה', 'יציאה', 'Day Offset', 'זמן קודם (דק׳)', 'זמן מומלץ (דק׳)', 'אחוזון ששימש להצעה', 'שינוי שהוחל (דק׳)', 'רמת ראיה', 'n בשעה', 'n בחלון', 'ימים', 'חלון / תא', 'איגום',
    'פיזור חציוני ימים (דק׳)', 'מעטפת', 'יציבות P80', 'יציב', 'מקור', 'תקופה', 'בסיס תכנון', 'סיבה', 'קוד החלטה'];
  const w = [22, 9, 7, 7, 8, 8, 10, 10, 18, 10, 8, 8, 8, 7, 22, 40, 10, 8, 44, 7, 22, 22, 30, 60, 14];
  const ch = result.decisions.filter(d => d.action === 'change'), rv = result.decisions.filter(d => d.action !== 'change');
  const cs = cont.available ? cont.summary : null;
  const overlapRows = cs ? [
    ...['vehicles', 'duties'].map(k => { const s = cs.by_kind[k]; const he = k === 'vehicles' ? 'רכב' : 'סידור';
      return [`חפיפות ${he}: זוגות אירועים שנבדקו ${s.pairs}`, `לפני ${s.before} · אחרי ${s.after} · קיימות ללא שינוי ${s.existing_unchanged} · הוחמרו ${s.existing_worsened} · הוקלו ${s.existing_eased} · חדשות ${s.new}`]; }),
    ['אירועים ייחודיים עם חפיפה חדשה (רכב או סידור, כל אירוע פעם אחת)', cs.unique_events.new], ['אירועים ייחודיים עם חפיפה קיימת שהוחמרה', cs.unique_events.existing_worsened],
    ['אירועים ייחודיים עם חפיפה קיימת ללא שינוי (בשיבוץ הישן)', cs.unique_events.existing_unchanged]] : [['חפיפות', cont.note]];
  const sheets = [
    mk('0 איך לקרוא', '444444', ['נושא', 'הסבר'], [
      ['במילים פשוטות', 'הצעה לזמני נסיעה מעודכנים. כל הנסיעות ושעות היציאה נשמרות. מאריכים רק כשיש מספיק נתונים ותוצאה עקבית; אחרת הזמן הקיים נשמר. קיצורים לבדיקה בלבד.'],
      ['מה עושים עם התוצאה?', 'קודם מאמתים מפה, תאריך וסוג יום. אין לטעון קובץ המסומן דוגמה לא לטעינה. לאחר אישור טעינה לעותק עבודה, משבצים מחדש רכבים ונהגים באופטיבוס ובודקים חפיפות והפסקות לפני אישור להפעלה.'],
      ['סימון אדום', 'בגיליון שינויים, הזמן המומלץ והתוספת שהוחלה מוצגים באדום ובמודגש. אדום מציין שינוי מוצע בקובץ, לא טעות. האחוזון ששימש להצעה מופיע ליד הזמן.'],
      ['מה הקובץ', 'דוח שינויים נפרד מקובץ הטעינה. קובץ הטעינה עצמו (UPDATE TRIPS) אינו כולל עמודות הסבר.'],
      ['מעמד', el.status], ['למה', el.reasons.length ? el.reasons.join(' · ') : 'יש ראיה לתאריכי היעד ולקוד היום'], ['הפרדה', el.separation],
      ['לוח יעד', ctx.target || 'לא נמסר'], ['תאריכי יעד', ctx.target_date || 'לא נמסרו'], ['Days', el.days_note],
      ['מקור ההמלצות', ctx.source || 'לא נמסר'], ['מה השתנה', `${imp.changed} נסיעות מתוך ${imp.trips_total}; סך ${imp.added_minutes} דקות. רק זמן הגעה ושורת היעד ב-StopTimes.`],
      ['חפיפות', cs ? `חדשות: ${cs.unique_events.new} אירועים ייחודיים; קיימות שהוחמרו: ${cs.unique_events.existing_worsened}; קיימות ללא שינוי: ${cs.unique_events.existing_unchanged}. צפוי בתרחיש להרצה מחדש.` : cont.note],
      ['מה לא נבדק', 'לא בוצעה טעינה, אופטימיזציה או בדיקת תקנה 168.'],
      ['סדר קריאה', 'יעד וכשירות ← סיכום השפעה ← שינויים ← חסומים ולבדיקה ← רציפות ← Sign ו-Places ← ללא שינוי ← מקורות']],
      ['0 איך לקרוא', `דוח שינויים לקובץ טעינה של Optibus · ${ctx.screen === 'duty' ? 'מסך 4 (בודק סידור)' : 'מסך 5 (תכנון לחג, מושהה מהתצוגה כרגע)'}`, 'כל גיליון מתחיל בהסבר; השורה 7 היא כותרות.', 'מקרא: תא ריק = חסר, לא אפס.', 'התחל כאן.'], [24, 110]),
    mk('מונחון', '444444', ['מונח', 'הסבר'], [...(GLOSSARY[ctx.screen] || []), ...GLOSSARY_COMMON],
      ['מונחון', ctx.screen === 'duty' ? 'הגדרות מסך 4: תא חודש, ללא חלון' : 'הגדרות מסך 5 (מושהה מהתצוגה כרגע): חלון ±1 שעה, ימי אותו חג', 'מונח והסבר', '', ''], [24, 110]),
    mk('יעד וכשירות', '2C6E9E', ['שדה', 'ערך'], [['מעמד', el.status], ...el.reasons.map((r, i) => [`חסר ${i + 1}`, r]), ['Days במפת היעד', el.days ?? null], ['service', el.service_name],
      ['תאריכי יעד', el.dates.join(', ') || null], ['מקור התאריכים', el.dates_source], ['הסבר Days מול טווח היעד', el.days_note], ['הפרדה', el.separation]],
      ['יעד וכשירות', 'האם הקובץ כשיר לטעינה לעותק, ולמה', 'שורה = תנאי', 'כשיר רק עם ראיה לתאריכים ולקוד היום.', 'לעולם לא "סידור מאושר להפעלה".'], [26, 110]),
    mk('סיכום השפעה', '2C6E9E', ['מדד', 'ערך'], [
      ['נסיעות בלוח היעד', imp.trips_total], ['נסיעות ששונו', imp.changed], ['סך דקות שנוספו (נטו)', imp.added_minutes],
      ...Object.entries(imp.decisions_by_code).map(([k, v]) => [`החלטות: ${k}`, v]),
      ['המלצות ללא נסיעה תואמת', imp.unmatched_recs], ['נסיעות עם יותר מהמלצה אחת (נחסמו)', imp.rec_conflicts], ['נסיעות ללא המלצה (נשמרו כמו שהן)', imp.untouched_trips],
      ...overlapRows,
      ...imp.by_line.slice(0, 40).map(v => [`שינוי לפי מק״ט ${v.makat} כיוון ${v.dir} חלופה ${v.alt}`, `${v.trips} נסיעות, ${v.minutes} דק׳`])],
      ['סיכום השפעה', 'השפעת השינויים על הלוח', 'שורה = מדד', 'חפיפות: רכב וסידור בנפרד; אירועים ייחודיים נספרים פעם אחת.', 'לא נבדקו עלות ותקנה 168.'], [52, 70]),
    mk('שינויים', 'C0504D', decHead, ch.map(decRow), ['שינויים שהוחלו', 'נסיעות שזמן ההגעה שלהן שונה בקובץ הטעינה', 'שורה = נסיעה', 'אדום ומודגש = זמן ששונה ותוספת שהוחלה; חיובי = הארכה. לא ציון טעות.', 'בדוק n בשעה, n בחלון ויציבות.'], w),
    mk('חסומים ולבדיקה', 'E67E22', decHead, rv.map(decRow), ['חסומים ולבדיקה', 'המלצות שלא הוחלו ומדוע', 'שורה = נסיעה', 'הלוח הקיים נשמר בכל השורות האלה.', 'קוד ההחלטה מסביר.'], w),
    mk('רציפות', '9B59B6', ['קטגוריה', 'סוג', 'מזהה רכב/סידור', 'אירוע', 'שונה?', 'סוף לפני', 'סוף אחרי', 'אירוע הבא', 'תחילת אירוע הבא', 'חפיפה לפני (דק׳)', 'חפיפה אחרי (דק׳)'],
      cont.available ? cont.rows.map(c => [CAT_HE[c.category], c.kind, c.owner, c.event, yes(c.changed), c.end_before, c.end_after, c.next_event, c.next_start, c.overlap_before, c.overlap_after]) : [[cont.note]],
      ['רציפות: לפני ואחרי', cont.note, 'שורה = זוג אירועים ברכב או בסידור עם חפיפה לפני או אחרי', 'אותה נסיעה יכולה להופיע פעם ברכב ופעם בסידור.', 'חפיפה = שיבוץ מחדש באופטיבוס, לא קיצור.'], [16, 8, 14, 24, 7, 9, 9, 26, 10, 10, 10]),
    mk('Sign ו-Places', '444444', ['בדיקה', 'Trip Id', 'Route Id', 'Sign במפת היעד', 'Sign בקובץ המושווה'], [
      ...(ctx.signChecks || []).flatMap(s => [[`${s.label}: הושוו ${s.compared}, הבדלים ${s.diffs.length}, חסרים בקובץ המושווה ${s.missing_in_other}, רק בקובץ המושווה ${s.only_in_other}`], ...s.diffs.map(d => [s.label, d.id, d.routeId, d.target, d.other])]),
      [`Places: ${tpl.placesInfo ? tpl.placesInfo.note : 'לא ידוע'}`], ...(tpl.placesInfo && tpl.placesInfo.added_from_map.length ? [[`נוספו מהמפה: ${tpl.placesInfo.added_from_map.join(', ')}`]] : []),
      ...(tpl.mixedSign || []).map(m => ['Sign מעורב במסלול', null, m.routeId, m.signs.join('/'), null])],
      ['Sign ו-Places', 'ה-Sign בקובץ הטעינה נלקח ממפת היעד המדויקת; הבדלים מול קבצים אחרים נרשמים ולא "מתוקנים"', 'שורה = הבדל', 'Sign שונה מתבנית המסלול גרם לדחייה בהמשימה הקודמת.', 'בדוק לפני טעינה.'], [60, 22, 14, 14, 14]),
    mk('ללא שינוי', '444444', ['Trip Id', 'מק״ט', 'כיוון', 'חלופה', 'יציאה', 'משך (דק׳)', 'מצב'], result.untouched.map(t => [t.id, t.makat, t.dir, t.alt, hhmm(t.dep), t.arr - t.dep, 'ללא המלצה; נשמר מלוח המקור']),
      ['ללא שינוי', 'נסיעות שלא הייתה להן המלצה', 'שורה = נסיעה', '', ''], [22, 9, 7, 7, 8, 10, 30]),
    mk('מקורות', '444444', ['שדה', 'ערך'], [['לוח יעד', ctx.target || ''], ['מקור הלוח', JSON.stringify(tpl.source)], ['מקור המלצות', ctx.source || ''], ['תקופת ראיות', ctx.period || ''], ['מדיניות', JSON.stringify(result.policy)], ['אזהרות תבנית', tpl.warnings.join(' · ') || 'אין'], ['הופק', ctx.generated || '']],
      ['מקורות', 'מקור, תקופה ומדיניות', 'שורה = פרמטר', '', ''], [24, 110]),
  ];
  const changes = sheets.find(s => s.name === 'שינויים');
  const normalStyle = changes.styles;
  changes.styles = (r, c, v) => r >= 7 && (c === 7 || c === 9) && typeof v === 'number' ? 9 : normalStyle(r, c, v);
  return sheets;
}
function buildChangeReport(ctx, tpl, result, cont) { return workbook(changeReportSheets(ctx, tpl, result, cont)); }
/* Review workbook contains the unchanged load schema plus the entire before/after ledger.
   It is never the import file: the clean five-sheet download remains separate. */
function buildReviewWorkbook(ctx, tpl, result, cont) {
  if (result.trips.length !== tpl.trips.length) fail('מספר הנסיעות השתנה; סקירת לוח מלא נחסמה');
  const decisions = new Map(result.decisions.map(d => [d.trip.id, d]));
  const after = new Map(result.trips.map(t => [t.id, t]));
  if (after.size !== tpl.trips.length || tpl.trips.some(t => !after.has(t.id))) fail('זהויות הנסיעות אינן תואמות ללוח המקור');
  const report = changeReportSheets(ctx, tpl, result, cont);
  const intro = report[0];
  intro.rows.find(r => r[0] === 'מה הקובץ')[1] = 'חוברת סקירה מאוחדת: חמשת גיליונות הדאטהסט וכל נסיעות המפה לפני ואחרי, לצד הסברים. חוברת זו אינה לטעינה; קובץ UPDATE TRIPS נקי מוצע בנפרד.';
  intro.rows.push(['שעות אחרי חצות', 'בגיליון כל הנסיעות לפני ואחרי נשמרות שעות 24:00 ומעלה כדי להראות מעבר ליום הבא; בגיליונות הטעינה נשמר חוזה השעות וקוד Day Offset המקורי.']);
  intro.rows.push(['בסיס ההצעה', ctx.screen === 'holiday' ? 'P80 של נסיעות בחלון ±1 שעה סביב שעת היציאה בימי אותו שלב חג, בכפוף לבקרות המדגם והיציבות. נסיעות בשעה הן רק בשעת היציאה; נסיעות בחלון כוללות גם שעות שכנות.' : 'P80 תא חודש סגור של אותה שעת יציאה לפי בסיס המסך; אין חלון שעות.']);
  intro.rows.push(['היקף הלוח', 'כל נסיעות מפת הסניף וסוג היום שנבחרו, כולל נסיעות שלא שונו. לא כל סוגי הימים יחד.']);
  const rows = tpl.trips.map(t => {
    const out = after.get(t.id), d = decisions.get(t.id), r = d?.rec || {};
    const changed = d?.action === 'change';
    return [t.id, t.makat, t.dir, t.alt, hhmmPlain(t.dep), hhmmPlain(out.dep), hhmmPlain(t.arr), hhmmPlain(out.arr),
      t.arr - t.dep, Number.isFinite(d?.want) ? d.want : null, out.arr - out.dep, out.arr - t.arr,
      changed ? 'שונה בתרחיש' : 'נשמר', d?.code || 'no_recommendation',
      d?.reason || (changed ? 'עבר את מדיניות ההארכה ובקרות הראיות והיציבות' : 'ללא המלצה; נשמר מלוח המקור'),
      r.tier || null, r.n_hour ?? null, r.n_days ?? null, r.source || null, r.period || null, r.basis || null,
      t.dep >= 1440 ? Math.floor(t.dep / 1440) : 0, r.n_window ?? null];
  });
  const ledger = { name: 'כל הנסיעות לפני ואחרי', rtl: true, freeze: 2,
    rows: [['Trip Id', 'מק״ט', 'כיוון', 'חלופה', 'יציאה לפני', 'יציאה אחרי', 'הגעה לפני', 'הגעה אחרי',
      'משך קיים בדקות', 'הצעת ראיות בדקות', 'משך בתרחיש בדקות', 'שינוי שהוחל בדקות', 'מצב', 'קוד החלטה', 'למה השתנה או נשמר',
      'רמת ראיה', 'נסיעות בשעה', 'ימי שירות בראיות', 'מקור', 'תקופת ראיות', 'בסיס', 'היסט יום יציאה', 'נסיעות בחלון'], ...rows],
    widths: [24, 10, 8, 8, 12, 12, 12, 12, 16, 18, 18, 18, 22, 24, 65, 12, 15, 18, 32, 32, 40, 16, 16],
    styles: (row, col, v) => row === 0 ? 10 : rows[row - 1][12] === 'שונה בתרחיש' && [7, 10, 11].includes(col) ? 9 : (typeof v === 'number' && !Number.isInteger(v) ? 8 : 5) };
  return workbook([...report.slice(0, 1), ledger, ...loadSheets(tpl, result.trips), ...report.slice(1)]);
}

const api = { TRIPS_HEADERS, STOP_HEADERS, PLACES_HEADERS, VT_HEADERS, MAP_HEADERS, SHEETS, DEFAULT_POLICY, GLOSSARY, GLOSSARY_COMMON, clockToMin, hhmm, hhmmPlain, minToFrac, fracToMin,
  templateFromMap, templateFromUpdateTrips, chainsFromMap, signDiff, eligibility, applyRecommendations, continuity, impact, buildLoadFile, buildChangeReport, buildReviewWorkbook, workbook };
if (typeof module === 'object' && module.exports) module.exports = api; else root.OptibusUpdateTrips = api;
})(typeof window !== 'undefined' ? window : globalThis);
