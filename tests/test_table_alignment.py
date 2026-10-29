"""Aligned output: columns must start at the same terminal cell on every row.

`len()` is not terminal width. The scan table used "⚠️" and "ℹ️" (a narrow glyph plus a
variation selector): terminals disagree on whether they take 1 or 2 cells, so no padding
could line those rows up. Only unambiguous icons may appear in a table.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

from hermes_ops_kit.security.plugin_scanner import cli
from hermes_ops_kit.ui.console import Console
from hermes_ops_kit.ui.output import Table, print_table
from hermes_ops_kit.ui.width import display_width, is_unambiguous_icon, pad, truncate


@pytest.mark.parametrize(
    "text,cells",
    [
        ("abc", 3),
        ("", 0),
        ("✅", 2),
        ("🔴 x", 4),
        ("ENABLED ✓", 9),
        ("é", 1),  # a combining accent adds no cell
        (
            "⚠️",
            1,
        ),  # the variation selector itself adds none: the ambiguity is the terminal's
        ("日本", 4),
    ],
)
def test_display_width(text, cells):
    assert display_width(text) == cells


def test_pad_and_truncate_work_in_cells():
    assert pad("✅", 4) == "✅  " and display_width(pad("✅", 4)) == 4
    assert pad("7", 3, ">") == "  7"
    assert (
        truncate("abcdef", 4) == "abc…"
        and display_width(truncate("日本語日本語", 5)) <= 5
    )
    assert truncate("ab", 4) == "ab"


def test_every_icon_in_a_table_is_unambiguous():
    icons = [*cli.RISK_ICONS.values(), cli._UNKNOWN_ICON, "⛔", "🟡", "✅"]
    assert all(is_unambiguous_icon(icon) for icon in icons), icons
    # and the two that caused the misalignment are caught by the same check
    assert not is_unambiguous_icon("⚠️") and not is_unambiguous_icon("ℹ️")


def test_a_table_pads_by_cells_so_wide_characters_do_not_shift_columns():
    rows = [["✅", "a", "1"], ["🔴", "bb", "22"], ["⛔", "ccc", "333"]]
    table = Table(["", "NAME", "N"], rows, aligns="<<>")
    lines = [table.row(r) for r in rows]
    starts = {display_width(line[: line.index(r[1])]) for line, r in zip(lines, rows)}
    ends = {
        display_width(line[: line.rindex(r[2]) + len(r[2])])
        for line, r in zip(lines, rows)
    }
    assert len(starts) == 1 and len(ends) == 1  # one start cell, one right edge


def test_print_table_is_width_aware_and_json_mode_is_unchanged(capsys):
    con = Console(json_mode=False)
    print_table(con, ["", "NAME"], [["✅", "x"], ["🔴", "yy"]])
    lines = capsys.readouterr().out.splitlines()
    assert display_width(lines[2][: lines[2].index("x")]) == display_width(
        lines[3][: lines[3].index("yy")]
    )
    print_table(Console(json_mode=True), ["", "NAME"], [["✅", "x"]])
    assert json.loads(capsys.readouterr().out)["table"] == [{"": "✅", "NAME": "x"}]


def _result(name, risk, findings, cached):
    return SimpleNamespace(
        plugin_name=name,
        risk_level=SimpleNamespace(value=risk),
        findings=[SimpleNamespace()] * findings,
        cache_hit=cached,
    )


SCAN_ROWS = [
    ("apple", "none", 0, True, {"status": "enabled", "reason": "plugin_approved"}),
    (
        "herdr-agent-state",
        "medium",
        2,
        True,
        {"status": "enabled", "reason": "plugin_approved"},
    ),
    ("github-management", "low", 1, True, {"status": "enabled", "reason": "x"}),
    (
        "hermes-ops-kit",
        "high",
        317,
        False,
        {"status": "enabled", "reason": "plugin_approved"},
    ),
    ("social-media", "medium", 6, True, {"status": "disabled", "reason": "x"}),
    ("evil", "critical", 12, False, {"status": "blocked", "reason": "x"}),
    (
        "a-very-long-plugin-name-that-widens-the-column",
        "none",
        0,
        False,
        {"status": "enabled", "reason": "x"},
    ),
]


def _render_scan(detail="summary"):
    results = [_result(n, r, f, c) for n, r, f, c, _ in SCAN_ROWS]
    statuses = [s for *_, s in SCAN_ROWS]
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_scan_results(
            results, statuses, Console(json_mode=False), detail=detail
        )
    return buf.getvalue().splitlines()


def test_scan_columns_start_at_the_same_cell_on_every_row():
    lines = _render_scan()
    rows = [ln for ln in lines if any(ln.split()[1:2] == [n] for n, *_ in SCAN_ROWS)]
    assert len(rows) == len(SCAN_ROWS)

    def at(line, token):
        return display_width(line[: line.index(token)])

    name_cols = {at(ln, ln.split()[1]) for ln in rows}
    risk_cols = {
        at(
            ln,
            f" {next(t for t in ('NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL') if f' {t} ' in ln)} ",
        )
        for ln in rows
    }
    status_cols = {
        at(ln, next(s for s in ("ENABLED", "DISABLED", "BLOCKED") if s in ln))
        for ln in rows
    }
    assert len(name_cols) == len(risk_cols) == len(status_cols) == 1, (
        name_cols,
        risk_cols,
        status_cols,
    )


def test_the_findings_count_is_right_aligned_and_the_cache_tag_lines_up():
    lines = _render_scan()
    rows = [ln for ln in lines if any(ln.split()[1:2] == [n] for n, *_ in SCAN_ROWS)]
    ends, tags = set(), set()
    for ln in rows:
        tokens = ln.split()
        count = tokens[-2] if tokens[-1] == "[cache]" else tokens[-1]
        end = ln.rindex(count) + len(count)
        ends.add(display_width(ln[:end]))
        if tokens[-1] == "[cache]":
            tags.add(display_width(ln[: ln.rindex("[cache]")]))
    assert len(ends) == 1 and len(tags) == 1, (ends, tags)


def test_the_header_names_the_columns_and_no_row_says_findings_or_findings_s():
    lines = _render_scan()
    assert lines[0].split() == ["PLUGIN", "RISK", "STATUS", "FINDINGS"]
    assert set(lines[1].replace(" ", "")) == {"─"}
    assert not any(
        "findings)" in ln for ln in lines
    )  # the old "(1 findings)" wording is gone


def test_details_still_follow_their_row_in_verbose_mode():
    results = [_result("p", "medium", 0, False)]
    results[0].findings = [
        SimpleNamespace(
            rule="r",
            message="m",
            severity=SimpleNamespace(value="warning"),
            file_path="a.py",
            line=3,
            remediation="",
            id="p:c:r:abc",
        )
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_scan_results(
            results,
            [{"status": "enabled", "reason": "x"}],
            Console(json_mode=False),
            detail="verbose",
        )
    out = buf.getvalue()
    assert out.index(" p ") < out.index("[r]") < out.index("at a.py:3")


def test_the_cache_listing_is_aligned_too(monkeypatch):
    entries = [
        {
            "plugin_name": "short",
            "risk_level": "low",
            "scan_result": "clean",
            "scanned_at": "2026-10-29 10:00",
        },
        {
            "plugin_name": "a-longer-plugin-name",
            "risk_level": "high",
            "scan_result": "blocked",
            "scanned_at": "2026-10-29 11:00",
        },
        {
            "plugin_name": "mid",
            "risk_level": "medium",
            "scan_result": "warning",
            "scanned_at": "2026-10-29 12:00",
        },
    ]
    monkeypatch.setattr(cli, "cache_list", lambda: entries)
    monkeypatch.setattr(
        cli,
        "cache_stats",
        lambda: {"db_path": "x", "total_entries": 3, "expired_entries": 0},
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli._cmd_cache(["show"]) == 0
    rows = [ln for ln in buf.getvalue().splitlines() if "2026-10-29" in ln]
    starts = {display_width(ln[: ln.index("2026-10-29")]) for ln in rows}
    names = {
        display_width(ln[: ln.index(e["plugin_name"])]) for ln, e in zip(rows, entries)
    }
    assert len(rows) == 3 and len(starts) == 1 and len(names) == 1


def test_the_cache_listing_uses_the_same_icons_as_the_scan_table(monkeypatch):
    entries = [
        {
            "plugin_name": "a",
            "risk_level": "low",
            "scan_result": "clean",
            "scanned_at": "t1",
        },
        {
            "plugin_name": "b",
            "risk_level": "high",
            "scan_result": "warning",
            "scanned_at": "t2",
        },
        {
            "plugin_name": "c",
            "risk_level": "weird",
            "scan_result": "blocked",
            "scanned_at": "t3",
        },
    ]
    monkeypatch.setattr(cli, "cache_list", lambda: entries)
    monkeypatch.setattr(
        cli,
        "cache_stats",
        lambda: {"db_path": "x", "total_entries": 3, "expired_entries": 0},
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._cmd_cache(["show"])
    out = buf.getvalue()
    row = {
        e["plugin_name"]: next(
            ln for ln in out.splitlines() if f" {e['plugin_name']} " in ln
        )
        for e in entries
    }
    assert cli.RISK_ICONS["low"] in row["a"] and cli.RISK_ICONS["high"] in row["b"]
    assert (
        "⛔" in row["c"]
    )  # unknown risk level falls back to the cache's own classification


def test_finding_details_nest_under_the_plugin_name_column():
    results = [_result("p", "medium", 0, False)]
    results[0].findings = [
        SimpleNamespace(
            rule="r",
            message="m",
            severity=SimpleNamespace(value="warning"),
            file_path="a.py",
            line=3,
            remediation="fix it",
            id="p:c:r:abc",
        )
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_scan_results(
            results,
            [{"status": "enabled", "reason": "x"}],
            Console(json_mode=False),
            detail="explain",
        )
    lines = buf.getvalue().splitlines()
    row = next(ln for ln in lines if ln.split()[1:2] == ["p"])
    name_cell = display_width(row[: row.index(" p ") + 1])
    for marker in ("[r]", "at a.py:3", "fix:", "silence:", "approve:"):
        detail_line = next(ln for ln in lines if marker in ln)
        first = len(detail_line) - len(detail_line.lstrip())
        assert first >= name_cell, (marker, first, name_cell)
    finding_line = next(ln for ln in lines if "[r]" in ln)
    assert display_width(finding_line[: finding_line.index("🟡")]) == name_cell


# ── other commands' tables ────────────────────────────────────────────────────


def _col(lines: list[str], text: str) -> int:
    """Terminal cell at which ``text`` starts on the first line containing it."""
    line = next(ln for ln in lines if text in ln)
    return display_width(line[: line.index(text)])


def test_colour_codes_take_no_cells():
    assert display_width("\x1b[32mREADY\x1b[0m") == 5
    coloured = "\x1b[31mOFFLINE\x1b[0m"
    assert truncate(coloured, 3) == coloured  # never cut inside an escape sequence
    assert display_width(pad(coloured, 9)) == 9


def test_audit_tail_shows_the_date_and_aligns_the_event_column(monkeypatch, capsys):
    from hermes_ops_kit import commands
    from hermes_ops_kit.audit import ledger

    events = [
        {
            "ts": "2026-09-02T20:40:10Z",
            "type": "key_rotated",
            "provider": "p",
            "operation": "o",
            "status": "ok",
        },
        {
            "ts": "2026-09-02T20:41:00Z",
            "type": "route_bypass_detected",
            "route": "vision",
        },
        {"ts": "2026-09-02T20:42:00Z", "type": "x", "blob": "y" * 300},
    ]
    monkeypatch.setattr(ledger, "tail_events", lambda n: events)
    assert commands._handle_audit(["tail"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(
        "2026-09-02 20:40:10" in ln for ln in lines
    )  # the date is no longer cut off
    starts = {_col(lines, e) for e in ("key_rotated", "route_bypass_detected", " x ")}
    assert len({_col(lines, "key_rotated"), _col(lines, "route_bypass_detected")}) == 1
    assert (
        max(display_width(ln) for ln in lines) < 140
    )  # the long detail is cut, not wrapped
    assert starts


def test_budget_columns_line_up(monkeypatch, capsys):
    from hermes_ops_kit import commands
    from hermes_ops_kit.cost_governor import budget

    monkeypatch.setattr(
        budget,
        "evaluate_budget",
        lambda *a, **k: {
            "budget_status": "warn",
            "daily_spend_usd": 1.5,
            "daily_budget_usd": 5.0,
            "daily_percent": 30,
            "monthly_spend_usd": None,
            "monthly_budget_usd": 100.0,
            "monthly_percent": None,
            "enforcement_mode": "advisory",
            "actions": [],
        },
    )
    assert commands._handle_budget(["status"]) == 0
    lines = capsys.readouterr().out.splitlines()
    daily = next(ln for ln in lines if ln.lstrip().startswith("daily"))
    monthly = next(ln for ln in lines if ln.lstrip().startswith("monthly"))
    assert display_width(daily.rstrip()) == display_width(monthly.rstrip())
    assert "⚠" not in "".join(lines)


def test_doctor_rows_use_one_width_icons(capsys, monkeypatch):
    from hermes_ops_kit import commands

    monkeypatch.setattr(commands, "_load_hermes_env", lambda: {})
    commands._handle_doctor()
    out = capsys.readouterr().out
    assert "⚠" not in out
    checks = out.split("\nWARNINGS")[0]  # the summary list below has no label column
    rows = [ln for ln in checks.splitlines() if ln.startswith(("  ✅", "  🟡"))]
    assert rows
    # "  <icon> <label padded to 20> <detail>": the detail starts at the same index
    assert all(r[24] == " " and r[25] != " " for r in rows if len(r) > 25), rows
    assert all(
        display_width(r[:24]) == 25 for r in rows if len(r) > 25
    )  # icon is 2 cells


def test_usage_rows_align_online_and_offline_providers():
    from hermes_ops_kit import usage_metrics_v2 as um

    rows = [
        ["●", "GITHUB", "2428ms", "14 models", "PAID", "rec: gpt-5.4-mini", "v1"],
        ["○", "FIREWORKS", "?", "OFFLINE", "", "", "KEY not set " + "x" * 80],
        ["◐", "ANTHROPIC", "434ms", "OFFLINE", "", "", "err"],
    ]
    lines = um._aligned(rows, "<<>>")
    assert len({_col(lines, n) for n in ("GITHUB",)}) == 1
    assert _col(lines, "GITHUB") == _col(lines, "FIREWORKS") == _col(lines, "ANTHROPIC")
    # latency is right-aligned: the units end on the same cell
    ends = [
        display_width(ln[: ln.index(t) + len(t)])
        for ln, t in zip(lines, ("2428ms", "?", "434ms"))
    ]
    assert len(set(ends)) == 1
    assert um._aligned([]) == []


def test_route_rows_survive_a_route_longer_than_the_old_fixed_column():
    from hermes_ops_kit import usage_metrics_v2 as um

    entries = [
        {
            "role": "primary",
            "route": "a:b",
            "latency": 5,
            "cost": "free",
            "reason": "r1",
        },
        {
            "role": "aux_x",
            "route": "custom:freellm:@cf/stabilityai/stable-diffusion-xl-base-1.0",
            "latency": None,
            "cost": "paid",
            "reason": "r2",
        },
    ]
    lines = um._aligned(um._route_rows(entries), "<<>")
    assert _col(lines, "r1") == _col(lines, "r2")


def test_image_routes_status_column_aligns_despite_colour(monkeypatch, capsys):
    from hermes_ops_kit.image_routes import manager, router

    config = {
        "default_route": "quality",
        "policies": {},
        "routes": {
            "fast": {
                "provider": "gemini",
                "model": "m",
                "cost_class": "paid-low",
                "label": "l1",
            },
            "free-alt": {
                "provider": "custom:freellm",
                "model": "@cf/stabilityai/stable-diffusion-xl-base-1.0",
                "cost_class": "free",
                "label": "l2",
            },
        },
    }
    monkeypatch.setattr(manager, "_load_config", lambda: config)
    monkeypatch.setattr(
        router,
        "healthcheck",
        lambda: {
            "routes": {"fast": {"status": "ready"}, "free-alt": {"status": "no-key"}}
        },
    )
    manager.cmd_routes(SimpleNamespace())
    lines = capsys.readouterr().out.splitlines()
    assert _col(lines, "l1") == _col(lines, "l2")
    assert _col(lines, "READY") == _col(lines, "NO KEY")
