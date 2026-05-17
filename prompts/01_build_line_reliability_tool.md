# Codex Task: Build line reliability analyzer

בנה כלי Python שמקבל:

- service_date
- operator_ref
- route_short_name
- optional line_ref
- optional hour window

ומחזיר דוח planned vs actual לפי Open Bus Stride.

דרישות:

1. קרא קודם את `AGENTS.md` ואת החוברות בתיקיית `docs/`.
2. אם לא ניתן `line_ref`, מצא מועמדים דרך `/gtfs_routes/list`.
3. משוך planned/actual דרך `/rides_execution/list` כאשר אפשר.
4. החזר DataFrame + סיכום KPI:
   - planned rides
   - actual rides
   - matched rides
   - missing actual start
   - null gtfs_ride_id rate
5. שמור CSV ו־HTML RTL.
6. כתוב tests ללא קריאת רשת באמצעות monkeypatch.
