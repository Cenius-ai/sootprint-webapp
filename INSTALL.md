# Installing Sootprint

Sootprint has **no runtime dependencies** and no build step: `sootprint.py` is
the whole product. This file is the source of truth for getting it running,
whether you install the `sootprint` command or just run the file.

## Prerequisites

* **Python 3.9 or newer**, with `pip` available as `python3 -m pip`.
  Check with:

  ```bash
  python3 --version
  python3 -m pip --version
  ```

  That is the complete list. Sootprint needs no compiler, no system packages, no
  database server, no web server and no network access at run time.

## Package manager

Use **pip** with the committed `requirements.txt` at the project root. There is
no other package manager for this project (no npm/pipenv/poetry/conda), and the
runtime dependency list is deliberately empty — the only entry is the `pytest`
test runner.

## Setup, step by step

Run everything from the project root (the directory holding `sootprint.py`).

### 1. Install the dependencies and the command (one command)

```bash
bash install.sh
```

This upgrades pip/setuptools/wheel, installs `requirements.txt`, installs the
`sootprint` console command from `pyproject.toml`, verifies the tool imports and
runs `sootprint --version`. **It exits when it is done** — it never starts a
background process or a server. It is safe to re-run.

To do the same steps by hand:

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
python3 -c "import sootprint; assert callable(sootprint.main)"
```

### 2. Point it at a store (optional)

Sootprint writes one JSON file, `sweeps.json`, into `$SOOTPRINT_HOME`, and falls
back to `~/.sootprint/` when that variable is unset. Nothing to configure if the
default is fine:

```bash
export SOOTPRINT_HOME="$PWD/my-sootprint-log"   # optional
```

### 3. Load a demo log (optional)

There is no server and no database to seed, so "seeding" just means having some
sweeps to look at. Either use the bundled nine-sweep sample:

```bash
SOOTPRINT_HOME="$PWD/examples" sootprint list
SOOTPRINT_HOME="$PWD/examples" sootprint chart
SOOTPRINT_HOME="$PWD/examples" sootprint risk
```

or run the recorded end-to-end demo, which builds a throwaway log in a temp
directory and leaves your own log untouched:

```bash
bash demo.sh
```

### 4. Fill your own log

```bash
sootprint add --date 2024-10-12 --condition 5 --flue "main flue" --notes "pre-season sweep"
sootprint add --date 2025-01-19 --condition 3 --flue "main flue" --notes "creosote flakes on the damper"
```

### 5. Run it in dev

Sootprint is a CLI: there is no process to keep running and no port to bind.

```bash
sootprint --help          # the installed command
sootprint list            # or: python3 sootprint.py list  (no install needed)
```

Multi-command CLI note: running `sootprint` with no arguments prints the usage
summary and exits 0; the commands themselves are `add`, `list`, `chart` and
`risk`.

### 6. Run the tests

```bash
pytest              # 61 tests: add/list/chart/risk, error paths, heuristic units, 10k budget
```

## Where things live

| Path | What it is |
| --- | --- |
| `sootprint.py` | The entire product: one standard-library Python file |
| `tests/test_sootprint.py` | The committed test suite (pytest) |
| `examples/sweeps.json` | A ready-made nine-sweep sample log |
| `demo.sh` | Non-interactive end-to-end demo on a throwaway log |
| `$SOOTPRINT_HOME/sweeps.json` | Your log — defaults to `~/.sootprint/sweeps.json` |

## Uninstalling

```bash
python3 -m pip uninstall sootprint
```

Your log in `$SOOTPRINT_HOME/sweeps.json` (or `~/.sootprint/sweeps.json`) is a
plain file; deleting it is the whole cleanup.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `sootprint: command not found` | The console script is not on `PATH`; re-run `bash install.sh`, or call `python3 sootprint.py …` from the project root. |
| `error: the date must be ISO-8601 (YYYY-MM-DD)` | The `--date` value is not `YYYY-MM-DD`. Nothing was written. |
| `error: the flue condition must be a whole number from 1 to 5` | The `--condition` value is outside 1–5 or not a whole number. Nothing was written. |
| `error: could not read the sweep log at …` | The log file is truncated or not valid JSON. Sootprint refuses to touch it rather than overwrite your history: open the file, fix or move it, and run the command again. |
| No colour in the output | Colour only appears on a terminal and is disabled by `NO_COLOR` or `--no-color`. This is intentional. |
