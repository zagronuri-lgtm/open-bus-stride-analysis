# הצעת סכמת "חבילת משימה" למד-זמנים → עותק מפת ניסוי (הצעה, לא קוד), דנטה, 2026-09-30 10:34

מעמד: הצעה לדיון בלבד, בתיאום עם חוזה קודקס (`docs/travel-time-experiment-bridge-20260930.md`) והערכת ג'יורג'יו (`docs/claude-experiment-bridge-feasibility-20260930.md` §11 ונספח ב). אין כאן מימוש, אין שינוי במנוע הייצוא, אין נגיעה בעותקי ניסוי/בקרה. כל שדה מסומן במקור שלו היום (קיים / נגזר / חסר) ובהכרעת אורי שהוא תלוי בה:
- **(ב)** ראיה לתאריכי היעד ולקוד היום (Days) של מפת היעד: מי מספק ומה נחשב מאומת.
- **(ג)** פורמט קובץ הטעינה: התבנית המוכחת של המנוע (19 עמודות, 5 גיליונות, משימה 027) או הפורמט שהתקבל הלילה (עריכה-במקום של ייצוא Optibus, 22 עמודות, 9 גיליונות).
- **(ד)** מקור התבנית: ייצוא טרי של העותק (מזהי העותק) או נכס המפה הקפוא מהבנייה (מזהי המקור).

## 1. עקרונות
1. חבילה = מועמדים לניסוי, לא שינויים מאושרים. כל עוד ההסקה הסטטיסטית ממתינה (`duty_check_recs.js`: רמה A מורדת ל-R), אין נסיעה במעמד "עבר שער". השדה `gate_status` מפריד זאת במפורש.
2. חסר אינו אפס: כל שדה ראייתי שאין לו מקור = `null` + `missing_reason`.
3. הזמנים בחבילה בדקות יום שירות שלמות, כולל 24+ (לא HH:MM mod 24). ההמרה לפורמט Optibus (שבר-יום + Day Offset) היא שכבת ייצוא, לא חלק מהזהות.
4. SHA מוכיח עקביות, לא אישור. `built` ≠ מועד חילוץ ≠ מועד רענון (כמו ב-`meta.freshness_manifest`).
5. אין המצאת זמני ביניים; אין קיצור אוטומטי; קו 277 כיוון 1 חסום (policy.json).

## 2. מבנה החבילה (JSON, קידוד UTF-8)

```
{
  "contract_version": "experiment-package/0.1",            // חסר היום; מוצע. תלוי בקודקס (חוזה)
  "run_id": "ttx-20260930-<sניף>-<יום>-<sid>-<6 hex>",     // חסר היום; נוצר בצד השירות של קודקס, לא בדפדפן
  "created_at": "YYYY-MM-DD HH:MM",                        // שעון השירות (date), לא built
  "producer": {
    "tool": "מד-זמנים", "built": "<meta.built>",          // קיים (M.built); עדכון תצוגה בלבד
    "engine": {"optibus_update_trips.js": "<sha256>", "duty_check_recs.js": "<sha256>", "decision_card.js": "<sha256>"},   // נגזר: להוסיף ל-build_code_sha256
    "policy": {"file": "policy.json", "sha256": "<sha>", "spread_policy_version": "2026-09-29.1", "autoTiers": ["A"], "allowCuts": false, "minChangeMin": 3}   // קיים (index.json.policy, SPREAD_POLICY)
  },
  "source": {
    "trips_parquet_sha256": "<9966339d…|da5524ba…>",      // קיים ב-meta.cell_statistics.provenance; חסר בהמלצה עצמה → לצרף
    "percentiles_sha256": "<1dbf5bfb…>",                     // קיים
    "freshness": {"checked_at": "...", "refresh_at": null, "source_ids_in_build": [...]},   // קיים (meta.freshness_manifest); null = לא נבדק
    "period": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "basis": "חודש סגור אחרון לכל תא | לימודים | קיץ"},   // נגזר: היום "חודש סגור אחרון לכל תא" בלי תאריכים מפורשים
    "day_type_rule": "night-fixed: 00:00–03:59 → יום השירות הקודם; שבת אחרי צאת שבת+40 → מוצ\"ש (sat_sunset_plus_40)"   // קיים בצינור; ראו §5
  },
  "target": {
    "branch": "אונו", "day_tab": "חול|שישי|שבת|מוצ\"ש",   // קיים (state)
    "map_source_sid": "<sid המקור>",                          // קיים (index.json.templates.sid)
    "copy_id": "<sid העותק>",                                  // חסר; תלוי ב-(ד) ובמתאם קודקס
    "template": {"origin": "copy_export|build_asset", "sha256": "<sha>", "format": "engine-19|optibus-export-22"},   // תלוי ב-(ג)+(ד)
    "days_code": {"value": "<daysOfWeek[0]>", "verified": false, "evidence": null},   // תלוי ב-(ב); היום verified=false תמיד
    "target_dates": {"dates": [], "verified": false, "source": null},               // תלוי ב-(ב)
    "eligibility": {"status": "דוגמה — לא לטעינה | תרחיש להרצה מחדש", "reasons": [...]}   // קיים (eligibility()); ייהפך ל"תרחיש" רק עם (ב)
  },
  "trips": [ { ...ראו §3... } ],
  "summary": {"trips_total": n, "candidates": k, "changed_if_applied": c, "unmatched": u, "conflicts": x, "blocked": b},   // קיים (impact())
  "hashes": {"package_sha256": "<sha של trips+source+target בסדר קנוני>", "load_file_sha256": null}   // חסר; load_file רק אם נוצר
}
```

## 3. רשומת נסיעה

| שדה | תוכן | מקור היום | תלות |
|---|---|---|---|
| `optibus_trip_id` | Id/User Id בעותק (בתבניות היום: "מקט_כיוון_חלופה_HH:MM") | קיים (tpl.trips.id) | (ד): מזהי העותק רק מייצוא העותק |
| `route_id` | "מקט-כיוון-חלופה" | קיים | — |
| `makat`, `direction`, `alternative` | מהRoute Id; direction = directionName (לא direction הגולמי 3); חלופה קנונית (מחרוזת של שלם) | קיים; אזהרה בלבד על אי-התאמה | — (לאמת: אי-התאמה = חסימה, לא אזהרה) |
| `departure_min`, `arrival_min_before` | דקות יום שירות, 24+ מותר; Day Offset נגזר ביציאה | קיים פנימית (t.dep/t.arr); מיוצא היום כ-HH:MM | (ג): אופן הכתיבה לקובץ |
| `duration_before` | arrival − departure (דקות) של העותק | קיים (prev) | (ד): חייב להילקח מהעותק |
| `day_type_for_evidence` | סוג היום של הנסיעה לצורך התאמת הראיה (ראו §5) | נגזר: היום = לשונית היום | חסר כלל לשבת/מוצ"ש → §5 |
| `hour_cell` | floor(departure_min mod 1440 / 60) | קיים | — |
| `evidence` | ראו §4 | קיים חלקית | — |
| `proposal` | `{"duration_after": w, "delta_min": w−prev, "basis": "P80 מעוגל למעלה"}` או null | קיים (want) | — |
| `decision` | `{"action": "unchanged|change", "code": "<קוד החלטה>", "reason": "<טקסט>", "tier": "A|R|B|D"}` | קיים | — |
| `gate_status` | `"candidate"` (ברירת מחדל היום) / `"passed_engine_gates"` (רק כשההסקה תושלם ורמה A תחזור לפעול) / `"blocked"` | נגזר | הכרעת מדיניות (אורי) על החזרת רמה A |
| `apply_in_experiment` | true רק אם gate_status=candidate ו-decision.code ∈ {extend, review עם אישור ידני} | חסר | הכרעת אורי: אילו קודים נכנסים לניסוי |
| `stops` | 2 שורות StopTimes (מוצא/יעד) עם זמנים בדקות; >2 תחנות = blocked_intermediate | קיים | — |

## 4. סכמת ראיות (במקום טקסט חופשי)

```
"evidence": {
  "cell_key": "branch|day|season|makat|dir|alt|hour|month",   // קיים (evidence_key)
  "month": "2026-08", "season": "לימודים|קיץ",
  "n": 41, "n_days": 19,                                        // קיים
  "p50": 38.2, "p80": 43.667, "p90": 47.1 | null,               // קיים; p80 בדיוק מלא (סף), p90 null מתחת לסף התצוגה (20)
  "spread": {"iqr": 4.0, "relative_iqr": 0.105, "p90_minus_p80": 3.4, "status": "within_policy|review|unavailable"},   // קיים (spreadCheck)
  "stability": {"prev_month": "2026-07", "prev_p80": 42.9, "delta": 0.77, "tol": 5.0, "stable": true} | null,       // קיים
  "tier_descriptive": "A|R|B|D", "tier_effective": "R|B|D", "inference_pending": true,   // קיים
  "coverage_note": "P80 = אחוזון לינארי; שיעור הכיסוי בפועל מחושב בנפרד (cov80_n) ואינו 80% מובטח",
  "missing": ["..."]                                            // חסר אינו אפס
}
```

## 5. כלל שבת/מוצ"ש ואחרי חצות (הצעה)
- נסיעה במפת חול/שישי עם יציאה ≥ 24:00: `day_type_for_evidence` = סוג היום של המפה, `hour_cell` = 0–3. תואם לצינור (night-fixed: יום השירות הקודם).
- נסיעה במפת שבת: אם יציאה (בשעון) ≥ צאת שבת + 40 של תאריך הייחוס העונתי (`meta.sat_boundaries[sid][season].b`, כפי שהבנייה עושה לכריות ב-`build_dashboard_v2.py` 440–476) → `day_type_for_evidence` = מוצ"ש, אחרת שבת. יציאה ≥ 24:00 במפת שבת = מוצ"ש. הכלל נרשם בחבילה (`source.day_type_rule`) ובכל נסיעה, כך שקורא חיצוני יכול לשחזר.
- אי-התאמה בין לשונית היום שבחר המשתמש לסוג היום של הנסיעה = ראיה `null` עם `missing_reason: "day_type_mismatch"`, לא התאמה לתא הקרוב.

## 6. בדיקות שליליות שהחבילה חייבת להיכשל בהן (לקורסר)
1. `trips_parquet_sha256` שונה מזה שבבנייה שממנה נלקחו הראיות.
2. `template.sha256` שונה מהקובץ שנטען; `origin` = build_asset כש-(ד) דורש copy_export.
3. `eligibility.status` = "דוגמה" עם `apply_in_experiment` = true באיזו נסיעה.
4. `departure_min` < 0 או ≥ 2880; `arrival_min_before` < `departure_min`.
5. שתי רשומות עם אותו `optibus_trip_id`; או המלצה ל-trip שאינו בתבנית (unmatched).
6. `decision.code` ∈ {blocked, blocked_intermediate, rec_conflict, cut_envelope} עם `apply_in_experiment` = true.
7. שדה ראייתי חסר שמופיע כ-0 במקום null.
8. `day_type_for_evidence` שאינו עקבי עם §5.

## 7. מה תלוי בהכרעות אורי (ריכוז)
| הכרעה | שדות תלויים | עד להכרעה |
|---|---|---|
| (ב) תאריכי יעד/קוד יום | target.days_code.verified, target.target_dates, target.eligibility | eligibility = "דוגמה"; אין טעינה לעותק |
| (ג) פורמט | target.template.format, אופן כתיבת הזמנים, גיליונות נוספים (Stops/ReliefPoints/Taxis/Parameters) | המנוע מייצר 19 עמודות; ייצוא Optibus 22 עמודות נדחה ב-templateFromUpdateTrips |
| (ד) תבנית מהעותק | target.copy_id, target.template.origin, optibus_trip_id, duration_before | מזהים וזמני "לפני" הם של המקור, לא של העותק |
| החזרת רמה A / קודים שנכנסים לניסוי | gate_status, apply_in_experiment | הכל candidate; אפס החלות |

## 8. מה לא בהצעה
API/מתאם Optibus, תור, הרצה, קריאה חוזרת של תוצאות (קודקס/ג'יורג'יו); חישוב מחדש של אחוזונים (אין; המקורות הסטטיסטיים נשמרים); שינוי כלשהו במסך או במנוע לפני הכרעות (ב)–(ד).
