"""claude-adapter — local preparation of an Optibus UPDATE TRIPS workbook from a validated job (v2, after review round 1).

Scope (deliberately narrow): take (1) a ValidatedJob produced by ../implementation/bridge_contract.validate_job and
(2) a real "EXPORT TRIPS" workbook exported from the experiment COPY in Optibus, together with a manifest that
identifies that copy (ids, saved revision, sha256), and produce a new workbook in which ONLY the Arrival cell of
changed trips (sheet Trips) and the Time cell of their last stop (sheet StopTimes) differ from the template.
Everything else — every trip, every catalog sheet, every other cell — is preserved.

No network, no browser, no Optibus call. Uploading/saving/running stays a separate, explicitly approved step
(see DELIVERY-RECIPE.md). Data problems are returned as blocks (status='blocked'), never silently fixed.

Trust boundary: the ValidatedJob is re-validated here (validate_job on its stored JSON) and its digest/run_id must
match; the manifest and the template come from the operator's own registry/export, never from the browser.
Output layout: <out_dir>/<digest>/{update_trips.xlsx, report.json, SHA256SUMS.txt}; run_id is content only.

Proven basis (30.9.2026, copy sTcL7jQK5W): an in-place edit of the copy's own export with 876 Arrival/Time cells
changed and columns customer/Custom/tp removed was accepted by UPDATE TRIPS (POST 201) and read back exactly.
"""
from __future__ import annotations
import datetime as dt
import hashlib
from io import BytesIO
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'implementation'))
from bridge_contract import validate_job, ContractError  # noqa: E402

REQUIRED_SHEETS = ('Trips', 'StopTimes', 'Places', 'TripIdsMapping')
DROP_COLUMNS = {'Trips': ('customer', 'Custom'), 'StopTimes': ('tp',)}
MANIFEST_KEYS = {'dataset_id', 'schedule_id', 'revision', 'sha256', 'origin_schedule_id', 'days', 'exported_at', 'export_method'}
TRIP_COLUMNS = ('Id', 'Catalog Number', 'Sign', 'Direction', 'Alternative', 'Day Offset', 'Departure', 'Arrival', 'Distance', 'Days', 'Route Id', 'Origin Stop id', 'Destination Stop Id')
STOP_COLUMNS = ('Trip Id', 'Time', 'Point Id', 'distance', 'Sequence')
DAY_MAX = 4320
OUTPUT_NAME, REPORT_NAME, SUMS_NAME = 'update_trips.xlsx', 'report.json', 'SHA256SUMS.txt'


class AdapterError(ValueError):
    """Programming/usage error (bad arguments). Data problems are reported as blocks, not raised."""


class CellError(ValueError):
    """A cell that cannot be decoded unambiguously (reported as a block for the owning row)."""


@dataclass
class TripRow:
    row: int
    trip_id: str
    makat: str
    sign: str
    direction: str
    alternative: str
    route_id: str
    origin_stop: str
    destination_stop: str
    day_offset: int
    departure_min: int          # service minutes, Day Offset folded in
    arrival_min: int            # service minutes; export shows clock time, earlier-than-departure clock means next day
    days: str
    distance: float | None      # None when missing/non-finite (blocked later)


@dataclass
class Result:
    status: str                              # 'prepared' | 'blocked'
    run_id: str
    digest: str
    template_sha256: str
    job_dir: str | None = None
    output_path: str | None = None
    output_sha256: str | None = None
    report_path: str | None = None
    trips_total: int = 0
    trips_changed: int = 0
    minutes_delta_sum: int = 0
    arrivals_crossing_midnight_after: int = 0
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    changed: list = field(default_factory=list)   # [{id, before, after, delta}]
    diff_summary: dict = field(default_factory=dict)
    roundtrip: dict = field(default_factory=dict)
    dropped_columns: dict = field(default_factory=dict)
    not_proven: list = field(default_factory=list)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def cell_clock(value) -> int:
    """Strict decode of a clock cell to minutes in [0,1440). Accepts datetime.time/datetime with 0 seconds,
    'HH:MM' / 'HH:MM:00' text with 0<=HH<=23, or an exact Excel day fraction. Anything else raises CellError."""
    if value is None or value == '':
        raise CellError('empty time cell')
    if isinstance(value, bool):
        raise CellError('boolean time cell')
    if isinstance(value, dt.datetime):
        value = value.time()
    if isinstance(value, dt.time):
        if value.second or value.microsecond:
            raise CellError(f'time cell has seconds: {value.isoformat()}')
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)):
        f = float(value)
        if not math.isfinite(f) or f < 0 or f >= 1:
            raise CellError(f'day fraction out of range: {value!r}')
        m = f * 1440
        if abs(m - round(m)) > 1e-6:
            raise CellError(f'day fraction not on a whole minute: {value!r}')
        return int(round(m))
    text = str(value).strip()
    parts = text.split(':')
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise CellError(f'unreadable time cell: {value!r}')
    hh, mm = int(parts[0]), int(parts[1])
    ss = int(parts[2]) if len(parts) == 3 else 0
    if hh > 23 or mm > 59 or ss != 0:
        raise CellError(f'clock text out of range (HH<=23, MM<=59, SS=0 required): {text}')
    return hh * 60 + mm


def day_offset(value) -> int:
    """Decode a required offset consistently before and after writing; missing is not zero."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise CellError('Day Offset missing or invalid')
    try:
        offset = int(str(value).strip())
    except ValueError as exc:
        raise CellError(f'Day Offset {value!r} is not an integer') from exc
    if not 0 <= offset <= 3:
        raise CellError(f'Day Offset {value!r} out of range')
    return offset


def time_cell(minutes: int) -> dt.time:
    return dt.time((minutes % 1440) // 60, minutes % 60)


def decode_service_times(dep_clock: int, arr_clock: int, day_offset: int) -> tuple[int, int]:
    """Export convention: both clocks are mod 24h; Day Offset belongs to the departure; an arrival clock earlier
    than the departure clock means the next day. Unambiguous only for durations < 1440."""
    dep = dep_clock + 1440 * day_offset
    arr = arr_clock + 1440 * day_offset
    if arr < dep:
        arr += 1440
    return dep, arr


def load_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise AdapterError(f'manifest keys must be exactly {sorted(MANIFEST_KEYS)}')
    for k in MANIFEST_KEYS:
        if not isinstance(manifest[k], str) or not manifest[k].strip():
            raise AdapterError(f'manifest.{k} must be nonempty text')
    return manifest


def _headers(ws) -> list:
    return [c.value for c in ws[1]]


def _check_headers(ws, required: tuple[str, ...], where: str, blocks: list) -> dict[str, int] | None:
    h = _headers(ws)
    names = [str(x) for x in h if x is not None and str(x).strip() != '']
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        blocks.append(f'{where}: duplicate header(s) {dup}')
    if len(names) != len(h):
        blocks.append(f'{where}: empty header cell(s)')
    missing = [c for c in required if c not in names]
    if missing:
        blocks.append(f'{where}: missing column(s) {missing}')
    if dup or missing or len(names) != len(h):
        return None
    return {n: h.index(n) + 1 for n in required}


def _finite(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def read_trips(ws, blocks: list) -> dict[str, TripRow]:
    ix = _check_headers(ws, TRIP_COLUMNS, 'Trips', blocks)
    if ix is None:
        return {}
    trips: dict[str, TripRow] = {}
    for r in range(2, ws.max_row + 1):
        tid = ws.cell(r, ix['Id']).value
        if tid is None or str(tid).strip() == '':
            continue
        tid = str(tid)
        if tid in trips:
            blocks.append(f'Trips: duplicate trip id {tid}')
            continue
        try:
            origin_stop = ws.cell(r, ix['Origin Stop id']).value
            destination_stop = ws.cell(r, ix['Destination Stop Id']).value
            if any(v is None or not str(v).strip() for v in (origin_stop, destination_stop)):
                raise CellError('Origin/Destination Stop id missing')
            off_raw = ws.cell(r, ix['Day Offset']).value
            off = day_offset(off_raw)
            dep_c = cell_clock(ws.cell(r, ix['Departure']).value)
            arr_c = cell_clock(ws.cell(r, ix['Arrival']).value)
        except (CellError, ValueError) as exc:
            blocks.append(f'{tid}: {exc}')
            continue
        dep_min, arr_min = decode_service_times(dep_c, arr_c, off)
        trips[tid] = TripRow(r, tid, str(ws.cell(r, ix['Catalog Number']).value), str(ws.cell(r, ix['Sign']).value),
                             str(ws.cell(r, ix['Direction']).value), str(ws.cell(r, ix['Alternative']).value),
                             str(ws.cell(r, ix['Route Id']).value), str(origin_stop), str(destination_stop), off, dep_min, arr_min,
                             str(ws.cell(r, ix['Days']).value), _finite(ws.cell(r, ix['Distance']).value))
    return trips


def read_stoptimes(ws, trip_ids: set[str], blocks: list) -> dict[str, list[dict]]:
    ix = _check_headers(ws, STOP_COLUMNS, 'StopTimes', blocks)
    if ix is None:
        return {}
    out: dict[str, list[dict]] = {}
    for r in range(2, ws.max_row + 1):
        tid = ws.cell(r, ix['Trip Id']).value
        if tid is None or str(tid).strip() == '':
            continue
        tid = str(tid)
        if tid not in trip_ids:
            blocks.append(f'StopTimes row {r}: orphan trip id {tid}')
            continue
        seq_raw = ws.cell(r, ix['Sequence']).value
        seq = seq_raw if isinstance(seq_raw, int) and not isinstance(seq_raw, bool) else None
        if seq is None and isinstance(seq_raw, float) and seq_raw.is_integer():
            seq = int(seq_raw)
        if seq is None:
            blocks.append(f'{tid}: StopTimes row {r} Sequence not an integer ({seq_raw!r})')
            continue
        try:
            tm = cell_clock(ws.cell(r, ix['Time']).value)
        except CellError as exc:
            blocks.append(f'{tid}: StopTimes row {r}: {exc}')
            continue
        out.setdefault(tid, []).append(dict(row=r, seq=seq, time=tm, point=str(ws.cell(r, ix['Point Id']).value), distance=_finite(ws.cell(r, ix['distance']).value)))
    for tid, rows in out.items():
        rows.sort(key=lambda x: x['seq'])
        seqs = [x['seq'] for x in rows]
        if seqs != list(range(len(rows))):
            blocks.append(f'{tid}: StopTimes Sequence must be 0..n-1 without gaps/duplicates, got {seqs}')
    return out


def _revalidate(validated_job) -> tuple[dict | None, str | None]:
    try:
        payload = json.loads(validated_job.payload_json)
        snapshot = json.loads(validated_job.snapshot_json)
        again = validate_job(payload, snapshot)
    except (ContractError, ValueError, TypeError, AttributeError) as exc:
        return None, f'job re-validation failed: {exc}'
    if again.digest != validated_job.digest or again.run_id != validated_job.run_id:
        return None, 'job digest/run_id do not match re-validation (forged or stale ValidatedJob)'
    return payload, None


def prepare(validated_job, template_path: str, manifest: dict, out_dir: str, *, drop_columns: bool = True,
            protected_schedule_ids: tuple[str, ...] = ()) -> Result:
    """Build the UPDATE TRIPS workbook under <out_dir>/<digest>/. Returns Result(status='prepared'|'blocked').
    report.json is always written (inside the job directory); the workbook only when prepared. Never touches the template."""
    manifest = load_manifest(manifest)
    if not os.path.isfile(template_path):
        raise AdapterError('template file missing')
    digest = str(getattr(validated_job, 'digest', ''))
    if not (len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)):
        raise AdapterError('validated_job.digest must be 64 lowercase hex chars')
    run_id = str(getattr(validated_job, 'run_id', ''))
    with open(template_path, 'rb') as template_stream:
        template_bytes = template_stream.read()
    tpl_sha = hashlib.sha256(template_bytes).hexdigest()
    res = Result(status='blocked', run_id=run_id, digest=digest, template_sha256=tpl_sha)
    res.not_proven = [
        'הקובץ שנבנה כאן טרם הועלה ל-Optibus מהמתאם; המתכון הוכח ידנית ב-30.9 (עותק sTcL7jQK5W) בלבד',
        'הסרת העמודות customer/Custom/tp היא מתכון מוכח (28.8, 15.9, 30.9) אך לא הוכח שהיא הכרחית',
        'נסיעות עם תחנות ביניים לא נבדקו מעולם במסלול הזה ולכן נחסמות',
        'משך נסיעה >= 24 שעות אינו ניתן לייצוג חד-משמעי בתבנית הייצוא (שעון + Day Offset של היציאה) ולכן נחסם',
    ]
    job_dir = os.path.join(os.path.abspath(out_dir), digest)
    res.job_dir = job_dir
    if os.path.abspath(template_path).startswith(job_dir + os.sep):
        raise AdapterError('template must not live inside the job directory')
    try:
        os.makedirs(job_dir, exist_ok=False)
    except FileExistsError:
        res.blocks.append(f'job directory already reserved for digest {digest[:12]}; not overwriting')
        res.report_path = None
        return res  # Even an empty directory belongs to another attempt; never write or clean it.

    payload, err = _revalidate(validated_job)
    if err:
        res.blocks.append(err)
        return _finish(res)
    src = payload['source']
    if manifest['sha256'] != tpl_sha:
        res.blocks.append(f'template sha256 {tpl_sha[:12]} != manifest {manifest["sha256"][:12]}')
    if src['sha256'] != tpl_sha:
        res.blocks.append('payload.source.sha256 does not bind to this template export')
    for k in ('dataset_id', 'schedule_id', 'revision'):
        if src[k] != manifest[k]:
            res.blocks.append(f'payload.source.{k}={src[k]} != manifest.{k}={manifest[k]}')
    if manifest['schedule_id'] == manifest['origin_schedule_id']:
        res.blocks.append('copy schedule id equals origin schedule id (source == copy)')
    if manifest['schedule_id'] in protected_schedule_ids or src['schedule_id'] in protected_schedule_ids:
        res.blocks.append('target schedule is a protected map')
    if res.blocks:
        return _finish(res)

    try:
        wb = openpyxl.load_workbook(BytesIO(template_bytes))
    except Exception as exc:
        res.blocks.append(f'template decode failed: {type(exc).__name__}: {exc}')
        return _finish(res)
    for name in REQUIRED_SHEETS:
        if name not in wb.sheetnames:
            res.blocks.append(f'template lacks sheet {name}')
    if res.blocks:
        return _finish(res)
    trips = read_trips(wb['Trips'], res.blocks)
    stops = read_stoptimes(wb['StopTimes'], set(trips), res.blocks)
    res.trips_total = len(trips)
    if res.blocks:
        return _finish(res)

    ptrips = {t['id']: t for t in payload['trips']}
    missing = sorted(set(ptrips) - set(trips))
    extra = sorted(set(trips) - set(ptrips))
    if missing:
        res.blocks.append(f'{len(missing)} payload trips not in template, e.g. {missing[:3]}')
    if extra:
        res.blocks.append(f'{len(extra)} template trips not in payload (payload must be complete), e.g. {extra[:3]}')
    days_values = {t.days for t in trips.values()}
    if days_values != {manifest['days']}:
        res.blocks.append(f'Days column {sorted(days_values)} != manifest.days {manifest["days"]!r}')
    if res.blocks:
        return _finish(res)

    changes: dict[str, tuple[int, int]] = {}
    for tid, p in ptrips.items():
        t = trips[tid]
        if (p['makat'], p['direction'], p['alternative']) != (t.makat, t.direction, t.alternative):
            res.blocks.append(f'{tid}: identity mismatch payload {(p["makat"], p["direction"], p["alternative"])} vs template {(t.makat, t.direction, t.alternative)}')
        if p['line_number'] != t.sign:
            res.blocks.append(f'{tid}: line_number {p["line_number"]} != template Sign {t.sign}')
        if t.route_id != f'{t.makat}-{t.direction}-{t.alternative}':
            res.warnings.append(f'{tid}: Route Id {t.route_id} != makat-direction-alternative (left as is)')
        if p['departure'] != t.departure_min:
            res.blocks.append(f'{tid}: departure {p["departure"]} != template {t.departure_min}')
        if p['before_arrival'] != t.arrival_min:
            res.blocks.append(f'{tid}: before_arrival {p["before_arrival"]} != template {t.arrival_min}')
        if t.day_offset != t.departure_min // 1440:
            res.blocks.append(f'{tid}: Day Offset {t.day_offset} != departure // 1440')
        st = stops.get(tid, [])
        if not st:
            res.blocks.append(f'{tid}: no StopTimes rows')
            continue
        if st[0]['point'] != t.origin_stop:
            res.blocks.append(f'{tid}: first StopTimes Point Id != Origin Stop id')
        if st[-1]['point'] != t.destination_stop:
            res.blocks.append(f'{tid}: last StopTimes Point Id != Destination Stop Id')
        first_min, last_min = decode_service_times(st[0]['time'], st[-1]['time'], t.day_offset)
        if first_min != t.departure_min:
            res.blocks.append(f'{tid}: first StopTimes time != departure')
        if last_min != t.arrival_min:
            res.blocks.append(f'{tid}: last StopTimes time != arrival')
        legs = [s['distance'] for s in st]
        if t.distance is None:
            res.blocks.append(f'{tid}: Trips.Distance missing or not a finite number')
        elif t.distance < 0:
            res.blocks.append(f'{tid}: Trips.Distance is negative')
        elif any(x is None for x in legs):
            res.blocks.append(f'{tid}: StopTimes distance leg missing or not a finite number')
        elif any(x < 0 for x in legs):
            res.blocks.append(f'{tid}: StopTimes distance leg is negative')
        elif abs(sum(legs) - t.distance) > 0.0015:
            res.blocks.append(f'{tid}: StopTimes distance legs sum {sum(legs):.3f} != Trips.Distance {t.distance:.3f}')
        after = p['after_arrival']
        if after != p['before_arrival']:
            if after < p['before_arrival']:
                res.blocks.append(f'{tid}: decrease requested ({p["before_arrival"]}->{after}); contract v1 forbids')
            if after > DAY_MAX:
                res.blocks.append(f'{tid}: after_arrival {after} > {DAY_MAX}')
            if after - t.departure_min >= 1440:
                res.blocks.append(f'{tid}: duration {after - t.departure_min} >= 1440 cannot be represented unambiguously in the export format')
            if len(st) != 2:
                res.blocks.append(f'{tid}: {len(st)} stops — intermediate stop times are not proven; blocked')
            changes[tid] = (p['before_arrival'], after)
    if res.blocks:
        return _finish(res)

    ws_t, ws_s = wb['Trips'], wb['StopTimes']
    c_arr = _headers(ws_t).index('Arrival') + 1
    c_time = _headers(ws_s).index('Time') + 1
    for tid, (before, after) in sorted(changes.items()):
        t = trips[tid]
        ws_t.cell(t.row, c_arr).value = time_cell(after)
        ws_s.cell(stops[tid][-1]['row'], c_time).value = time_cell(after)
        if t.departure_min < 1440 <= after and before < 1440:
            res.arrivals_crossing_midnight_after += 1
        res.changed.append(dict(id=tid, before=before, after=after, delta=after - before))
    res.trips_changed = len(changes)
    res.minutes_delta_sum = sum(c['delta'] for c in res.changed)
    if drop_columns:
        for sheet, cols in DROP_COLUMNS.items():
            ws = wb[sheet]
            for col in cols:
                hs = _headers(ws)
                if col in hs:
                    ws.delete_cols(hs.index(col) + 1)
                    res.dropped_columns.setdefault(sheet, []).append(col)
    out_path = os.path.join(job_dir, OUTPUT_NAME)
    try:
        wb.save(out_path)
        res.output_path, res.output_sha256 = out_path, sha256_file(out_path)

        # --- independent verification 1: cell diff limited to the expected cells
        diff = verify_diff(template_bytes, out_path)
        res.diff_summary = diff
        expected = {'Trips.Arrival': len(changes), 'StopTimes.Time': len(changes)} if changes else {}
        unexpected = {k: v for k, v in diff.items() if not k.endswith('__dropped__') and k not in expected}
        dropped = {k.split('.')[0]: v for k, v in diff.items() if k.endswith('__dropped__')}
        if unexpected or any(diff.get(k) != v for k, v in expected.items()) or dropped != {k: sorted(v) for k, v in res.dropped_columns.items()}:
            res.blocks.append(f'post-write diff not limited to expected cells: {diff}')
        # --- independent verification 2: round-trip every trip's service times from the OUTPUT
        rt = roundtrip_check(out_path, {tid: (p['departure'], p['after_arrival']) for tid, p in ptrips.items()})
        res.roundtrip = rt
        if rt['mismatches']:
            res.blocks.append(f'round-trip mismatch for {len(rt["mismatches"])} trips, e.g. {rt["mismatches"][:3]}')
    except Exception as exc:
        res.blocks.append(f'output write/verification failed: {type(exc).__name__}: {exc}')
    if res.blocks:
        try:
            os.remove(out_path)
        except OSError:
            pass
        res.output_path = res.output_sha256 = None
        return _finish(res)
    res.status = 'prepared'
    return _finish(res)


def roundtrip_check(out_path: str, expected: dict[str, tuple[int, int]]) -> dict:
    """Reopen the produced workbook and reconstruct (departure, arrival) service minutes for every trip from Trips
    and from StopTimes; compare with the payload. Returns counts and the first mismatches."""
    wb = openpyxl.load_workbook(out_path, read_only=True)
    ws = wb['Trips']
    h = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    ix = {n: h.index(n) for n in ('Id', 'Day Offset', 'Departure', 'Arrival')}
    got: dict[str, tuple[int, int]] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[ix['Id']] is None:
            continue
        off = day_offset(r[ix['Day Offset']])
        got[str(r[ix['Id']])] = decode_service_times(cell_clock(r[ix['Departure']]), cell_clock(r[ix['Arrival']]), off)
    ss = wb['StopTimes']
    sh = [c.value for c in next(ss.iter_rows(min_row=1, max_row=1))]
    six = {n: sh.index(n) for n in ('Trip Id', 'Time', 'Sequence')}
    first: dict[str, tuple[int, int]] = {}
    last: dict[str, tuple[int, int]] = {}
    for r in ss.iter_rows(min_row=2, values_only=True):
        if r[six['Trip Id']] is None:
            continue
        tid, seq, tm = str(r[six['Trip Id']]), int(r[six['Sequence']]), cell_clock(r[six['Time']])
        if tid not in first or seq < first[tid][0]:
            first[tid] = (seq, tm)
        if tid not in last or seq > last[tid][0]:
            last[tid] = (seq, tm)
    wb.close()
    mismatches = []
    for tid, (dep, arr) in expected.items():
        g = got.get(tid)
        off = dep // 1440
        st = decode_service_times(first[tid][1], last[tid][1], off) if tid in first else None
        if g != (dep, arr) or st != (dep, arr):
            mismatches.append(dict(id=tid, expected=[dep, arr], trips_sheet=list(g) if g else None, stoptimes=list(st) if st else None))
    return dict(checked=len(expected), mismatches=mismatches)


def verify_diff(template_path: str | bytes, out_path: str) -> dict:
    """Compare cells against the frozen source bytes (paths retained for standalone callers)."""
    a = openpyxl.load_workbook(BytesIO(template_path) if isinstance(template_path, bytes) else template_path)
    b = openpyxl.load_workbook(out_path)
    diff: dict[str, Any] = {}
    if a.sheetnames != b.sheetnames:
        diff['__sheets__'] = [a.sheetnames, b.sheetnames]
        return diff
    for name in a.sheetnames:
        wa, wb_ = a[name], b[name]
        ha, hb = _headers(wa), _headers(wb_)
        if wa.max_row != wb_.max_row:
            diff[f'{name}.__rows__'] = [wa.max_row, wb_.max_row]
        for col in hb:
            if col not in ha:
                diff[f'{name}.__added__'] = diff.get(f'{name}.__added__', []) + [col]
                continue
            ca, cb = ha.index(col) + 1, hb.index(col) + 1
            n = sum(1 for r in range(2, max(wa.max_row, wb_.max_row) + 1) if wa.cell(r, ca).value != wb_.cell(r, cb).value)
            if n:
                diff[f'{name}.{col}'] = n
        dropped = sorted(str(c) for c in ha if c not in hb)
        if dropped:
            diff[f'{name}.__dropped__'] = dropped
    return diff


def _finish(res: Result) -> Result:
    res.report_path = os.path.join(res.job_dir, REPORT_NAME)
    with open(res.report_path, 'w', encoding='utf-8') as fh:
        json.dump(asdict(res) | {'generated_at': dt.datetime.now().isoformat(timespec='seconds')}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(res.job_dir, SUMS_NAME), 'w') as fh:
        if res.output_path:
            fh.write(f'{res.output_sha256}  {OUTPUT_NAME}\n')
        fh.write(f'{sha256_file(res.report_path)}  {REPORT_NAME}\n')
        fh.write(f'{res.template_sha256}  template\n')
    return res


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Prepare an UPDATE TRIPS workbook from a validated job (local only).')
    ap.add_argument('--payload', required=True); ap.add_argument('--snapshot', required=True)
    ap.add_argument('--template', required=True); ap.add_argument('--manifest', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--keep-columns', action='store_true', help='do not drop customer/Custom/tp')
    a = ap.parse_args()
    job = validate_job(json.load(open(a.payload, encoding='utf-8')), json.load(open(a.snapshot, encoding='utf-8')))
    r = prepare(job, a.template, json.load(open(a.manifest, encoding='utf-8')), a.out, drop_columns=not a.keep_columns)
    print(json.dumps(asdict(r), ensure_ascii=False, indent=1))
    sys.exit(0 if r.status == 'prepared' else 2)
