"""Tests for src/endpoint_catalog.py — no network calls."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.endpoint_catalog import (
    _optional_params,
    _required_params,
    _use_case_for,
    save_csv,
    save_html,
    save_markdown,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ROWS = [
    {
        "group_en": "user cases",
        "group_he": "מקרי שימוש",
        "method": "GET",
        "path": "/rides_execution/list",
        "summary": "השוואת נסיעות",
        "description": "Compare planned vs actual rides.",
        "required_params": "",
        "optional_params": "limit, offset, date_from, date_to",
        "analyst_use_case": "ביצוע מול תכנון",
    },
    {
        "group_en": "gtfs",
        "group_he": "נתוני GTFS (תכנון)",
        "method": "GET",
        "path": "/gtfs_routes/list",
        "summary": "מסלולים",
        "description": "Planned routes.",
        "required_params": "",
        "optional_params": "limit, offset",
        "analyst_use_case": "מסלולים מתוכננים (GTFS)",
    },
]


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------

def test_use_case_for_known_prefix() -> None:
    assert "ביצוע" in _use_case_for("/rides_execution/list")


def test_use_case_for_unknown_prefix_returns_empty() -> None:
    assert _use_case_for("/unknown/endpoint") == ""


def test_required_params_filters_correctly() -> None:
    params = [
        {"name": "date_from", "required": True},
        {"name": "limit", "required": False},
        {"name": "operator_ref", "required": True},
    ]
    result = _required_params(params)
    assert "date_from" in result
    assert "operator_ref" in result
    assert "limit" not in result


def test_optional_params_filters_correctly() -> None:
    params = [
        {"name": "date_from", "required": True},
        {"name": "limit", "required": False},
    ]
    result = _optional_params(params)
    assert "limit" in result
    assert "date_from" not in result


# ---------------------------------------------------------------------------
# Unit tests: save functions
# ---------------------------------------------------------------------------

def test_save_csv_creates_file_with_correct_headers(tmp_path: Path) -> None:
    out = tmp_path / "catalog.csv"
    save_csv(SAMPLE_ROWS, out)
    assert out.exists()
    with out.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["path"] == "/rides_execution/list"
    assert rows[0]["method"] == "GET"


def test_save_csv_empty_rows_does_nothing(tmp_path: Path) -> None:
    out = tmp_path / "empty.csv"
    save_csv([], out)
    assert not out.exists()


def test_save_markdown_contains_group_and_path(tmp_path: Path) -> None:
    out = tmp_path / "catalog.md"
    save_markdown(SAMPLE_ROWS, out)
    text = out.read_text(encoding="utf-8")
    assert "/rides_execution/list" in text
    assert "מקרי שימוש" in text  # Hebrew group heading


def test_save_html_contains_required_elements(tmp_path: Path) -> None:
    out = tmp_path / "catalog.html"
    save_html(SAMPLE_ROWS, out)
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html" in text.lower()
    assert 'dir="rtl"' in text
    assert "/rides_execution/list" in text
    assert "/gtfs_routes/list" in text
    assert "badge" in text


def test_save_html_row_count_matches(tmp_path: Path) -> None:
    out = tmp_path / "catalog.html"
    save_html(SAMPLE_ROWS, out)
    text = out.read_text(encoding="utf-8")
    # Each row produces one <tr> in tbody
    assert text.count("<tr>") >= len(SAMPLE_ROWS)
