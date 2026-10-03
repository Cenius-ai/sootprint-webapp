#!/usr/bin/env python3
"""Sootprint - a chimney sweep log, a flue condition chart and a creosote risk score.

Everything lives in one human-readable JSON file under $SOOTPRINT_HOME
(default ~/.sootprint/sweeps.json). Standard library only, no network,
no accounts. The creosote score is a heuristic built from your own log;
it is not an inspection and not a code-compliance verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

# --- committed theme tokens -------------------------------------------------
# CLI surface: monospace, no chrome, one accent for primary status, and no
# signal that is carried by colour alone. The accent is the project accent
# #008f91 (oklch(0.58 0.12 196)); xterm-256 index 37 is its nearest terminal
# approximation. Colour is only ever added on top of text that already reads
# correctly without it, and only on a real TTY unless NO_COLOR is set.
ACCENT_HEX = "#008f91"
ACCENT_ANSI = "\x1b[38;5;37m"
ANSI_RESET = "\x1b[0m"
# --- end theme tokens -------------------------------------------------------

PROG = "sootprint"
STORE_ENV_VAR = "SOOTPRINT_HOME"
STORE_DIRNAME = ".sootprint"
STORE_FILENAME = "sweeps.json"
FORMAT_VERSION = 1
DEFAULT_FLUE = "main flue"
MIN_CONDITION = 1
MAX_CONDITION = 5
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

CONDITION_WORDS = {
    1: "heavily fouled",
    2: "fouled",
    3: "fair",
    4: "good",
    5: "clean",
}

ADD_EXAMPLE = "sootprint add --date YYYY-MM-DD --condition 1-5"

EMPTY_LOG_LONG = f"No sweeps logged yet. Run `{ADD_EXAMPLE}` to log your first sweep."
EMPTY_LOG_SHORT = "No sweeps logged yet. Run `sootprint add` to log your first sweep."

CONDITION_HELP = "flue condition on the 1-5 scale (1 = heavily fouled, 5 = clean)"

# Creosote heuristic weights. They always add up to 100 so the score is a
# plain percentage of the worst case the log can describe.
WEIGHT_RECENCY = 45.0
WEIGHT_CONDITION = 35.0
WEIGHT_TREND = 20.0
RECENCY_HORIZON_DAYS = 365.0
TREND_FULL_RISK_PER_30_DAYS = (
    1.0  # losing a whole condition point a month = full trend risk
)

BANDS = ((25, "Low"), (50, "Moderate"), (75, "High"))


class SootprintError(Exception):
    """Base class for every error Sootprint reports to the user."""

    exit_code = EXIT_ERROR


class UsageError(SootprintError):
    """Bad arguments or bad input: nothing is written."""

    exit_code = EXIT_USAGE


class StoreError(SootprintError):
    """The sweep log could not be read or written."""

    exit_code = EXIT_ERROR


@dataclass
class Output:
    """stdout/stderr writers that know whether colour is welcome."""

    color: bool = False

    def line(self, text: str = "") -> None:
        print(text)

    def error(self, text: str) -> None:
        print(f"{PROG}: error: {text}", file=sys.stderr)

    def accent(self, text: str) -> str:
        if not self.color:
            return text
        return f"{ACCENT_ANSI}{text}{ANSI_RESET}"


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

# The sweep log is untrusted input, so it is parsed strictly as data with the
# standard library's JSON parser - never through an object-graph unserializer that
# can construct arbitrary objects or run code. Every decoded record is then
# validated field by field by _coerce_entry before it becomes a SweepEntry.
_JSON_PARSER = json.JSONDecoder()


def _parse_store_document(text: str, path: Path) -> object:
    """Decode the single JSON document held in text, or fail with a plain message."""
    try:
        return _JSON_PARSER.decode(text)
    except json.JSONDecodeError as exc:
        raise StoreError(
            f"could not read the sweep log at {path}: the file is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno})."
        ) from exc


@dataclass(frozen=True)
class SweepEntry:
    """One logged sweep visit."""

    date: str  # ISO-8601 calendar date, YYYY-MM-DD
    condition: int  # 1 (heavily fouled) .. 5 (clean)
    flue: str = DEFAULT_FLUE
    notes: str = ""
    logged_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date,
            "condition": self.condition,
            "flue": self.flue,
            "notes": self.notes,
            "logged_at": self.logged_at,
        }


def as_date(entry: SweepEntry) -> date:
    """Return the entry's calendar date (entries are validated when loaded)."""
    return date.fromisoformat(entry.date)


def _coerce_entry(raw: object, index: int, path: Path) -> SweepEntry:
    where = f"{path} (entry {index + 1})"
    if not isinstance(raw, dict):
        raise StoreError(
            f"could not read the sweep log at {where}: the entry is not an object."
        )
    raw_date = raw.get("date")
    if not isinstance(raw_date, str) or not ISO_DATE_RE.match(raw_date):
        raise StoreError(
            f"could not read the sweep log at {where}: the date is not ISO-8601 (YYYY-MM-DD)."
        )
    try:
        date.fromisoformat(raw_date)
    except ValueError as exc:
        raise StoreError(
            f"could not read the sweep log at {where}: {raw_date} is not a real calendar date."
        ) from exc
    raw_condition = raw.get("condition")
    if isinstance(raw_condition, bool) or not isinstance(raw_condition, int):
        raise StoreError(
            f"could not read the sweep log at {where}: the condition is not a whole number."
        )
    if not MIN_CONDITION <= raw_condition <= MAX_CONDITION:
        raise StoreError(
            f"could not read the sweep log at {where}: the condition must be a whole number "
            f"from {MIN_CONDITION} to {MAX_CONDITION}."
        )
    flue = raw.get("flue", DEFAULT_FLUE)
    notes = raw.get("notes", "")
    logged_at = raw.get("logged_at", "")
    for value, field in ((flue, "flue"), (notes, "notes"), (logged_at, "logged_at")):
        if not isinstance(value, str):
            raise StoreError(
                f"could not read the sweep log at {where}: the {field} value is not text."
            )
    return SweepEntry(
        date=raw_date,
        condition=raw_condition,
        flue=flue or DEFAULT_FLUE,
        notes=notes,
        logged_at=logged_at,
    )


class SweepStore:
    """The single JSON file that holds every logged sweep."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def default(cls) -> SweepStore:
        home = os.environ.get(STORE_ENV_VAR, "").strip()
        base = Path(home).expanduser() if home else Path.home() / STORE_DIRNAME
        return cls(base / STORE_FILENAME)

    def read_entries(self) -> list[SweepEntry]:
        """Read the log. A missing file is an empty log, not an error."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except UnicodeDecodeError as exc:
            raise StoreError(
                f"could not read the sweep log at {self.path}: the file is not valid UTF-8 text."
            ) from exc
        except OSError as exc:
            raise StoreError(
                f"could not read the sweep log at {self.path}: {exc.strerror or exc}."
            ) from exc
        if not text.strip():
            return []
        payload = _parse_store_document(text, self.path)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict) and isinstance(payload.get("sweeps"), list):
            records = payload["sweeps"]
        else:
            raise StoreError(
                f"could not read the sweep log at {self.path}: expected a JSON object with a "
                '"sweeps" list.'
            )
        return [
            _coerce_entry(record, index, self.path)
            for index, record in enumerate(records)
        ]

    def append(self, entry: SweepEntry) -> None:
        entries = self.read_entries()
        entries.append(entry)
        self.write(entries)

    def write(self, entries: Sequence[SweepEntry]) -> None:
        """Replace the log atomically so an interrupted write cannot truncate it."""
        payload = {
            "format_version": FORMAT_VERSION,
            "sweeps": [entry.to_dict() for entry in entries],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp_name = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".sweeps-", suffix=".tmp"
            )
        except OSError as exc:
            raise StoreError(
                f"could not write the sweep log at {self.path}: {exc.strerror or exc}."
            ) from exc
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.path)
        except OSError as exc:
            with suppress(OSError):
                tmp_path.unlink()
            raise StoreError(
                f"could not write the sweep log at {self.path}: {exc.strerror or exc}."
            ) from exc


# --------------------------------------------------------------------------- #
# Ordering and shared helpers
# --------------------------------------------------------------------------- #


def sort_newest_first(entries: Sequence[SweepEntry]) -> list[SweepEntry]:
    return sorted(
        entries, key=lambda entry: (entry.date, entry.logged_at), reverse=True
    )


def sort_oldest_first(entries: Sequence[SweepEntry]) -> list[SweepEntry]:
    return sorted(entries, key=lambda entry: (entry.date, entry.logged_at))


def _scale(value: float, low: float, high: float) -> float:
    """Map value onto 0..1 across [low, high], clamped."""
    if high <= low:
        return 0.0
    fraction = (value - low) / (high - low)
    return max(0.0, min(1.0, fraction))


def _condition_column(condition: int) -> str:
    return f"{condition}/{MAX_CONDITION}"


def _counted(count: float, singular: str) -> str:
    """'1 sweep' / '3 sweeps' - counts read like prose, not like a status code."""
    noun = singular if abs(count - 1) < 0.05 else f"{singular}s"
    shown = f"{count:.0f}" if float(count).is_integer() else f"{count:.1f}"
    return f"{shown} {noun}"


# --------------------------------------------------------------------------- #
# creosote risk heuristic
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RiskFactor:
    label: str
    detail: str
    points: float
    maximum: float


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    band: str
    factors: tuple[RiskFactor, ...]
    days_since_last: int
    last_date: str
    sweep_count: int


def _trend_slope_per_30_days(entries: Sequence[SweepEntry]) -> float:
    """Least-squares slope of condition against time, in condition points per 30 days.

    Positive means conditions are improving over time, negative means fouling.
    """
    if len(entries) < 2:
        return 0.0
    ordered = sort_oldest_first(entries)
    origin = as_date(ordered[0])
    points = [
        ((as_date(entry) - origin).days, float(entry.condition)) for entry in ordered
    ]
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    variance = sum((x - mean_x) ** 2 for x, _ in points)
    if variance == 0:
        return 0.0
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return covariance / variance * 30.0


def assess_risk(
    entries: Sequence[SweepEntry], today: date | None = None
) -> RiskAssessment:
    """Score the creosote risk of a logged flue from 0 (low) to 100 (severe)."""
    today = today or date.today()
    ordered = sort_oldest_first(entries)
    most_recent = ordered[-1]
    days_since_last = max(0, (today - as_date(most_recent)).days)
    average_condition = sum(entry.condition for entry in ordered) / len(ordered)
    slope = _trend_slope_per_30_days(ordered)

    fouling = MAX_CONDITION - average_condition
    recency_points = _scale(days_since_last, 0.0, RECENCY_HORIZON_DAYS) * WEIGHT_RECENCY
    condition_points = (
        _scale(fouling, 0.0, float(MAX_CONDITION - MIN_CONDITION)) * WEIGHT_CONDITION
    )
    trend_points = _scale(-slope, 0.0, TREND_FULL_RISK_PER_30_DAYS) * WEIGHT_TREND
    score = round(min(100.0, recency_points + condition_points + trend_points))

    band = "Severe"
    for threshold, name in BANDS:
        if score < threshold:
            band = name
            break

    if abs(slope) < 0.05:
        trend_detail = "steady - conditions are not drifting either way"
    elif slope < 0:
        trend_detail = f"worsening - conditions are falling about {_counted(abs(slope), 'point')} per 30 days"
    else:
        trend_detail = f"improving - conditions are rising about {_counted(slope, 'point')} per 30 days"

    factors = (
        RiskFactor(
            label="Days since last sweep",
            detail=(
                f"{_counted(days_since_last, 'day')} since {most_recent.date}"
                + (" (today or later)" if days_since_last == 0 else "")
            ),
            points=recency_points,
            maximum=WEIGHT_RECENCY,
        ),
        RiskFactor(
            label="Average logged condition",
            detail=(
                f"{average_condition:.1f}/5 across {_counted(len(ordered), 'sweep')}"
                f" ({CONDITION_WORDS[round(average_condition)]})"
            ),
            points=condition_points,
            maximum=WEIGHT_CONDITION,
        ),
        RiskFactor(
            label="Condition trend",
            detail=trend_detail,
            points=trend_points,
            maximum=WEIGHT_TREND,
        ),
    )
    return RiskAssessment(
        score=score,
        band=band,
        factors=factors,
        days_since_last=days_since_last,
        last_date=most_recent.date,
        sweep_count=len(ordered),
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _print_empty_log(out: Output, *, long_form: bool) -> None:
    if long_form:
        out.line(EMPTY_LOG_LONG)
    else:
        out.line(EMPTY_LOG_SHORT)
        out.line("Example: sootprint add --date 2024-05-01 --condition 4")


def cmd_add(args: argparse.Namespace, out: Output) -> int:
    entry_date = _parse_iso_date(args.date)
    condition = _parse_condition(args.condition)
    store = SweepStore.default()
    entry = SweepEntry(
        date=entry_date,
        condition=condition,
        flue=args.flue or DEFAULT_FLUE,
        notes=args.notes,
        logged_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    store.append(entry)
    out.line(
        f"Logged sweep on {entry.date} (condition {entry.condition}/{MAX_CONDITION})."
    )
    return EXIT_OK


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    header_line = "  ".join(
        header.ljust(widths[index]) for index, header in enumerate(headers)
    )
    rule = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        for row in rows
    ]
    return [header_line.rstrip(), rule, *body]


def cmd_list(args: argparse.Namespace, out: Output) -> int:
    store = SweepStore.default()
    entries = store.read_entries()
    if not entries:
        _print_empty_log(out, long_form=True)
        return EXIT_OK
    ordered = sort_newest_first(entries)
    rows = [
        [
            entry.date,
            _condition_column(entry.condition),
            entry.flue,
            entry.notes or "(no notes)",
        ]
        for entry in ordered
    ]
    out.line(f"Sweep log - {_counted(len(ordered), 'sweep')}, newest first")
    out.line("")
    for line in _render_table(["Date", "Condition", "Flue", "Notes"], rows):
        out.line(line)
    out.line("")
    out.line(f"{_counted(len(ordered), 'sweep')} logged")
    return EXIT_OK


def cmd_chart(args: argparse.Namespace, out: Output) -> int:
    store = SweepStore.default()
    entries = store.read_entries()
    if not entries:
        _print_empty_log(out, long_form=False)
        return EXIT_OK
    ordered = sort_oldest_first(entries)
    flue_width = max(len(entry.flue) for entry in ordered)
    out.line(f"Flue condition chart - {_counted(len(ordered), 'sweep')}, oldest first")
    out.line(
        "Legend: "
        + ", ".join(
            f"{value} = {CONDITION_WORDS[value]}"
            for value in range(MIN_CONDITION, MAX_CONDITION + 1)
        )
    )
    out.line(
        "Each bar is one # per condition point (4 columns per point), longest = cleanest."
    )
    out.line("")
    for entry in ordered:
        bar = "#" * (entry.condition * 4)
        out.line(
            f"{entry.date}  {_condition_column(entry.condition):<3}  "
            f"{entry.flue.ljust(flue_width)}  {bar}"
        )
    average = sum(entry.condition for entry in ordered) / len(ordered)
    out.line("")
    out.line(
        f"Average condition: {average:.1f}/5 across {_counted(len(ordered), 'sweep')}."
    )
    return EXIT_OK


def cmd_risk(args: argparse.Namespace, out: Output) -> int:
    store = SweepStore.default()
    entries = store.read_entries()
    if not entries:
        _print_empty_log(out, long_form=False)
        return EXIT_OK
    assessment = assess_risk(entries)
    out.line("Creosote risk score")
    out.line("")
    out.line(out.accent(f"Score: {assessment.score} / 100    Band: {assessment.band}"))
    out.line("")
    out.line("What drove the score:")
    for factor in assessment.factors:
        out.line(f"  - {factor.label}: {factor.detail}")
        out.line(
            f"    contributes {round(factor.points)} of {round(factor.maximum)} risk points"
        )
    out.line("")
    out.line(
        f"Sweeps logged: {assessment.sweep_count}, most recent {assessment.last_date} "
        f"({_counted(assessment.days_since_last, 'day')} ago)."
    )
    out.line(
        "This is a heuristic built from your own log - not an inspection, and not a "
        "code-compliance verdict."
    )
    return EXIT_OK


COMMANDS = {
    "add": cmd_add,
    "list": cmd_list,
    "chart": cmd_chart,
    "risk": cmd_risk,
}

COMMAND_HELP = (
    ("add", "log one sweep visit with its ISO-8601 date and 1-5 flue condition"),
    ("list", "show every logged sweep, newest first"),
    ("chart", "draw dated ASCII bars of the logged flue condition, oldest first"),
    ("risk", "compute the 0-100 creosote risk score, its band and its driving factors"),
)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #

USAGE = f"""{PROG} - chimney sweep log, flue condition chart and creosote risk score

Usage:
  {PROG} <command> [options]
  {PROG} --help

Commands:
  add     Log one sweep visit: {PROG} add --date 2024-05-01 --condition 4 [--flue NAME] [--notes TEXT]
  list    Show every logged sweep, newest first
  chart   Draw dated ASCII bars of the logged flue condition, oldest first
  risk    Compute the 0-100 creosote risk score, its band and its driving factors

Where the data lives:
  One UTF-8 JSON file at $SOOTPRINT_HOME/sweeps.json, defaulting to
  ~/.sootprint/sweeps.json. Written atomically; open or copy it any time.

Examples:
  {PROG} add --date 2024-05-01 --condition 4 --flue "main flue" --notes "annual sweep"
  {PROG} list
  {PROG} chart
  {PROG} risk
"""

EPILOG = """\
Where the data lives:
  One UTF-8 JSON file at $SOOTPRINT_HOME/sweeps.json, defaulting to
  ~/.sootprint/sweeps.json. Written atomically; open, copy or hand-edit it
  at any time. Set SOOTPRINT_HOME to keep the log somewhere else.

Examples:
  sootprint add --date 2024-05-01 --condition 4 --flue "main flue" --notes "annual sweep"
  sootprint list
  sootprint chart
  sootprint risk

Condition scale:
  1 = heavily fouled, 2 = fouled, 3 = fair, 4 = good, 5 = clean

Exit status:
  0 success, 1 the sweep log could not be read or written, 2 bad input or usage

Everything stays on this machine: one local JSON file, no accounts, no network.
The creosote score is a heuristic built from your own log, not an inspection.
"""


def _parse_iso_date(value: str) -> str:
    text = value.strip()
    if not ISO_DATE_RE.match(text):
        raise UsageError(
            f"the date must be ISO-8601 (YYYY-MM-DD), for example 2024-05-01; got {value!r}."
        )
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise UsageError(
            f"{text!r} is not a real calendar date. Use ISO-8601 (YYYY-MM-DD), "
            "for example 2024-05-01."
        ) from exc
    return text


def _parse_condition(value: str) -> int:
    text = str(value).strip()
    try:
        number = int(text)
    except ValueError as exc:
        raise UsageError(
            f"the flue condition must be a whole number from {MIN_CONDITION} to "
            f"{MAX_CONDITION}; got {value!r}."
        ) from exc
    if not MIN_CONDITION <= number <= MAX_CONDITION:
        raise UsageError(
            f"the flue condition must be a whole number from {MIN_CONDITION} to "
            f"{MAX_CONDITION}; got {value!r}."
        )
    return number


def _parse_single_line(value: str) -> str:
    return " ".join(value.split())


def _add_parser(parent: argparse._SubParsersAction) -> None:
    parser = parent.add_parser(
        "add",
        help="log one sweep visit with its ISO-8601 date and 1-5 flue condition",
        description=(
            "Log one sweep visit. The date must be ISO-8601 (YYYY-MM-DD) and the "
            "condition a whole number from 1 to 5.\n"
            "Nothing is written when the input is rejected."
        ),
        epilog=(
            "Examples:\n"
            "  sootprint add --date 2024-05-01 --condition 4\n"
            '  sootprint add --date 2024-05-01 --condition 2 --flue "rear flue" \\\n'
            '      --notes "light creosote flakes on the baffle"\n\n'
            "Condition scale:\n"
            "  1 = heavily fouled, 2 = fouled, 3 = fair, 4 = good, 5 = clean\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        required=True,
        metavar="YYYY-MM-DD",
        help="the ISO-8601 date the sweep happened",
    )
    parser.add_argument(
        "--condition",
        required=True,
        metavar="1-5",
        help=CONDITION_HELP,
    )
    parser.add_argument(
        "--flue",
        default=DEFAULT_FLUE,
        metavar="NAME",
        type=_parse_single_line,
        help=f"which flue was swept (default: {DEFAULT_FLUE})",
    )
    parser.add_argument(
        "--notes",
        default="",
        metavar="TEXT",
        type=_parse_single_line,
        help="free-text notes kept with the sweep (default: empty)",
    )
    parser.set_defaults(handler=cmd_add)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        usage=f"{PROG} <command> [options]",
        description=(
            "Sootprint keeps a dated chimney sweep log in one local JSON file, "
            "draws the flue condition trend and scores creosote risk."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} 1.0.0",
        help="show the version and exit",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="never use ANSI colour, even on a terminal",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for name, help_text in COMMAND_HELP:
        if name == "add":
            _add_parser(subparsers)
            continue
        subparser = subparsers.add_parser(
            name,
            help=help_text,
            description=help_text.capitalize() + ".",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        subparser.set_defaults(handler=COMMANDS[name])
    return parser


def _first_unknown_command(argv: Sequence[str]) -> str | None:
    for token in argv:
        if token == "--":
            continue
        if token.startswith("-"):
            continue
        return token if token not in COMMANDS else None
    return None


def _wants_color(argv: Sequence[str]) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if "--no-color" in argv:
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    out = Output(color=_wants_color(raw_args))

    unknown = _first_unknown_command(raw_args)
    if unknown is not None:
        print(f"{PROG}: error: Unknown command {unknown!r}.", file=sys.stderr)
        print(file=sys.stderr)
        print(USAGE, file=sys.stderr, end="")
        return EXIT_USAGE

    parser = build_parser()
    try:
        args = parser.parse_args(raw_args)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return EXIT_OK
        return code if isinstance(code, int) else EXIT_USAGE

    if getattr(args, "command", None) is None:
        print(USAGE, end="")
        return EXIT_OK

    try:
        return args.handler(args, out)
    except SootprintError as exc:
        out.error(str(exc))
        return exc.exit_code
    except OSError as exc:
        out.error(f"file system error: {exc.strerror or exc}.")
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        out.error("interrupted before the command finished.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
