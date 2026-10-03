"""Test suite for Sootprint (sootprint.py).

Every test drives the real CLI entry point (``sootprint.main``) against an
isolated store directory, so what is asserted is what a user gets.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

import sootprint

STORE_FILENAME = "sweeps.json"
EMPTY_LIST_MESSAGE = (
    "No sweeps logged yet. Run `sootprint add --date YYYY-MM-DD --condition 1-5` "
    "to log your first sweep."
)
EMPTY_CHART_MESSAGE = (
    "No sweeps logged yet. Run `sootprint add` to log your first sweep."
)


@pytest.fixture()
def store_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty, isolated $SOOTPRINT_HOME for one test."""
    home = tmp_path / "sootprint-home"
    monkeypatch.setenv(sootprint.STORE_ENV_VAR, str(home))
    monkeypatch.delenv("NO_COLOR", raising=False)
    return home


@pytest.fixture()
def run(capsys: pytest.CaptureFixture[str]):
    """Run the CLI in-process and return (exit_code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = sootprint.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def store_file(home: Path) -> Path:
    return home / STORE_FILENAME


def read_sweeps(home: Path) -> list[dict]:
    """Read the log back with the same data-only JSON parser the tool uses."""
    text = store_file(home).read_text(encoding="utf-8")
    return json.JSONDecoder().decode(text)["sweeps"]


def write_raw_store(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = store_file(home)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# F1 - sootprint add
# --------------------------------------------------------------------------- #


def test_add_logs_a_sweep_and_confirms_on_stdout(store_home: Path, run) -> None:
    code, out, err = run(
        "add",
        "--date",
        "2024-05-01",
        "--condition",
        "4",
        "--flue",
        "rear flue",
        "--notes",
        "light creosote flakes",
    )

    assert code == 0
    assert out == "Logged sweep on 2024-05-01 (condition 4/5).\n"
    assert err == ""
    assert store_file(store_home).is_file()
    sweeps = read_sweeps(store_home)
    assert len(sweeps) == 1
    assert sweeps[0]["date"] == "2024-05-01"
    assert sweeps[0]["condition"] == 4
    assert sweeps[0]["flue"] == "rear flue"
    assert sweeps[0]["notes"] == "light creosote flakes"
    assert sweeps[0]["logged_at"].startswith("20")


def test_add_uses_sensible_defaults_for_flue_and_notes(store_home: Path, run) -> None:
    code, out, _ = run("add", "--date", "2024-05-01", "--condition", "4")

    assert code == 0
    assert out == "Logged sweep on 2024-05-01 (condition 4/5).\n"
    entry = read_sweeps(store_home)[0]
    assert entry["flue"] == "main flue"
    assert entry["notes"] == ""


def test_store_directory_is_created_on_first_write(store_home: Path, run) -> None:
    assert not store_home.exists()

    run("add", "--date", "2024-05-01", "--condition", "4")

    assert store_home.is_dir()
    assert store_file(store_home).is_file()


@pytest.mark.parametrize(
    "bad_date", ["05/01/2024", "2024-5-1", "1 May 2024", "20240501", "yesterday"]
)
def test_add_rejects_dates_that_are_not_iso_8601(
    store_home: Path, run, bad_date: str
) -> None:
    code, out, err = run("add", "--date", bad_date, "--condition", "4")

    assert code != 0
    assert out == ""
    assert "ISO-8601" in err and "YYYY-MM-DD" in err
    assert "Traceback" not in err
    assert not store_file(store_home).exists()
    _, list_out, _ = run("list")
    assert EMPTY_LIST_MESSAGE in list_out


def test_add_rejects_a_date_that_is_not_a_real_calendar_day(
    store_home: Path, run
) -> None:
    code, out, err = run("add", "--date", "2024-02-30", "--condition", "4")

    assert code != 0
    assert out == ""
    assert "not a real calendar date" in err
    assert not store_file(store_home).exists()


@pytest.mark.parametrize("bad_condition", ["9", "0", "-2", "abc", "3.5"])
def test_add_rejects_conditions_outside_one_to_five(
    store_home: Path, run, bad_condition: str
) -> None:
    code, out, err = run("add", "--date", "2024-05-01", "--condition", bad_condition)

    assert code != 0
    assert out == ""
    assert "whole number from 1 to 5" in err
    assert "Traceback" not in err
    assert not store_file(store_home).exists()


def test_add_rejects_out_of_range_condition_listed_first_in_message(
    store_home: Path, run
) -> None:
    code, out, err = run("add", "--date", "2024-05-01", "--condition", "9")

    assert code != 0
    assert out == ""
    assert "9" in err
    _, list_out, _ = run("list")
    assert "No sweeps logged yet." in list_out


@pytest.mark.parametrize(
    "argv",
    [
        ["add"],
        ["add", "--date", "2024-05-01"],
        ["add", "--condition", "4"],
    ],
)
def test_add_requires_date_and_condition(
    store_home: Path, run, argv: list[str]
) -> None:
    code, out, err = run(*argv)

    assert code != 0
    assert out == ""
    assert err.strip() != ""
    assert not store_file(store_home).exists()


def test_add_appends_and_list_shows_newest_first(store_home: Path, run) -> None:
    run("add", "--date", "2024-05-01", "--condition", "4")
    code, out, err = run("add", "--date", "2024-06-02", "--condition", "2")

    assert code == 0
    assert "2024-06-02" in out and "2/5" in out
    assert err == ""

    _, list_out, _ = run("list")
    lines = [line for line in list_out.splitlines() if line.startswith("20")]
    assert len(lines) == 2
    assert lines[0].startswith("2024-06-02")
    assert lines[1].startswith("2024-05-01")


def test_add_normalises_flue_and_notes_to_a_single_line(store_home: Path, run) -> None:
    run(
        "add", "--date", "2024-05-01", "--condition", "4", "--notes", "ash   on\tbaffle"
    )

    entry = read_sweeps(store_home)[0]
    assert entry["notes"] == "ash on baffle"


# --------------------------------------------------------------------------- #
# F2 - sootprint list
# --------------------------------------------------------------------------- #


def test_list_on_empty_store_invites_the_first_add_without_creating_a_file(
    store_home: Path, run
) -> None:
    code, out, err = run("list")

    assert code == 0
    assert EMPTY_LIST_MESSAGE in out
    assert err == ""
    assert not store_home.exists()


def test_list_prints_date_condition_flue_notes_and_count(store_home: Path, run) -> None:
    run(
        "add",
        "--date",
        "2024-05-01",
        "--condition",
        "4",
        "--flue",
        "rear flue",
        "--notes",
        "light creosote flakes",
    )
    run("add", "--date", "2024-06-02", "--condition", "2")

    code, out, err = run("list")

    assert code == 0
    assert err == ""
    assert "2024-06-02" in out and "2/5" in out and "main flue" in out
    assert "2024-05-01" in out and "4/5" in out and "rear flue" in out
    assert "light creosote flakes" in out
    assert "2 sweeps logged" in out


def test_list_falls_back_to_the_home_default_when_env_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.delenv(sootprint.STORE_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(fake_home))

    run("add", "--date", "2024-05-01", "--condition", "4")
    _, out, _ = run("list")

    expected = fake_home / ".sootprint" / STORE_FILENAME
    assert expected.is_file()
    assert "2024-05-01" in out


def test_list_reports_a_corrupt_store_without_a_traceback(
    store_home: Path, run
) -> None:
    write_raw_store(
        store_home, '{"format_version": 1, "sweeps": [{"date": "2024-05-01", "condi'
    )

    code, out, err = run("list")

    assert code != 0
    assert out == ""
    assert "could not read the sweep log" in err
    assert str(store_file(store_home)) in err
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "broken",
    [
        '{"format_version": 1, "sweeps": "not a list"}',
        '["not", "an", "object"]',
        '{"format_version": 1, "sweeps": [{"date": "05/01/2024", "condition": 4}]}',
        '{"format_version": 1, "sweeps": [{"date": "2024-05-01", "condition": 9}]}',
        '{"format_version": 1, "sweeps": [{"date": "2024-05-01", "condition": "4"}]}',
        "",
    ],
)
def test_list_handles_odd_store_contents_gracefully(
    store_home: Path, run, broken: str
) -> None:
    write_raw_store(store_home, broken)

    code, out, err = run("list")

    assert "Traceback" not in err
    if broken == "":
        assert code == 0  # an empty file is an empty log
    else:
        assert code != 0
        assert out == ""
        assert "could not read the sweep log" in err


def test_a_corrupt_store_is_never_overwritten_by_add(store_home: Path, run) -> None:
    broken = '{"format_version": 1, "sweeps": [{"date": "2024-05-01", "condi'
    path = write_raw_store(store_home, broken)

    code, out, err = run("add", "--date", "2024-07-01", "--condition", "3")

    assert code != 0
    assert out == ""
    assert "could not read the sweep log" in err
    assert path.read_text(encoding="utf-8") == broken


# --------------------------------------------------------------------------- #
# F3 - sootprint chart
# --------------------------------------------------------------------------- #


def test_chart_draws_dated_bars_oldest_first_with_a_legend(
    store_home: Path, run
) -> None:
    run("add", "--date", "2024-04-20", "--condition", "4")
    run("add", "--date", "2024-01-15", "--condition", "2")
    run("add", "--date", "2024-03-02", "--condition", "3")

    code, out, err = run("chart")

    assert code == 0
    assert err == ""
    rows = [line for line in out.splitlines() if line.startswith("2024-")]
    assert [row[:10] for row in rows] == ["2024-01-15", "2024-03-02", "2024-04-20"]
    bar_lengths = [row.count("#") for row in rows]
    assert bar_lengths == [8, 12, 16], "bar length must be proportional to condition"
    assert "Legend:" in out
    assert all(
        word in out for word in ("heavily fouled", "fouled", "fair", "good", "clean")
    )


def test_chart_empty_state(store_home: Path, run) -> None:
    code, out, err = run("chart")

    assert code == 0
    assert EMPTY_CHART_MESSAGE in out
    assert "#" not in out
    assert err == ""
    assert not store_home.exists()


def test_chart_reports_a_corrupt_store_on_stderr_with_empty_stdout(
    store_home: Path, run
) -> None:
    write_raw_store(store_home, "{not json at all")

    code, out, err = run("chart")

    assert code != 0
    assert out == ""
    assert str(store_file(store_home)) in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------- #
# F4 - sootprint risk
# --------------------------------------------------------------------------- #


def test_risk_reports_score_band_and_factors(store_home: Path, run) -> None:
    run("add", "--date", "2024-01-01", "--condition", "5")
    run("add", "--date", "2024-04-01", "--condition", "2")

    code, out, err = run("risk")

    assert code == 0
    assert err == ""
    assert "Score:" in out and "/ 100" in out
    score = int(out.split("Score:")[1].split("/")[0].strip())
    assert 0 <= score <= 100
    assert any(band in out for band in ("Low", "Moderate", "High", "Severe"))
    assert "Days since last sweep" in out
    assert "Average logged condition" in out
    assert "Condition trend" in out
    assert "heuristic" in out  # the score is not presented as an inspection verdict


def test_risk_is_low_for_a_fresh_clean_flue(store_home: Path, run) -> None:
    today = date.today().isoformat()
    run("add", "--date", today, "--condition", "5")
    run(
        "add",
        "--date",
        (date.today() - timedelta(days=30)).isoformat(),
        "--condition",
        "5",
    )

    _, out, _ = run("risk")

    score = int(out.split("Score:")[1].split("/")[0].strip())
    assert score <= 24
    assert "Low" in out


def test_risk_is_severe_for_a_fouled_stale_flue(store_home: Path, run) -> None:
    run("add", "--date", "2020-01-01", "--condition", "1")
    run("add", "--date", "2020-06-01", "--condition", "1")

    _, out, _ = run("risk")

    score = int(out.split("Score:")[1].split("/")[0].strip())
    assert score >= 75
    assert "Severe" in out


def test_risk_empty_state_prints_no_score(store_home: Path, run) -> None:
    code, out, err = run("risk")

    assert code == 0
    assert EMPTY_CHART_MESSAGE in out
    assert "Score" not in out
    assert err == ""
    assert not store_home.exists()


def test_risk_reports_a_corrupt_store(store_home: Path, run) -> None:
    write_raw_store(store_home, '{"sweeps": [{"date": "2024-05-01"}]}')

    code, out, err = run("risk")

    assert code != 0
    assert out == ""
    assert "could not read the sweep log" in err


def test_risk_score_is_bounded_and_deterministic(store_home: Path, run) -> None:
    day = date(2024, 1, 1)
    for index in range(40):
        entry_day = day + timedelta(days=index * 9)
        run("add", "--date", entry_day.isoformat(), "--condition", str(index % 5 + 1))

    first = run("risk")[1]
    second = run("risk")[1]

    assert first == second, "the heuristic must be deterministic for a fixed log"
    score = int(first.split("Score:")[1].split("/")[0].strip())
    assert 0 <= score <= 100


# --------------------------------------------------------------------------- #
# F5 - store location and atomic writes
# --------------------------------------------------------------------------- #


def test_store_is_human_readable_json_at_the_configured_path(
    store_home: Path, run
) -> None:
    run("add", "--date", "2024-05-01", "--condition", "4")

    payload = json.JSONDecoder().decode(
        store_file(store_home).read_text(encoding="utf-8")
    )
    assert payload["format_version"] == 1
    assert isinstance(payload["sweeps"], list)
    assert store_file(store_home).read_text(encoding="utf-8").endswith("\n")


def test_writes_leave_no_temporary_files_behind(store_home: Path, run) -> None:
    run("add", "--date", "2024-05-01", "--condition", "4")
    run("add", "--date", "2024-05-02", "--condition", "3")

    leftovers = [p.name for p in store_home.iterdir() if p.name != STORE_FILENAME]
    assert leftovers == []


# --------------------------------------------------------------------------- #
# F6 - usage, help and unknown commands
# --------------------------------------------------------------------------- #


def test_help_lists_every_command_and_exits_zero(run) -> None:
    code, out, err = run("--help")

    assert code == 0
    assert err == ""
    for command in ("add", "list", "chart", "risk"):
        assert command in out
    assert "SOOTPRINT_HOME" in out


def test_bare_invocation_prints_usage_and_exits_zero(run) -> None:
    code, out, err = run()

    assert code == 0
    assert "Usage:" in out
    assert err == ""


@pytest.mark.parametrize("command", ["frobnicate", "Delete", "help-me"])
def test_unknown_command_names_it_and_shows_usage_on_stderr(run, command: str) -> None:
    code, out, err = run(command)

    assert code != 0
    assert out == ""
    assert "Unknown command" in err and command in err
    for known in ("add", "list", "chart", "risk"):
        assert known in err


def test_version_flag(run) -> None:
    code, out, err = run("--version")

    assert code == 0
    assert "sootprint" in out
    assert err == ""


def test_each_subcommand_has_help(run) -> None:
    for command in ("add", "list", "chart", "risk"):
        code, out, _err = run(command, "--help")
        assert code == 0, command
        assert out.strip() != "", command


def test_colour_is_disabled_when_stdout_is_not_a_tty(store_home: Path, run) -> None:
    run("add", "--date", "2020-01-01", "--condition", "1")

    _, out, _ = run("risk")

    assert "\x1b[" not in out


def test_no_color_env_var_is_honoured(
    store_home: Path, run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    run("add", "--date", "2020-01-01", "--condition", "1")

    _, out, _ = run("risk")

    assert "\x1b[" not in out


# --------------------------------------------------------------------------- #
# Non-functional: the 10,000-sweep budget
# --------------------------------------------------------------------------- #


def test_ten_thousand_sweeps_stay_fast(store_home: Path, run) -> None:
    sweeps = [
        {
            "date": (date(1999, 1, 1) + timedelta(days=index)).isoformat(),
            "condition": index % 5 + 1,
            "flue": "main flue",
            "notes": f"visit {index}",
            "logged_at": "2024-01-01T00:00:00+00:00",
        }
        for index in range(10_000)
    ]
    write_raw_store(store_home, json.dumps({"format_version": 1, "sweeps": sweeps}))

    for command in ("list", "chart", "risk"):
        started = time.perf_counter()
        code, out, err = run(command)
        elapsed = time.perf_counter() - started
        assert code == 0, (command, err)
        assert out.strip() != "", command
        assert elapsed < 1.0, f"{command} took {elapsed:.3f}s"


def test_the_tool_is_one_stdlib_only_file() -> None:
    source = Path(sootprint.__file__).read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/env python3")
    allowed = {
        "__future__",
        "argparse",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "json",
        "os",
        "pathlib",
        "re",
        "sys",
        "tempfile",
        "typing",
    }
    imported = set()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            imported.add(stripped.removeprefix("import ").split(".")[0].split(" ")[0])
        elif stripped.startswith("from "):
            imported.add(stripped.split()[1].split(".")[0])
    assert imported <= allowed, f"unexpected imports: {sorted(imported - allowed)}"


def test_list_says_one_sweep_for_a_single_entry(store_home: Path, run) -> None:
    run("add", "--date", "2024-05-01", "--condition", "4")

    _, out, _ = run("list")

    assert "1 sweep logged" in out
    assert "1 sweeps" not in out


def test_store_path_resolution_follows_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sootprint.STORE_ENV_VAR, str(tmp_path / "custom"))
    assert sootprint.SweepStore.default().path == tmp_path / "custom" / STORE_FILENAME

    monkeypatch.delenv(sootprint.STORE_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert (
        sootprint.SweepStore.default().path
        == tmp_path / "home" / ".sootprint" / STORE_FILENAME
    )


def test_the_file_runs_as_a_script_from_the_shell(store_home: Path) -> None:
    """The deliverable is the file itself, so prove `python3 sootprint.py` works."""
    import subprocess

    script = Path(sootprint.__file__)
    env = {**os.environ, sootprint.STORE_ENV_VAR: str(store_home)}

    empty = subprocess.run(
        [sys.executable, str(script), "list"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert empty.returncode == 0
    assert EMPTY_LIST_MESSAGE in empty.stdout

    logged = subprocess.run(
        [
            sys.executable,
            str(script),
            "add",
            "--date",
            "2024-05-01",
            "--condition",
            "4",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert logged.returncode == 0
    assert logged.stdout == "Logged sweep on 2024-05-01 (condition 4/5).\n"
    assert (store_home / STORE_FILENAME).is_file()

    bad = subprocess.run(
        [sys.executable, str(script), "add", "--date", "nope", "--condition", "4"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert bad.returncode != 0
    assert bad.stdout == ""
    assert "ISO-8601" in bad.stderr


# --------------------------------------------------------------------------- #
# Unit level: the heuristic and the committed design tokens
# --------------------------------------------------------------------------- #


def _entry(day: str, condition: int) -> sootprint.SweepEntry:
    return sootprint.SweepEntry(date=day, condition=condition)


def test_committed_theme_tokens_are_present() -> None:
    # The committed accent, in hex for non-CSS surfaces and as its terminal
    # approximation for the one coloured value in the output.
    assert sootprint.ACCENT_HEX == "#008f91"
    assert sootprint.ACCENT_ANSI == "\x1b[38;5;37m"


def test_assess_risk_can_be_evaluated_against_a_fixed_day() -> None:
    entries = [_entry("2024-01-01", 5), _entry("2024-04-01", 2)]

    fresh = sootprint.assess_risk(entries, today=date(2024, 4, 1))
    stale = sootprint.assess_risk(entries, today=date(2026, 4, 1))

    assert fresh.days_since_last == 0
    assert stale.days_since_last == 730
    assert fresh.score < stale.score


def test_band_thresholds_cover_the_whole_range() -> None:
    clean_today = [_entry(date.today().isoformat(), 5)]
    fouled_long_ago = [_entry("2015-01-01", 1), _entry("2015-06-01", 1)]

    low = sootprint.assess_risk(clean_today, today=date.today())
    severe = sootprint.assess_risk(fouled_long_ago, today=date.today())

    assert low.band == "Low" and low.score <= 24
    assert severe.band == "Severe" and severe.score >= 75


def test_a_future_date_never_produces_a_negative_gap() -> None:
    assessment = sootprint.assess_risk(
        [_entry("2999-01-01", 3)], today=date(2024, 1, 1)
    )

    assert assessment.days_since_last == 0
    assert 0 <= assessment.score <= 100


def test_factor_points_always_add_up_to_the_score() -> None:
    entries = [
        _entry("2024-01-01", 5),
        _entry("2024-04-01", 2),
        _entry("2024-07-01", 1),
    ]

    assessment = sootprint.assess_risk(entries, today=date(2024, 12, 31))

    assert sum(factor.maximum for factor in assessment.factors) == 100
    assert assessment.score == round(
        sum(factor.points for factor in assessment.factors)
    )
