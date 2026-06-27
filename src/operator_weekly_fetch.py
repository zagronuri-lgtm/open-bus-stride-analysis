"""Fetch planned-vs-actual rides per operator/day for a week from Open Bus Stride.

Uses /rides_execution/list (planned_start_time, actual_start_time) per line_ref,
aggregates to (operator_ref, service_date) level, and writes a tidy CSV.

Run:  python -m src.operator_weekly_fetch --date-from 2026-05-24 --date-to 2026-05-30 \
        --out data/_rides_execution_raw_2026W22.csv
"""

from __future__ import annotations

import argparse
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import threading

import pandas as pd
import requests

from src.exclusions_calendar import (
    DEFAULT_EXCLUSIONS_DIR,
    ExclusionsCalendarError,
    classify_date,
    load_calendar,
)

BASE = "https://open-bus-stride-api.hasadna.org.il"

# operator_ref -> Hebrew name, verified live against the API on 2026-05-30.
OPERATORS = {
    18: "קווים",
    15: "מטרופולין",
    3: "אגד",
    5: "דן",
    34: "תנופה",
    16: "סופרבוס",
    25: "אלקטרה אפיקים",
    4: "אלקטרה אפיקים תחבורה",
}

LATE_THRESHOLD_MIN = 5      # user-requested late metric
PENALTY_LATE_MIN = 10       # penalties workbook table 3 ("חריגה מעל 10 דקות")
EARLY_THRESHOLD_MIN = 2     # penalties workbook table 4 ("הקדמה מ-2 דקות")


def _get(session: requests.Session, path: str, *, timeout: int = 120, retries: int = 3, **params):
    params = {k: v for k, v in params.items() if v is not None}
    last = None
    for attempt in range(retries):
        try:
            r = session.get(BASE + path, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"GET {path} failed after {retries} tries: {last}")


def list_line_refs(session, operator_ref, date_from, date_to):
    routes = _get(
        session, "/gtfs_routes/list",
        date_from=date_from, date_to=date_to,
        operator_refs=str(operator_ref), limit=15000,
    )
    return sorted({r["line_ref"] for r in routes if r.get("line_ref") is not None})


def fetch_line(session, operator_ref, line_ref, date_from, date_to):
    """Return list of (local_service_date, planned, executed) per service date.

    'executed' = ride was matched to a SIRI real-time ride (actual_start_time
    is not null). NOTE: in this API actual_start_time mirrors the scheduled
    time, so departure lateness is NOT derivable here; only execution/missing.
    Dates are bucketed by Asia/Jerusalem local time to align with service days.
    """
    rides = _get(
        session, "/rides_execution/list",
        date_from=date_from, date_to=date_to,
        operator_ref=str(operator_ref), line_ref=str(line_ref),
        order_by="planned_start_time asc", limit=15000,
    )
    agg = collections.defaultdict(lambda: [0, 0])  # planned, executed
    for r in rides:
        p = r.get("planned_start_time")
        if not p:
            continue
        date = pd.Timestamp(p).tz_convert("Asia/Jerusalem").strftime("%Y-%m-%d")
        rec = agg[date]
        rec[0] += 1
        if r.get("actual_start_time"):
            rec[1] += 1
    return [(d, *v) for d, v in agg.items()]


def annotate_exclusions(df, *, exclusions_dir=DEFAULT_EXCLUSIONS_DIR, ignore=False):
    """Tag each row with the national exclusion treatment for its service_date.

    Raw fetch: rows are annotated (drop/segment/keep/normal), never deleted, so
    the report layer can exclude or segment days while keeping the full pull.
    """
    df = df.copy()
    if "service_date" not in df.columns or df.empty:
        df["exclusion_treatment"] = pd.Series(dtype=str)
        df["exclusion_name"] = pd.Series(dtype=str)
        return df
    if ignore or exclusions_dir is None:
        df["exclusion_treatment"] = "normal"
        df["exclusion_name"] = ""
        return df
    try:
        entries = load_calendar(exclusions_dir)
    except (ExclusionsCalendarError, OSError) as exc:
        print(f"[warn] exclusions calendar unavailable ({exc}); marking all days normal", flush=True)
        df["exclusion_treatment"] = "normal"
        df["exclusion_name"] = ""
        return df

    cache: dict[str, tuple[str, str]] = {}
    for value in df["service_date"].unique():
        result = classify_date(date.fromisoformat(str(value)), entries, national_only=True)
        cache[value] = (result.treatment, result.name or "")
    df["exclusion_treatment"] = df["service_date"].map(lambda v: cache[v][0])
    df["exclusion_name"] = df["service_date"].map(lambda v: cache[v][1])
    return df


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date-from", required=True)
    ap.add_argument("--date-to", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--operators", default=None, help="comma list of refs to limit (debug)")
    ap.add_argument("--exclusions-dir", default=str(DEFAULT_EXCLUSIONS_DIR), help="Exclusions calendar directory.")
    ap.add_argument("--ignore-exclusions", action="store_true", help="Skip exclusion tagging (debug).")
    args = ap.parse_args(argv)

    refs = [int(x) for x in args.operators.split(",")] if args.operators else list(OPERATORS)
    session = requests.Session()

    # 1) enumerate line_refs per operator
    tasks = []
    for ref in refs:
        lines = list_line_refs(session, ref, args.date_from, args.date_to)
        print(f"[lines] op {ref} {OPERATORS.get(ref,'?')}: {len(lines)} lines", flush=True)
        tasks.extend((ref, lr) for lr in lines)
    print(f"[total] {len(tasks)} (operator,line) tasks", flush=True)

    keep_dates = set(pd.date_range(args.date_from, args.date_to).strftime("%Y-%m-%d"))

    # 2) fetch concurrently, aggregate to (operator, date)
    rows = collections.defaultdict(lambda: [0, 0])  # planned, executed
    lock = threading.Lock()
    done = [0]
    errors = []

    def work(task):
        ref, lr = task
        try:
            return ref, fetch_line(session, ref, lr, args.date_from, args.date_to), None
        except Exception as exc:  # noqa: BLE001
            return ref, [], f"op{ref}/line{lr}: {exc}"

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(work, t) for t in tasks]
        for fut in as_completed(futures):
            ref, line_rows, err = fut.result()
            with lock:
                done[0] += 1
                if err:
                    errors.append(err)
                for (date, planned, executed) in line_rows:
                    if date not in keep_dates:
                        continue
                    rec = rows[(ref, date)]
                    rec[0] += planned; rec[1] += executed
                if done[0] % 250 == 0:
                    print(f"[progress] {done[0]}/{len(tasks)} lines, {len(errors)} errors", flush=True)

    # 3) tidy frame
    out = []
    for (ref, date), (planned, executed) in sorted(rows.items()):
        out.append({
            "operator_ref": ref,
            "operator_name": OPERATORS.get(ref, str(ref)),
            "service_date": date,
            "planned": planned,
            "executed": executed,
            "missing": planned - executed,
        })
    df = pd.DataFrame(out)
    df = annotate_exclusions(df, exclusions_dir=args.exclusions_dir, ignore=args.ignore_exclusions)
    df.to_csv(args.out, index=False)
    flagged = df[df["exclusion_treatment"].isin(["drop", "segment"])] if not df.empty else df
    if not flagged.empty:
        tagged = sorted(set(zip(flagged["service_date"], flagged["exclusion_treatment"], flagged["exclusion_name"])))
        print(f"[exclusions] flagged days: {tagged}", flush=True)
    print(f"[done] wrote {len(df)} rows to {args.out}; total lines={done[0]}; errors={len(errors)}", flush=True)
    if errors:
        print("[errors sample]", errors[:5], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
