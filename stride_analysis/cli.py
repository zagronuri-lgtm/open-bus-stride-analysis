"""Command-line interface for stride_analysis.

Examples::

    # discover every operator live from the API
    python -m stride_analysis --list-operators

    # one operator, explicit window, write a Hebrew Markdown report
    python -m stride_analysis --operator 5 --from 2026-05-24 --to 2026-05-30 --report

    # every operator for last week, with penalty estimate and report
    python -m stride_analysis --all --last-week --report

Nothing is hard-coded: ``--operator`` takes any ``operator_ref`` (discoverable
via ``--list-operators``), and ``--all`` enumerates whatever the API returns for
the window.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stride_analysis import config
from stride_analysis.analysis.execution import calc_execution, execution_totals
from stride_analysis.analysis.penalties import (
    calc_penalties,
    load_penalty_tables,
    penalties_by_operator,
)
from stride_analysis.data.stride_client import StrideClient

_logger = config.get_logger("stride_analysis.cli")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="stride_analysis",
        description="Universal performance analysis for Israeli public transit (Open Bus Stride).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    selection = parser.add_argument_group("selection")
    selection.add_argument(
        "--list-operators", action="store_true",
        help="List all operators discovered for the window and exit.",
    )
    selection.add_argument(
        "--operator", metavar="REF", default=None,
        help="operator_ref to analyse (numeric). Discover refs with --list-operators.",
    )
    selection.add_argument(
        "--all", action="store_true",
        help="Analyse all operators discovered for the window.",
    )
    selection.add_argument(
        "--route", metavar="SHORT_NAME", default=None,
        help="Optional public line number filter (route_short_name).",
    )

    window = parser.add_argument_group("time window")
    window.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", default=None)
    window.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", default=None)
    window.add_argument("--last-week", action="store_true", help="Shortcut for the previous Sun–Sat week.")

    output = parser.add_argument_group("output")
    output.add_argument("--report", action="store_true", help="Write a Hebrew Markdown report to data/output/.")
    output.add_argument("--out", metavar="PATH", default=None, help="Explicit output path for CSV/report.")
    output.add_argument("--no-penalties", action="store_true", help="Skip penalty estimation.")
    output.add_argument("--no-cache", action="store_true", help="Bypass the local response cache.")
    output.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    output.add_argument("--quiet", action="store_true", help="Reduce logging to warnings.")
    return parser


def _resolve_window(args: argparse.Namespace) -> tuple[str, str]:
    if args.last_week:
        today = date.today()
        # Previous week: most recent Saturday back to the Sunday before it.
        # Israeli service week runs Sunday→Saturday.
        days_since_sunday = (today.weekday() + 1) % 7  # Monday=0 → Sunday start
        this_sunday = today - timedelta(days=days_since_sunday)
        last_sunday = this_sunday - timedelta(days=7)
        last_saturday = last_sunday + timedelta(days=6)
        return last_sunday.isoformat(), last_saturday.isoformat()
    if args.date_from and args.date_to:
        return args.date_from, args.date_to
    if args.date_from:
        return args.date_from, args.date_from
    raise SystemExit("error: provide --from/--to, or --last-week, or --list-operators")


def _make_client(args: argparse.Namespace) -> StrideClient:
    from stride_analysis.data.cache import Cache

    cache = Cache(enabled=not args.no_cache)
    return StrideClient(cache=cache)


def cmd_list_operators(client: StrideClient, args: argparse.Namespace) -> int:
    """Print all operators for the window (or a recent default date)."""
    date_from, date_to = (None, None)
    if args.date_from or args.last_week:
        date_from, date_to = _resolve_window(args)
    operators = client.list_operators(date_from=date_from, date_to=date_to)
    if not operators:
        print("No operators found for the requested window.", file=sys.stderr)
        return 1
    print(f"{'ref':>6}  {'routes':>7}  name")
    print("-" * 40)
    for op in operators:
        print(f"{op.operator_ref:>6}  {op.route_count or 0:>7}  {op.name}")
    print(f"\n{len(operators)} operators.")
    return 0


def cmd_analyse(client: StrideClient, args: argparse.Namespace) -> int:
    """Fetch rides, compute execution + penalties, optionally write a report."""
    date_from, date_to = _resolve_window(args)
    operator_ref = None if args.all else args.operator
    if operator_ref is None and not args.all:
        raise SystemExit("error: specify --operator REF or --all")

    label = "כלל המפעילים" if args.all else f"operator_ref={operator_ref}"
    _logger.info("Analysing %s for %s..%s", label, date_from, date_to)

    rides = client.fetch_rides(
        date_from, date_to,
        operator_ref=operator_ref,
        route_short_name=args.route,
        workers=args.workers,
        progress=not args.quiet,
    )
    if rides.empty:
        print("No rides returned for the requested selection.", file=sys.stderr)
        return 1

    group_by = [c for c in ("operator_ref", "operator_name", "service_date") if c in rides.columns]
    summary = calc_execution(rides, group_by=group_by)

    if not args.no_penalties:
        try:
            summary = calc_penalties(summary, load_penalty_tables())
        except (FileNotFoundError, ValueError) as exc:
            _logger.warning("Penalty estimation skipped: %s", exc)

    totals = execution_totals(summary)
    print(_format_console_summary(summary, totals))

    # Persist CSV.
    config.ensure_dirs()
    csv_path = Path(args.out) if (args.out and not args.report) else (
        config.OUTPUT_DIR / f"execution_{date_from}_{date_to}.csv"
    )
    summary.to_csv(csv_path, index=False)
    print(f"\nCSV → {csv_path}")

    if args.report:
        from stride_analysis.reports.generator import generate_report

        per_op = penalties_by_operator(summary) if "penalty_nis" in summary.columns else None
        report_path = Path(args.out) if args.out else (
            config.OUTPUT_DIR / f"report_{date_from}_{date_to}.md"
        )
        generate_report(
            summary,
            date_from=date_from,
            date_to=date_to,
            title=label if not args.all else "כלל המפעילים",
            daily=summary if "service_date" in summary.columns else None,
            request_count=len(client.request_log),
            out_path=report_path,
        )
        print(f"Report → {report_path}")
    return 0


def _format_console_summary(summary: pd.DataFrame, totals: dict) -> str:
    lines = [
        "",
        f"  planned : {totals['planned']:,}",
        f"  executed: {totals['executed']:,}",
        f"  missing : {totals['missing']:,} ({totals['missing_pct']}%)",
    ]
    if "penalty_nis" in summary.columns:
        lines.append(f"  penalty : ₪{int(summary['penalty_nis'].sum()):,}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.quiet:
        config.get_logger().setLevel("WARNING")

    client = _make_client(args)
    try:
        if args.list_operators:
            return cmd_list_operators(client, args)
        return cmd_analyse(client, args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
