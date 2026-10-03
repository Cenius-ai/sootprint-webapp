# Using Sootprint

Walkthroughs of the four commands that actually exist, with the output they
actually print. The log used below is the nine-sweep sample in
[`examples/sweeps.json`](../examples/sweeps.json); the first three sweeps are
used for the short examples so the output stays readable.

```bash
export SOOTPRINT_HOME="$PWD/examples"   # optional: use the bundled sample log
```

---

## `sootprint add` — log a sweep

Records one sweep visit. The date must be ISO-8601 (`YYYY-MM-DD`) and the flue
condition a whole number from 1 to 5; `--flue` and `--notes` are optional.

```console
$ sootprint add --date 2024-01-15 --condition 2 --flue "main flue" \
      --notes "heavy soot on the smoke shelf"
Logged sweep on 2024-01-15 (condition 2/5).
```

```console
$ sootprint add --date 2024-05-01 --condition 4
Logged sweep on 2024-05-01 (condition 4/5).
```

* The sweep is visible in `sootprint list` immediately afterwards.
* One line of confirmation goes to stdout and nothing else, so it is safe to
  call from a script or a cron entry.
* `--flue` defaults to `main flue`; `--notes` defaults to empty.
* Both free-text fields are single-line: runs of whitespace are collapsed to
  single spaces, so a log row can never be broken across lines.

### When the input is bad, nothing is written

The error goes to **stderr**, stdout stays empty, the exit status is **2**, and
`sweeps.json` is not created or modified.

```console
$ sootprint add --date 15/01/2024 --condition 4
sootprint: error: the date must be ISO-8601 (YYYY-MM-DD), for example 2024-05-01; got '15/01/2024'.
$ echo $?
2
```

```console
$ sootprint add --date 2024-05-01 --condition 9
sootprint: error: the flue condition must be a whole number from 1 to 5; got '9'.
$ echo $?
2
```

A date-shaped string that is not a real calendar day is refused too
(`2024-02-30` → *"is not a real calendar date"*), and `add` refuses to write
into a store it cannot read, so a damaged log is never overwritten.

---

## `sootprint list` — read the log back

Newest first, with date, condition `n/5`, flue and notes, and a trailing count.

```console
$ sootprint list
Sweep log - 3 sweep(s), newest first

Date        Condition  Flue       Notes
----------  ---------  ---------  -----------------------------
2024-04-20  4/5        rear flue  light ash only
2024-03-02  3/5        main flue  baffle scraped, flue sound
2024-01-15  2/5        main flue  heavy soot on the smoke shelf

3 sweeps logged
```

### Empty log

```console
$ sootprint list
No sweeps logged yet. Run `sootprint add --date YYYY-MM-DD --condition 1-5` to log your first sweep.
```

Exit status 0, and no store file is created by reading.

### Unreadable or corrupt log

A truncated or hand-mangled file is reported on stderr, naming the file, with a
non-zero exit and no Python traceback:

```console
$ sootprint list
sootprint: error: could not read the sweep log at /home/you/.sootprint/sweeps.json: the file is not valid JSON (line 1, column 57).
$ echo $?
1
```

`chart` and `risk` behave the same way, and print nothing at all on stdout — so
a pipeline never receives a half-drawn chart or a phantom score.

---

## `sootprint chart` — the flue condition trend

One dated row per sweep, oldest first, with a bar whose length is proportional to
the condition (four `#` columns per point). The legend names the whole 1–5 scale.

```console
$ sootprint chart
Flue condition chart - 3 sweep(s), oldest first
Legend: 1 = heavily fouled, 2 = fouled, 3 = fair, 4 = good, 5 = clean
Each bar is one # per condition point (4 columns per point), longest = cleanest.

2024-01-15  2/5  main flue  ########
2024-03-02  3/5  main flue  ############
2024-04-20  4/5  rear flue  ################

Average condition: 3.0/5 across 3 sweep(s).
```

Bars are plain ASCII, so the chart survives being piped into a file or a pager:

```bash
sootprint chart > flue-history.txt
```

An empty log invites the first `add` instead of drawing an empty axis:

```console
$ sootprint chart
No sweeps logged yet. Run `sootprint add` to log your first sweep.
Example: sootprint add --date 2024-05-01 --condition 4
```

---

## `sootprint risk` — the creosote score

A whole number from 0 to 100, its band, and the factors that produced it.

```console
$ sootprint risk
Creosote risk score

Score: 62 / 100    Band: High

What drove the score:
  - Days since last sweep: 896 day(s) since 2024-04-20
    contributes 45 of 45 risk points
  - Average logged condition: 3.0/5 across 3 sweep(s) (fair)
    contributes 18 of 35 risk points
  - Condition trend: improving - conditions are rising about 0.6 point(s) per 30 days
    contributes 0 of 20 risk points

Sweeps logged: 3, most recent 2024-04-20 (896 day(s) ago).
This is a heuristic built from your own log - not an inspection, and not a code-compliance verdict.
```

* `Days since last sweep` (up to 45 points) grows to its maximum at 365 days.
* `Average logged condition` (up to 35 points) is full at an average of 1/5 and
  zero at 5/5.
* `Condition trend` (up to 20 points) uses the least-squares slope of condition
  against time: steady scores 0, and losing a full condition point every 30 days
  scores the maximum 20.
* Bands: **Low** 0–24, **Moderate** 25–49, **High** 50–74, **Severe** 75–100.
* The score is deterministic — the same log always produces the same number — and
  the factors are printed so the number is explainable rather than a black box.

An empty log prints the empty-state line and exits 0 without printing a score,
because a score needs at least one sweep.

---

## `sootprint --help` and unknown commands

```console
$ sootprint --help
usage: sootprint <command> [options]

Sootprint keeps a dated chimney sweep log in one local JSON file, draws the flue condition trend and scores creosote risk.

positional arguments:
  <command>
    add       log one sweep visit with its ISO-8601 date and 1-5 flue
              condition
    list      show every logged sweep, newest first
    chart     draw dated ASCII bars of the logged flue condition, oldest first
    risk      compute the 0-100 creosote risk score, its band and its driving
              factors
...
```

Running `sootprint` with no arguments prints the usage summary and exits 0. An
unknown command names the command, prints the usage summary to stderr and exits
non-zero:

```console
$ sootprint frobnicate
sootprint: error: Unknown command 'frobnicate'.

sootprint - chimney sweep log, flue condition chart and creosote risk score
...
$ echo $?
2
```

---

## Scripting notes

* Results on stdout, diagnostics on stderr, so `sootprint list | grep 2024` works.
* Exit status 0 = success, 1 = the log could not be read or written, 2 = bad
  input or usage — enough to branch on in a shell script.
* Colour is only ever added on a terminal, and is disabled by `NO_COLOR` or
  `--no-color`, so captured output is plain text.
* Every command is non-interactive: there is nothing to answer, ever.
