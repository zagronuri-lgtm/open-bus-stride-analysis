/* Optibus export panels for screen 4 (duty checker) and screen 5 (holidays).
   Depends on page globals from template.html: D, M, BR, DAYS, CELLS, state, usable(); and on the inlined modules
   OptibusUpdateTrips and DutyCheckRecs. Assets are fetched lazily from optibus-export/ (same origin). No upload anywhere:
   the page downloads one combined review workbook; the clean UPDATE TRIPS file requires a separate click. */
(function () {
'use strict';
const X = window.OptibusUpdateTrips, DC = window.DutyCheckRecs;
if (!X || !DC) return;
const BASE = 'optibus-export/';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cache = new Map();
async function sha256(buf) { const h = await crypto.subtle.digest('SHA-256', buf); return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, '0')).join(''); }
async function asset(file, expect) {
  if (!/^[A-Za-z0-9_-]+\.json$/.test(file)) throw new Error('שם קובץ נכס לא תקין');
  if (cache.has(file)) return cache.get(file);
  const r = await fetch(BASE + file, { credentials: 'same-origin' }); if (!r.ok) throw new Error(`HTTP ${r.status} ${file}`);
  const buf = await r.arrayBuffer(); if (expect && await sha256(buf) !== expect) throw new Error(`חתימת ${file} אינה תואמת לאינדקס`);
  const v = JSON.parse(new TextDecoder().decode(buf)); cache.set(file, v); return v;
}
function download(bytes, name) {
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }));
  const a = document.createElement('a'); a.href = url; a.download = name.replace(/[\\/:*?"<>|]/g, '_'); a.click(); setTimeout(() => URL.revokeObjectURL(url), 1500);
}
const stamp = () => new Date().toISOString().slice(0, 16).replace('T', ' ');
function summaryHtml(imp, el, cont) {
  const cs = cont.available ? cont.summary : null;
  return `<div role="status"><b>${esc(el.status)}.</b> ${el.reasons.length ? esc(el.reasons.join(' · ')) + '. ' : ''}${esc(el.separation)}<br>
  הופקה חוברת סקירה אחת: דאטהסט וכל ${imp.trips_total} נסיעות המפה, לפני ואחרי, כולל הסיבה לשינוי או לשמירת הזמן. היקף: מפת הסניף וסוג היום שנבחרו, לא כל סוגי הימים יחד.
  שונו <bdi dir="ltr">${imp.changed}</bdi> נסיעות (<bdi dir="ltr">${imp.added_minutes >= 0 ? '+' : ''}${imp.added_minutes}</bdi> דק׳, הגעה בלבד)${imp.rec_conflicts ? `; <bdi dir="ltr">${imp.rec_conflicts}</bdi> נסיעות עם המלצות כפולות נחסמו` : ''}.
  ${cs ? `חפיפות בשיבוץ הישן: לפני <bdi dir="ltr">${cs.by_kind.vehicles.before}</bdi> רכב / <bdi dir="ltr">${cs.by_kind.duties.before}</bdi> סידור; חדשות אחרי השינוי: <bdi dir="ltr">${cs.unique_events.new}</bdi> אירועים ייחודיים, הוחמרו <bdi dir="ltr">${cs.unique_events.existing_worsened}</bdi>. צפוי בתרחיש להרצה מחדש.` : esc(cont.note)}
  לא בוצעו טעינה, אופטימיזציה או בדיקת תקנה 168.</div>`;
}
async function run(tplFile, tplSha, recs, policy, ctx, evidence, statusEl, guard = () => {}) {
  statusEl.textContent = 'טוען לוח יעד…';
  const map = await asset(tplFile, tplSha);
  guard();
  const tpl = X.templateFromMap(map, { label: ctx.target, placesOriginal: map.places_original, placesOriginalLabel: map.places_original_label });
  const el = X.eligibility(tpl, evidence || {});
  const res = X.applyRecommendations(tpl, recs, policy); const cont = X.continuity(tpl, res); const imp = X.impact(res, cont);
  const reportCtx = { ...ctx, eligibility: el, signChecks: map.sign_checks || [], generated: stamp() };
  download(X.buildReviewWorkbook(reportCtx, tpl, res, cont), `סקירת_דאטהסט_${ctx.name}__${el.tag}.xlsx`);
  statusEl.innerHTML = summaryHtml(imp, el, cont);
  const clean = document.createElement('button'); clean.type = 'button';
  clean.textContent = `הורד בנפרד UPDATE TRIPS נקי · ${el.status}`;
  clean.onclick = () => { try { guard(); download(X.buildLoadFile(tpl, res.trips), `UPDATE_TRIPS_${ctx.name}__${el.tag}.xlsx`); }
    catch (err) { statusEl.textContent = 'ההורדה נחסמה: ' + err.message; } };
  statusEl.appendChild(clean);
}

/* Small, read-only selection workflow. Trip IDs belong to the selected map; filters never change identity.
   Reuses the export engine: no alternate P80 rule, no file download, no map mutation. */
function mountTripSelection(box, sel, scopeKey, mapsFor) {
  const panel = document.createElement('section'); panel.id = 'tripSelection'; panel.className = 'duty-box';
  panel.innerHTML = `<h3>בדיקת נסיעות נבחרות — בלי אקסל</h3>
    <p class="note">בחר מפה למעלה, הצג את נסיעותיה וסמן מה לבדוק. התוצאה היא הצעת זמן לבדיקה מול המפה הרשומה, לא אישור שינוי לוח. שאר הנסיעות אינן נבדקות כאן.</p>
    <button type="button" id="tsLoad">הצג נסיעות לבחירה</button>
    <div id="tsControls" hidden>
      <label>קו · כיוון · חלופה <select id="tsRoute" aria-label="סינון קו כיוון וחלופה"></select></label>
      <label>יציאה משעה <input id="tsFrom" aria-label="יציאה משעה" placeholder="למשל 06:00" size="7"></label>
      <label>עד שעה (כולל) <input id="tsTo" aria-label="יציאה עד שעה" placeholder="למשל 09:00" size="7"></label>
      <button type="button" id="tsFilter">סנן נסיעות</button>
      <p class="note">השעות הן שעות היציאה המדויקות במפה, כולל 24:00 ומעלה ליום הבא. שתי שעות זהות בוחרות אותה דקת יציאה. שדה ריק אינו מגביל.</p>
      <button type="button" id="tsAll">סמן את כל המסוננות</button>
      <button type="button" id="tsClear">נקה בחירה</button>
      <button type="button" id="tsRun" disabled>בדוק את הנסיעות שנבחרו</button>
      <button type="button" id="tsExperiment" disabled>חבילת ניסוי לקלוד — לסקירה בלבד</button>
      <p class="note">להעברת הבחירה והראיות לסקירה: הקובץ כולל את כל נסיעות המקור ללא שינוי והצעות בנפרד. זהות מפעיל ותאריכי יעד עדיין דורשים אימות. ההורדה אינה שולחת לקלוד ואינה מפעילה ניסוי.</p>
      <span id="tsCount" role="status" aria-live="polite"></span><div id="tsChosen" aria-label="הנסיעות שבחרת" style="margin:12px 0;line-height:1.8;max-height:260px;overflow:auto"></div>
      <div id="tsList" style="max-height:360px;overflow:auto;margin-top:8px"></div>
      <button type="button" id="tsPrev">עמוד קודם</button> <span id="tsPage"></span> <button type="button" id="tsNext">עמוד הבא</button>
    </div><div id="tsStatus" role="status" aria-live="polite"></div><div id="tsResult" style="overflow:auto"></div><button type="button" id="tsStats" disabled>בדיקת שינוי סטטיסטי בכל שעות הקו</button><div id="tsDiagnostics" style="line-height:1.8"></div>`;
  box.insertBefore(panel, box.querySelector('#oxDutyGo'));
  const q = id => panel.querySelector('#' + id);
  let filteredIds = new Set();
  const chosen = new Set(); let loaded = null, revision = 0, request = 0, page = 0, filtered = [];
  const key = () => scopeKey() + '|' + sel.value;
  let lastKey = key(), diagnosticRecs = [], diagnosticRevision = 0, checkedReview = null, reviewDownloadUrl = null;
  q('tsStats').onclick = async () => { const rev=++diagnosticRevision; q('tsStats').disabled=true; try { await runTripShiftPanel(diagnosticRecs,q('tsDiagnostics'),()=>rev!==diagnosticRevision); } catch(e) { if(rev===diagnosticRevision) q('tsDiagnostics').textContent='הניתוח לא הושלם: '+e.message; } finally { if(rev===diagnosticRevision) q('tsStats').disabled=false; } };
  const fmt = min => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
  const routeKey = t => JSON.stringify([t.makat, t.dir, t.alt]);
  const lineKey = t => 'line:' + t.makat;
  const routeLabel = t => `קו ${t.row[X.TRIPS_HEADERS.indexOf('Sign')] || t.makat} · כיוון ${t.dir} · חלופה ${t.alt} · מק״ט ${t.makat}`;
  const num = value => Number.isFinite(value) ? String(value) : '—';
  const monthLabel = value => {
    const m = String(value || '').match(/^(\d{4})-(\d{2})$/);
    return m && Number(m[2]) >= 1 && Number(m[2]) <= 12 ? ['ינואר','פברואר','מרץ','אפריל','מאי','יוני','יולי','אוגוסט','ספטמבר','אוקטובר','נובמבר','דצמבר'][Number(m[2])-1] + ' ' + m[1] : value || 'אין חודש זמין';
  };
  function renderChosen() {
    const trips = loaded ? loaded.tpl.trips.filter(t => chosen.has(t.id)).sort((a,b) => a.dep-b.dep) : [];
    q('tsChosen').innerHTML = trips.length ? '<b>הנסיעות שבחרת — נשמרות גם במעבר בין כיוונים:</b><ul>' + (trips.length > 100 ? '<p>מוצגות 100 הנסיעות הראשונות מתוך ' + trips.length + '. כל הנבחרות נכללות בבדיקה; להסרת נסיעה נוספת אפשר לסנן אותה בטבלת הבחירה.</p>' : '') + trips.slice(0,100).map(t => `<li>${esc(routeLabel(t))} · יציאה <bdi>${fmt(t.dep)}</bdi> <button type="button" data-remove="${esc(t.id)}" aria-label="הסר נסיעה ${esc(t.id)}">הסר</button></li>`).join('') + '</ul>' : 'טרם נבחרו נסיעות. אפשר לבחור באותו קו נסיעות מכמה כיוונים וחלופות.';
  }
  q('tsChosen').addEventListener('click', e => {
    const id = e.target?.getAttribute('data-remove'); if (!id || !chosen.has(id)) return;
    const buttons = [...q('tsChosen').querySelectorAll('[data-remove]')];
    const position = buttons.indexOf(e.target);
    chosen.delete(id); selectionChanged(); renderList();
    const remaining = [...q('tsChosen').querySelectorAll('[data-remove]')];
    (remaining[Math.min(position, remaining.length - 1)] || q('tsLoad')).focus();
  });
  function clearResult() { if (reviewDownloadUrl) { URL.revokeObjectURL(reviewDownloadUrl); reviewDownloadUrl=null; } checkedReview=null; q('tsExperiment').disabled=true; diagnosticRevision++; diagnosticRecs=[]; q('tsStats').disabled=true; q('tsDiagnostics').innerHTML=''; q('tsResult').innerHTML = ''; q('tsStatus').textContent = ''; }
  function selectionChanged() { clearResult(); renderChosen(); q('tsCount').textContent = `נבחרו ${chosen.size} נסיעות מתוך ${loaded?.tpl.trips.length || 0}; ${filtered.length} מתאימות לסינון; ${[...chosen].filter(id => !filteredIds.has(id)).length} נבחרות מחוץ לסינון`; q('tsRun').disabled = !chosen.size; }
  function invalidate() {
    const next = key(); if (next === lastKey) return;
    lastKey = next; revision++; request++; loaded = null; chosen.clear(); filtered = []; filteredIds.clear(); page = 0;
    q('tsControls').hidden = true; q('tsList').innerHTML = ''; q('tsRun').disabled = true; q('tsCount').textContent = ''; renderChosen(); clearResult(); q('tsLoad').disabled = !sel.value || !!state.cluster;
    q('tsStatus').textContent = 'החתך השתנה. הצג את נסיעות המפה החדשה כדי לבחור מחדש.';
  }
  function renderList() {
    page = Math.max(0, Math.min(page, Math.max(0, Math.ceil(filtered.length / 100) - 1)));
    q('tsAll').disabled = !filtered.length;
    const rows = filtered.slice(page * 100, (page + 1) * 100);
    q('tsList').innerHTML = `<table class="dutyt"><thead><tr><th>בחר</th><th>קו · כיוון · חלופה</th><th>יציאה (יום שירות)</th><th>זמן קיים בדקות</th><th>מזהה נסיעה</th></tr></thead><tbody>${rows.map(t => `<tr><td><input type="checkbox" data-trip="${esc(t.id)}" aria-label="${esc('בחר ' + routeLabel(t) + ' בשעה ' + fmt(t.dep) + ' מזהה ' + t.id)}" ${chosen.has(t.id) ? 'checked' : ''}></td><td>${esc(routeLabel(t))}</td><td><bdi>${fmt(t.dep)}</bdi></td><td>${num(t.arr-t.dep)}</td><td>${esc(t.id)}</td></tr>`).join('')}</tbody></table>`;
    q('tsPage').textContent = filtered.length ? `עמוד ${page + 1} מתוך ${Math.ceil(filtered.length / 100)}` : 'אין נסיעות בסינון הזה';
    q('tsPrev').disabled = page === 0; q('tsNext').disabled = (page + 1) * 100 >= filtered.length;
    q('tsCount').textContent = `נבחרו ${chosen.size} נסיעות מתוך ${loaded?.tpl.trips.length || 0}; ${filtered.length} מתאימות לסינון; ${[...chosen].filter(id => !filteredIds.has(id)).length} נבחרות מחוץ לסינון`;
    q('tsRun').disabled = !chosen.size;
  }
  function minute(text) {
    if (!text.trim()) return null;
    const m = text.trim().match(/^(\d{1,2}):([0-5]\d)$/);
    if (!m) throw new Error('יש להזין שעה במבנה 06:00; אחרי חצות אפשר 24:30.');
    return Number(m[1]) * 60 + Number(m[2]);
  }
  function filter() {
    if (!loaded) return;
    try {
      const from = minute(q('tsFrom').value), to = minute(q('tsTo').value);
      if (from != null && to != null && from > to) throw new Error('שעת הסיום מוקדמת מההתחלה. לחציית חצות השתמש ב־24:00 ומעלה.');
      filtered = loaded.tpl.trips.filter(t => (!q('tsRoute').value || routeKey(t) === q('tsRoute').value || lineKey(t) === q('tsRoute').value) && (from == null || t.dep >= from) && (to == null || t.dep <= to)).sort((a, b) => a.dep - b.dep || routeLabel(a).localeCompare(routeLabel(b), 'he', {numeric:true}) || a.id.localeCompare(b.id));
      filteredIds = new Set(filtered.map(t => t.id));
      page = 0; q('tsStatus').textContent = ''; renderList();
    } catch (e) { filtered = []; filteredIds.clear(); page = 0; renderList(); q('tsStatus').textContent = e.message; }
  }
  q('tsFilter').onclick = filter; q('tsRoute').addEventListener('change', filter);
  q('tsPrev').onclick = () => { page--; renderList(); }; q('tsNext').onclick = () => { page++; renderList(); };
  q('tsList').addEventListener('change', e => {
    const id = e.target?.getAttribute('data-trip');
    if (!loaded || !loaded.tpl.trips.some(t => t.id === id)) return;
    if (e.target.checked) chosen.add(id); else chosen.delete(id); selectionChanged();
  });
  q('tsAll').onclick = () => { filtered.forEach(t => chosen.add(t.id)); selectionChanged(); renderList(); };
  q('tsClear').onclick = () => { chosen.clear(); selectionChanged(); renderList(); };
  q('tsLoad').onclick = async () => {
    invalidate(); const snapshot = { key: key(), rev: revision, req: ++request, b: state.b, day: state.day, basis: state.p80Basis, sid: sel.value };
    const guard = () => { if (snapshot.key !== key() || snapshot.rev !== revision || snapshot.req !== request || state.cluster || !mapsFor(snapshot.b, snapshot.day).some(m => m.sid === snapshot.sid)) throw new Error('החתך השתנה; יש להציג את הנסיעות מחדש.'); };
    chosen.clear(); loaded = null; filtered = []; q('tsControls').hidden = true; clearResult(); q('tsLoad').disabled = true;
    q('tsStatus').textContent = 'טוען נסיעות מהמפה הנבחרת…';
    try {
      guard(); const index = await asset('index.json'); guard();
      const ref = index.templates.find(t => t.sid === snapshot.sid); if (!ref) throw new Error('אין תבנית למפה זו.');
      const map = await asset(ref.file, ref.sha256), policy = await asset('policy.json', index.policy.sha256); guard();
      if (String(map.optibusId) !== snapshot.sid) throw new Error('זהות המפה אינה תואמת לבחירה.');
      const tpl = X.templateFromMap(map, {placesOriginal: map.places_original});
      if (tpl.trips.some(t => typeof t.id !== 'string' || !t.id.trim())) throw new Error('מזהה נסיעה חסר או ריק במפה: הבחירה נחסמה.');
      const ids = new Set(tpl.trips.map(t => t.id)); if (ids.size !== tpl.trips.length) throw new Error('מזהי נסיעה כפולים במפה: בחירה בודדת נחסמה.');
      const idx = DC.index(CELLS, { b: snapshot.b, di: snapshot.day, si: snapshot.basis === 'school' ? M.seasons.indexOf('לימודים') : snapshot.basis === 'summer' ? M.seasons.indexOf('קיץ') : null, usableMonth: mi => usable(mi), spreadEvidence: typeof D !== 'undefined' ? D.recommendation_spread : {} });
      loaded = {tpl, policy, idx, snapshot, reviewSource:{asset:ref.file, sid:ref.sid, sha256:ref.sha256 ?? null, policy_sha256:index.policy.sha256 ?? null}};
      const routes = [...new Map(tpl.trips.map(t => [routeKey(t), t])).values()].sort((a,b) => routeLabel(a).localeCompare(routeLabel(b), 'he', {numeric:true}));
      const lines = [...new Map(routes.map(t => [t.makat, t])).values()];
      q('tsRoute').innerHTML = '<option value="">כל הקווים, הכיוונים והחלופות</option>' + lines.map(t => `<option value="${esc(lineKey(t))}">קו ${esc(t.row[X.TRIPS_HEADERS.indexOf('Sign')] || t.makat)} · כל הכיוונים והחלופות · מק״ט ${esc(t.makat)}</option>`).join('') + routes.map(t => `<option value="${esc(routeKey(t))}">${esc(routeLabel(t))}</option>`).join('');
      q('tsFrom').value = ''; q('tsTo').value = ''; q('tsControls').hidden = false; filter();
    } catch (e) { if (snapshot.req === request) q('tsStatus').textContent = 'הבדיקה לא נטענה: ' + e.message; }
    finally { if (snapshot.req === request) q('tsLoad').disabled = !sel.value || !!state.cluster; }
  };
  q('tsRun').onclick = () => {
    invalidate(); if (!loaded || !chosen.size) return;
    try {
      const {tpl, idx, policy, snapshot} = loaded;
      if (snapshot.key !== key() || snapshot.rev !== revision) throw new Error('החתך השתנה. יש לבחור מחדש.');
      const trips = tpl.trips.filter(t => chosen.has(t.id)).sort((a,b) => a.dep-b.dep || a.id.localeCompare(b.id));
      const recs = DC.recsForTrips(trips, idx, { source: `מד־זמנים, בילד ${M.built}`, basis: snapshot.basis === 'school' ? 'לימודים בלבד — חודשים נפרדים' : snapshot.basis === 'summer' ? 'קיץ בלבד — חודשים נפרדים' : 'עיון כללי — חודש סגור אחרון', monthName: mi => MONTHS[mi], seasonName: si => M.seasons[si] });
      diagnosticRecs=recs; q('tsStats').disabled=false;
      const result = X.applyRecommendations(tpl, recs, policy);
      if (result.decisions.length !== trips.length || result.decisions.some(d => !chosen.has(d.trip.id))) throw new Error('תוצאת הבדיקה אינה תואמת לנסיעות שנבחרו.');
      checkedReview = {recs, decisions:result.decisions, selectedIds:[...chosen]};
      q('tsExperiment').disabled = !(typeof window !== 'undefined' && window.ExperimentReviewPackage);
      const changed = result.decisions.filter(d => d.action === 'change').length;
      q('tsStatus').textContent = `נבדקו ${trips.length} נסיעות · ${changed} הצעות שינוי שעברו את הכללים · ${BR[snapshot.b]} · ${DAYS[snapshot.day]} · מפה ${snapshot.sid}. לא הופק קובץ ולא שונתה מפה.`;
      q('tsResult').innerHTML = `<p class="note">ההצעה מבוססת על P80, מדגם, יציבות חודשית ופיזור. IQR גבוה או פער גבוה בין P80 ל־P90 עוצרים את החלת ההצעה ומעבירים אותה לבחינה; אין תוספת דקות אוטומטית לפי הפיזור. <a href="#g-spread" onclick="event.preventDefault(); exOpenTerm('g-spread')">מהו IQR?</a></p>
        ${result.decisions.map(d => {
          const calendar = M.school_calendar_review;
          const history = (d.rec.monthlyEvidence || []).slice();
          const targetMonths = MONTHS.filter((m,i)=>usable(i) && (snapshot.basis==='school' ? !['07','08'].includes(m.slice(5,7)) : snapshot.basis==='summer' ? ['07','08'].includes(m.slice(5,7)) : true));
          for(const month of targetMonths) if(!history.some(x=>x.month===month)) history.push({month,season:snapshot.basis==='school'?'לימודים לפי לוח המקור':snapshot.basis==='summer'?'קיץ':'—',n:null,days:null,p80:null,missing:true});
          history.sort((a,b)=>b.month.localeCompare(a.month));
          const months = new Set(history.map(m=>m.month));
          const exclusions = (calendar?.national_exclusions || []).filter(e=>months.has(e.date.slice(0,7)));
          const calendarText = exclusions.length ? exclusions.map(e=>`${e.date}: ${e.name} (${e.treatment==='drop'?'החרגת יום':'סיווג נפרד'}; ${e.hours || 'חלון לא צוין'})`).join(' · ') : calendar?.source_available ? 'אין החרגות ארציות רשומות בחודשים המוצגים; אין בכך אישור שהיו ימי לימודים רגילים' : 'מקור רשימת ההחרגות אינו זמין';
          const target = d.action === 'change' ? d.want : d.prev, r = d.rec;
          const titles = {extend:'הארכה הנתמכת במדגם ובבדיקת היציבות',negligible:d.reason,cut_review:'הנתונים מציעים קיצור; נדרשת בדיקה נפרדת',review:r.inferencePending ? 'המדדים התיאוריים עברו; נדרשת בדיקה סטטיסטית לפני החלת זמן חדש' : r.spread_review && !r.spread_review.passed ? 'דורש בחינה: פיזור גבוה או מידע חסר; הזמן הקיים נשמר' : 'אין די תמיכה לשינוי לפי כללי המדגם והיציבות',no_evidence:'אין ראיות מספיקות לשינוי',blocked:'השינוי חסום בגלל סתירת מקורות או מדיניות',unstable_decision:'ההצעה אינה יציבה בין חודשי ההשוואה',blocked_intermediate:'חסרים זמני תחנות ביניים מאומתים'};
          const validCandidate = Number.isFinite(r.minutes) && r.minutes > 0 && Number.isFinite(r.p80) && r.p80 > 0;
          const canReview = validCandidate && !['blocked','no_evidence','blocked_intermediate'].includes(d.code);
          const examineExtension = canReview && r.minutes > d.prev;
          const recommendation = d.action === 'change' ? 'מומלץ לבחון הארכה בניסוי' : examineExtension ? 'נדרשת בחינת הארכה' : canReview && r.minutes < d.prev ? 'אין המלצה לקיצור בשלב זה — נדרשת בדיקה' : 'אין עדיין הכרעה לגבי הזמן המתאים';
          const nextStep = examineExtension ? `יעד ראשוני לניסוי: ${num(r.minutes)} דקות, לפי P80. עד להשלמת הבדיקה אין שינוי בלוח.` : 'עד להשלמת הבדיקה אין שינוי בלוח. אין בכך אישור שהזמן הקיים מתאים.';
          return `<article class="tsDecision" style="margin:16px 0;padding:20px;border:1px solid var(--line);border-radius:12px;font-family:inherit;line-height:1.8;text-align:right">
            <h4 style="margin:0 0 12px;font-size:17px">${esc(routeLabel(d.trip))} · יציאה <bdi>${fmt(d.trip.dep)}</bdi></h4>
            <div style="display:flex;flex-wrap:wrap;gap:16px 36px;font-size:17px"><span>זמן קיים: <b>${num(d.prev)}</b> דקות</span><span>${d.action === 'change' ? 'זמן מוצע לניסוי' : 'זמן שנשאר בלוח'}: <b>${num(target)}</b> דקות</span><span>שינוי: <bdi>${target-d.prev > 0 ? '+' : ''}${num(target-d.prev)}</bdi> דקות</span></div>
            <p style="font-size:19px"><b>המלצה: ${esc(recommendation)}</b><br>${esc(nextStep)}</p><p><b>מה נדרש לפני החלטה:</b> ${esc(titles[d.code] || d.reason)}. יש לבדוק את הפער לפי ימים ושעות ואת השפעתו על הסידור לפני שינוי קבוע.</p>
            <p><b>תקופת יעד:</b> ${esc(r.basis || '—')}. ${snapshot.basis==='last'?'עיון כללי עשוי לבחור קיץ; לבדיקת מפת לימודים יש לבחור יעד לימודים.':'אין ערבוב בין לימודים לקיץ בבחירת יעד.'}</p>
            <details open><summary>${snapshot.basis === 'school' ? 'חודשי לימודים בנפרד — כולל מאי ויוני כשיש נתונים' : snapshot.basis === 'summer' ? 'חודשי קיץ בנפרד' : 'חודשי בסיס בנפרד — עיון כללי'}</summary><table class="dutyt"><thead><tr><th>חודש</th><th>סיווג מקור</th><th>תצפיות</th><th>ימים</th><th>P80 בדקות</th><th>תאריכי מדגם</th></tr></thead><tbody>${history.map(m=>`<tr><td>${esc(monthLabel(m.month))}${m.missing?' · אין תא תואם':''}</td><td>${esc(m.season)}</td><td>${num(m.n)}</td><td>${num(m.days)}</td><td>${num(m.p80)}</td><td>${m.from && m.to ? esc(m.from)+' עד '+esc(m.to) : '—'}</td></tr>`).join('')}</tbody></table><p>כל חודש עומד בפני עצמו. בסיס ההחלטה הוא החודש השמיש האחרון והשוואתו לקודמו באותה עונה; חודשים דלילים או חסרים מסומנים ללא אחוזון שמיש; מקף אינו אפס. אין תא תואם אינו אומר שאין נסיעות בקו. תווית לימודים אינה אימות שכל יום היה יום לימודים.</p><details><summary>אילו ימים הוחרגו ומה עדיין דורש אימות?</summary><p>${esc(calendarText)}</p><p>אלה ההחרגות הארציות הרשומות בלוח המקור; אינן רשימה מלאה של חופשות או שיבושים. היעדר יום במדגם אינו מוכיח שהוחרג. בפרט, סוף שנת הלימודים לפי שכבת גיל וימים חריגים במאי–יוני טרם אומתו. ימים ארוכים אינם מוסרים רק בשל משכם.</p></details></details>
            <p><b>מתי נמדדו הנסיעות?</b> ${esc(monthLabel(r.period))} · ${num(r.n_hour)} תצפיות ב־${num(r.n_days)} ימי שירות בתוך החודש.<br>
            ${r.stability?.prev_month ? 'בדיקת יציבות מול ' + esc(monthLabel(r.stability.prev_month)) + '.' : 'אין חודש השוואה זמין לבדיקת יציבות.'}
            ${history.find(m=>m.month===r.period)?.from ? 'תאריכי הדגימות: ' + esc(history.find(m=>m.month===r.period).from) + ' עד ' + esc(history.find(m=>m.month===r.period).to) + '; רק ימי השירות שבמדגם נכללו.' : 'זו תקופת חודש המקור; תאריכי הדגימות המדויקים אינם זמינים, ואין פירושו שכל ימי החודש נכללו.'}</p>
            <p><b>זמן לפי P80 בלבד, לפני בדיקה סטטיסטית:</b> ${num(r.minutes)} דקות. אין בכך המלצה מאושרת.</p>
            <p><b>P80 בראיות:</b> ${num(r.p80)} דקות. המדגם הוא לאותו קו, כיוון וחלופה בשעת היציאה, ולא לדקת היציאה המדויקת בלבד.</p>
            <p><b>פיזור ובחינת ההצעה:</b> ${esc(r.spread_review?.reason || 'אין מדדי פיזור מאומתים לתא')}</p>
            <details><summary>פירוט הנימוק והמקור</summary><div style="padding:12px 0;max-width:90ch;white-space:normal;overflow-wrap:anywhere"><p>${esc(d.reason)}</p>${d.reason === r.reason ? '' : '<p>' + esc(r.reason || '') + '</p>'}<p>בסיס: ${esc(r.basis || '—')}<br>מקור: ${esc(r.source || '—')}</p><small>מזהה נסיעה: ${esc(d.trip.id)}</small></div></details>
          </article>`;
        }).join('')}<p class="note">אין אישור לשינוי לוח: רציפות הסידור, רכבים ונהגים דורשים בדיקה נפרדת; תוקף המפה ותאריך ההפעלה טעונים אימות.</p>`;
    } catch (e) { clearResult(); q('tsStatus').textContent = e.message; }
  };
  q('tsExperiment').onclick = () => {
    invalidate(); if (!loaded || !checkedReview) return;
    try {
      const {snapshot} = loaded;
      if (snapshot.key !== key() || snapshot.rev !== revision || state.cluster || !mapsFor(snapshot.b,snapshot.day).some(m=>m.sid===snapshot.sid)) throw new Error('החתך השתנה; יש לבדוק מחדש.');
      const pkg = window.ExperimentReviewPackage.build({tpl:loaded.tpl, headers:X.TRIPS_HEADERS, source:loaded.reviewSource, context:{branch:BR[snapshot.b],day_type:DAYS[snapshot.day],basis:snapshot.basis}, selectedIds:checkedReview.selectedIds, recommendations:checkedReview.recs, decisions:checkedReview.decisions, createdAt:new Date().toISOString()});
      if (reviewDownloadUrl) URL.revokeObjectURL(reviewDownloadUrl);
      const url=URL.createObjectURL(new Blob([JSON.stringify(pkg,null,2)], {type:'application/json;charset=utf-8'}));
      reviewDownloadUrl=url;
      const a=document.createElement('a'); a.href=url; a.download=('סקירת_ניסוי_'+snapshot.sid+'.json').replace(/[\\/:*?"<>|]/g,'_'); a.textContent='הורד את חבילת הסקירה';
      q('tsStatus').textContent='החבילה מוכנה להורדה — לסקירה בלבד. זמני המקור נשמרו; לא נשלח לקלוד ולא הופעל ניסוי. ';
      q('tsStatus').appendChild(a);
    } catch(e) { clearResult(); q('tsStatus').textContent='חבילת הסקירה לא הופקה: '+e.message; }
  };
  sel.addEventListener('change', invalidate);
  document.addEventListener('dashboard-scope-change', invalidate);
  document.addEventListener('change', e => { if (/^(branchSel|clusterSel)$/.test(e.target?.id || '')) invalidate(); });
  document.getElementById('basisChips')?.addEventListener('click', invalidate);
  document.getElementById('dayTabs')?.addEventListener('click', () => setTimeout(invalidate, 0));
  return {invalidate};
}

/* ---------- screen 4 ---------- */
function mountDuty() {
  const sec = document.getElementById('sec4'); if (!sec || document.getElementById('oxDuty')) return;
  const box = document.createElement('div'); box.id = 'oxDuty'; box.className = 'duty-box'; box.style.marginTop = '14px';
  box.innerHTML = `<h3 style="margin:0 0 6px">בדיקת נסיעות ויצוא לאופטיבוס</h3>
   <p class="note" style="line-height:1.7"><b>קובץ עם זמני נסיעה מוצעים לבדיקה באופטיבוס.</b> החוברת כוללת את כל נסיעות מפת הסניף וסוג היום הנבחרים, כולל לפני ואחרי והסיבה לכל החלטה. היא אינה מאחדת סוגי ימים שונים. שעות היציאה לא משתנות. מאריכים זמן נסיעה רק כשיש מספיק נתונים וההמלצה עקבית. אם אין בסיס מספיק לשינוי — הזמן הקיים נשמר. קיצורים מוצגים בדוח לבדיקה בלבד.</p>
   <p class="note" style="line-height:1.7">אחרי השינוי צריך לשבץ מחדש רכבים ונהגים באופטיבוס ולבדוק חפיפות והפסקות. <b>הקובץ אינו סידור מאושר להפעלה.</b> אם הוא מסומן ״דוגמה — לא לטעינה״, אין לטעון אותו עד לאימות מפת היעד, התאריך וסוג היום.</p>
   <details class="howto"><summary>איך נקבע הזמן המוצע?</summary><div class="body">משווים נסיעות של אותו קו, כיוון, חלופה ושעת יציאה, בסוג היום שנבחר. משתמשים בחודש סגור לפי הבסיס שנבחר למעלה. נדרשות לפחות 30 נסיעות בחמישה ימי שירות. הזמן המוצע הוא P80: האחוזון ה-80 של משכי הנסיעה המדווחים (אינטרפולציה לינארית), מעוגל למעלה לדקה; שיעור הנסיעות שאינן עולות עליו מחושב בנפרד בכרטיס. בודקים גם שהנתון דומה לחודש קודם באותה עונה: פער עד הגבוה מבין 5 דקות או 10%. מאריכים רק ב־3 דקות לפחות, ורק אם גם נתוני החודש הקודם תומכים בכך. זמן קצר יותר אינו מוחל אוטומטית. פיזור משפיע על ההחלטה: IQR חלקי החציון מעל 25%, או פער P90 פחות P80 מעל הגבוה מבין 5 דקות ו־10% מ־P80, מעבירים את ההצעה לבחינה ושומרים את הזמן הקיים. אלה ספי בירור ניסיוניים, לא תקן סטטיסטי או ספים מכוילים. גם מדד חסר עוצר החלה. פיזור נמוך אינו אישור יציבות, ולא מוחקים חריגים או מוסיפים דקות לפי IQR. אלה כללי בחירה לתכנון, לא הבטחה לנסיעה הבאה. נכון לגרסה זו ההסקה הסטטיסטית עדיין חקרנית: הזמן הקיים נשמר ביצוא, והזמן לפי P80 מוצג כמועמד לבדיקה בלבד.</div></details>
   <label>מפה <select id="oxDutyMap"></select></label> <button type="button" id="oxDutyGo">הורד סקירת דאטהסט מלאה</button><div id="oxDutyStatus" class="note" style="margin-top:8px"></div>`;
  sec.appendChild(box);
  const sel = box.querySelector('#oxDutyMap'), st = box.querySelector('#oxDutyStatus');
  let index = null, busy = false, scopeRevision = 0;
  let selectionPanel = null;
  const button = box.querySelector('#oxDutyGo');
  const scopeKey = () => JSON.stringify([state.b, state.day, state.p80Basis, state.cluster]);
  let lastScope = scopeKey();
  const mapsFor = (b, day) => (((M.maps_declared || {})[BR[b]] || {})[DAYS[day]] || { maps: [] }).maps;
  const refresh = () => {
    const key = scopeKey();
    if (key !== lastScope) { scopeRevision++; lastScope = key; st.textContent = ''; }
    const selected = sel.value;
    const decl = ((M.maps_declared || {})[BR[state.b]] || {})[DAYS[state.day]] || { maps: [] };
    sel.innerHTML = decl.maps.map(m => `<option value="${esc(m.sid)}">${esc(m.sid)} · ${esc(m.file || '')}</option>`).join('') || '<option value="">אין מפה רשומה לסניף וליום</option>';
    if ([...sel.options].some(o => o.value === selected)) sel.value = selected;
    if (state.cluster) sel.innerHTML = '<option value="">בחר סניף מסוים לייצוא מפת סניף</option>';
    button.disabled = busy || !sel.value;
    selectionPanel?.invalidate();
  };
  refresh(); ['focus', 'mousedown'].forEach(ev => sel.addEventListener(ev, refresh)); document.getElementById('dayTabs')?.addEventListener('click', () => setTimeout(refresh, 0));
  document.addEventListener('change', e => { if (e.target && /^(branchSel|clusterSel|lineSel)$/.test(e.target.id)) refresh(); });
  document.addEventListener('dashboard-scope-change', refresh);
  document.getElementById('basisChips')?.addEventListener('click', refresh);
  sel.addEventListener('change', () => { scopeRevision++; st.textContent = ''; });
  selectionPanel = mountTripSelection(box, sel, scopeKey, mapsFor);
  button.onclick = async () => {
    if (busy) return;
    const scope = { b: state.b, day: state.day, basis: state.p80Basis, key: scopeKey(), revision: scopeRevision, sid: sel.value };
    const guard = () => {
      if (state.cluster || scope.key !== scopeKey() || scope.revision !== scopeRevision || scope.sid !== sel.value || !mapsFor(scope.b, scope.day).some(m => m.sid === scope.sid))
        throw new Error('בחירת הסניף, היום או המפה השתנתה או אינה תואמת. בחר מפה והפק מחדש.');
    };
    try {
      guard(); busy = true; button.disabled = true; st.textContent = 'מכין ייצוא למפה הנבחרת…';
      index = index || await asset('index.json'); guard(); const sid = scope.sid; const t = index.templates.find(x => x.sid === sid);
      if (!t) { st.textContent = 'אין תבנית לוח למפה זו בחבילת היצוא.'; return; }
      const policy = await asset('policy.json', index.policy.sha256);
      const idx = DC.index(CELLS, { b: scope.b, di: scope.day, si: scope.basis === 'school' ? M.seasons.indexOf('לימודים') : scope.basis === 'summer' ? M.seasons.indexOf('קיץ') : null, usableMonth: mi => usable(mi), spreadEvidence: typeof D !== 'undefined' ? D.recommendation_spread : {} });
      const map = await asset(t.file, t.sha256); guard();
      if (String(map.optibusId) !== sid) throw new Error('זהות המפה אינה תואמת לבחירה');
      const tpl0 = X.templateFromMap(map, { placesOriginal: map.places_original });
      const recs = DC.recsForTrips(tpl0.trips, idx, { source: `מד־זמנים, בילד ${M.built}`, basis: scope.basis === 'school' ? 'לימודים בלבד — חודשים נפרדים' : scope.basis === 'summer' ? 'קיץ בלבד — חודשים נפרדים' : 'עיון כללי — חודש סגור אחרון', monthName: mi => MONTHS[mi], seasonName: s => M.seasons[s] });
      await run(t.file, t.sha256, recs, policy, { screen: 'duty', name: `${BR[scope.b]}_${DAYS[scope.day]}_${sid}`, target: `${BR[scope.b]} · ${DAYS[scope.day]} · ${sid}`, target_date: 'לא נקבע: לוח שגרה',
        source: `מד־זמנים ${M.built}`, period: 'חודש סגור אחרון לכל תא' }, { dates: [], day_code: { verified: false } }, st, guard);
    } catch (err) { st.textContent = 'היצוא נכשל: ' + err.message; console.error(err); } finally { busy = false; refresh(); }
  };
}

/* ---------- screen 5 ---------- */
function mountHoliday() {
  if (typeof HOLIDAYS_SUSPENDED !== 'undefined' && HOLIDAYS_SUSPENDED) return;   // מסך החגים מושהה: לא מרכיבים פאנל ולא מושכים index.json עבורו
  const sec = document.getElementById('sec5'); if (!sec || document.getElementById('oxHoliday')) return;
  const box = document.createElement('div'); box.id = 'oxHoliday'; box.className = 'duty-box'; box.style.marginTop = '14px';
  box.innerHTML = `<h3 style="margin:0 0 6px">תכנון לחג הבא · יצוא לאופטיבוס</h3>
   <p class="note" style="line-height:1.7">נתוני תרחיש להרצה מחדש באופטיבוס, לא סידור מאושר להפעלה. מודל תכנון נפרד מטבלאות ההשוואה (שאינן משתנות): P80 חלון של נסיעות גולמיות בשעות ±1 סביב שעת היציאה, בימי אותו חג. רמה A מוחלת רק כשה-P80 יציב (שעה לבדה, בלי שעה שכנה, בלי כל יום); לא יציב (R) וימים דומים ממשפחה אחרת (B) לבדיקה בלבד. הלוח הקיים נשמר כשאין בסיס. BI מקור משלים; Open Bus הוא מקור הייחוס.</p>
   <p class="note">זמינה כעת תכנית חוה״מ סוכות 2026 לאלעד. סף יציבות התרחיש: הגבוה מבין 5 דקות ו־10%; מתוך 83 תאים בעלי מדגם מתאים, 78 עומדים בו. בסף מחמיר של 3 דקות ו־5% נותרים 44. זו רגישות לבחירת מודל, לא רמת ביטחון סטטיסטית. המלצה מוחלת רק אם גם כל בדיקות הרגישות תומכות בהארכה של 3 דקות לפחות.</p>
   <label>תכנית <select id="oxHolidayPlan"></select></label> <button type="button" id="oxHolidayGo">הורד סקירת דאטהסט מלאה</button><div id="oxHolidayStatus" class="note" style="margin-top:8px"></div>`;
  sec.appendChild(box);
  const sel = box.querySelector('#oxHolidayPlan'), st = box.querySelector('#oxHolidayStatus');
  asset('index.json').then(index => { sel.innerHTML = index.plans.map(p => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join('') || '<option value="">אין תכנית זמינה</option>'; })
    .catch(() => { sel.innerHTML = '<option value="">חבילת היצוא לא נטענה</option>'; });
  let revision = 0, busy = false;
  const button = box.querySelector('#oxHolidayGo');
  sel.addEventListener('change', () => { revision++; st.textContent = ''; });
  button.onclick = async () => {
    if (busy) return;
    const scope = { id: sel.value, revision };
    const guard = () => {
      if (scope.id !== sel.value || scope.revision !== revision || document.getElementById('oxHoliday') !== box || (typeof HOLIDAYS_SUSPENDED !== 'undefined' && HOLIDAYS_SUSPENDED))
        throw new Error('בחירת תכנית החג השתנתה. בחר תכנית והפק מחדש.');
    };
    try {
      guard(); busy = true; button.disabled = true;
      const index = await asset('index.json'); guard(); const p = index.plans.find(x => x.id === scope.id); if (!p) return;
      const plan = await asset(p.file, p.sha256); const policy = await asset('policy.json', index.policy.sha256); guard();
      if (String(plan.id) !== scope.id) throw new Error('זהות תכנית החג אינה תואמת לבחירה');
      const t = index.templates.find(x => x.sid === plan.target.sid); if (!t) { st.textContent = 'לוח היעד של התכנית חסר בחבילה.'; return; }
      await run(t.file, t.sha256, plan.recs, policy, { screen: 'holiday', name: plan.id, target: plan.target.label, target_date: plan.target.target_date,
        source: plan.meta.source, period: `${plan.meta.period_primary}; ימים דומים: ${plan.meta.period_secondary}` }, plan.target.evidence, st, guard);
    } catch (err) { st.textContent = 'היצוא נכשל: ' + err.message; console.error(err); } finally { busy = false; button.disabled = false; }
  };
}
const go = () => { mountDuty(); mountHoliday(); };
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
/* sec5 is rebuilt by the holiday script; re-mount if its content is replaced */
new MutationObserver(() => { if (document.getElementById('sec5') && !document.getElementById('oxHoliday')) mountHoliday(); }).observe(document.body, { childList: true, subtree: true });
})();
