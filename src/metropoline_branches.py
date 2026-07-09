"""מיפוי קווי מטרופולין למק\"ט → אשכול + סניף.

מקור ברירת המחדל:
  data/reference/metropoline_line_branch_map.csv
  (מיוצא מקובץ \"קווים לפי מקט קו אשכול סניף…\")
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

METROPOLINE_OPERATOR_REF = 15
UNASSIGNED_BRANCH = "לא משויך"

_DEFAULT_CSV = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "reference"
    / "metropoline_line_branch_map.csv"
)


@dataclass(frozen=True)
class BranchInfo:
    route_mkt: str
    route_short_name: str
    cluster: str
    branch: str
    vehicle_type: str = ""
    line_uniqueness: str = ""
    od: str = ""


def _strip_zeros(code: str | int | None) -> str:
    s = str(code or "").strip().lstrip("0")
    return s or "0"


def default_branch_map_path() -> Path:
    return _DEFAULT_CSV


def load_branch_map(path: str | Path | None = None) -> dict[str, BranchInfo]:
    """טוען מיפוי route_mkt (ללא אפסים מובילים) → BranchInfo.

    אם אותו מק\"ט מופיע יותר מפעם — נשמרת הרשומה הראשונה.
    """
    csv_path = Path(path) if path else _DEFAULT_CSV
    if not csv_path.exists():
        raise FileNotFoundError(f"Metropoline branch map not found: {csv_path}")

    out: dict[str, BranchInfo] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mkt = _strip_zeros(row.get("route_mkt") or row.get('מק"ט'))
            if not mkt or mkt in out:
                continue
            branch = (row.get("branch") or row.get("סניף") or "").strip() or UNASSIGNED_BRANCH
            cluster = (row.get("cluster") or row.get("אשכול") or "").strip()
            out[mkt] = BranchInfo(
                route_mkt=mkt,
                route_short_name=str(
                    row.get("route_short_name") or row.get("קו") or ""
                ).strip(),
                cluster=cluster,
                branch=branch,
                vehicle_type=str(
                    row.get("vehicle_type") or row.get("סוג הרכב") or ""
                ).strip(),
                line_uniqueness=str(
                    row.get("line_uniqueness") or row.get("ייחודיות קו") or ""
                ).strip(),
                od=str(row.get("od") or row.get("מוצא/יעד") or "").strip(),
            )
    return out


def branch_for_mkt(mkt: str | None, branch_map: dict[str, BranchInfo]) -> str:
    """מחזיר שם/קוד סניף למק\"ט, או 'לא משויך'."""
    if not mkt:
        return UNASSIGNED_BRANCH
    info = branch_map.get(_strip_zeros(mkt))
    return info.branch if info else UNASSIGNED_BRANCH


def branch_for_line(
    operator_ref: int,
    line_ref: str,
    line_mkt: dict[tuple[int, str], str],
    branch_map: dict[str, BranchInfo],
) -> str:
    """סניף לקו מטרופולין בלבד; למפעילים אחרים מחזיר מחרוזת ריקה."""
    if operator_ref != METROPOLINE_OPERATOR_REF:
        return ""
    mkt = line_mkt.get((operator_ref, str(line_ref)))
    return branch_for_mkt(mkt, branch_map)
