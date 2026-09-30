'use strict';
const byId = id => document.getElementById(id);
let raw = null;
byId('file').addEventListener('change', async () => {
  raw = null; byId('submit').disabled = true;
  try {
    const file = byId('file').files[0];
    if (!file || file.size > 2 * 1024 * 1024) throw new Error('נדרש קובץ עד 2 MiB');
    const text = await file.text();
    const data = JSON.parse(text);
    if (!data.source || !Array.isArray(data.trips) || !Array.isArray(data.service_dates)) throw new Error('מבנה בסיסי חסר');
    const changed = data.trips.filter(t => t.after_arrival !== t.before_arrival).length;
    byId('preview').textContent = `מקור: ${data.source.dataset_id}\nסידור: ${data.source.schedule_id}\nגרסה: ${data.source.revision}\nסניף: ${data.branch}\nתאריכים: ${data.service_dates.join(', ')}\nנסיעות: ${data.trips.length}\nשינויים מוצעים: ${changed}\nתצוגה מקדימה בלבד; השרת יבצע אימות מלא.`;
    raw = text; byId('submit').disabled = false;
  } catch (error) { byId('preview').textContent = `טעינה נכשלה: ${error.message}`; }
});
async function request(method, path, body) {
  try {
    const token = byId('token').value.trim();
    if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) throw new Error('נדרש טוקן מקומי תקין');
    const response = await fetch(path, {method, headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'}, body, cache:'no-store', credentials:'omit', redirect:'error'});
    const result = await response.json();
    if (!response.ok) throw new Error(`השרת דחה את הבקשה (${response.status}): ${result.error || 'שגיאה'}`);
    if (result.job_id) byId('job').value = result.job_id;
    const stateLabel = {blocked_adapter:'החבילה הוכנה; ההפעלה באופטיבוס טרם חוברה',queued:'ממתין להכנה',claimed:'בהכנה',prepared:'החבילה הוכנה',failed:'ההכנה נעצרה',failed_integrity:'החבילה הועברה להסגר עקב פגיעה בשלמות הנתונים'}[result.state] || result.state;
    const failureLabel = {stale_registry:'המקור או הראיות השתנו; נדרשת בדיקה מחדש',preparation_failed:'יצירת החבילה נכשלה',stored_integrity_failed:'הנתונים השמורים אינם תקינים; נשמרו לחקירה ולא יישלחו להכנה חוזרת'}[result.failure_code] || '';
    const countsLabel = result.counts === null ? 'ספירות אינן זמינות: החבילה בהסגר.' : `נסיעות: ${result.counts.total}\nנבחרו: ${result.counts.selected}\nשינויים: ${result.counts.changed}`;
    byId('result').textContent = `מזהה: ${result.job_id}\nמצב: ${stateLabel}
${failureLabel}\n${countsLabel}\nהכנה מקומית בלבד; לא בוצעה פעולה באופטיבוס.`;
  } catch (error) { byId('result').textContent = error.message; }
}
byId('submit').addEventListener('click', () => {if (raw !== null) request('POST','/jobs',raw);});
byId('status').addEventListener('click', () => {
 const id = byId('job').value.trim();
 if (!/^[0-9a-f]{32}$/.test(id)) {byId('result').textContent='מזהה משימה לא תקין';return;}
 request('GET', `/jobs/${id}`);
});
byId('clear').addEventListener('click', () => {byId('token').value='';});
