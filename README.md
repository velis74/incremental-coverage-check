# Incremental coverage check

A GitHub Action that fails a pull request when **the lines it changes** are not covered
by tests.

Total coverage is a poor gate: a large, well-tested codebase absorbs untested additions
without the number moving, while a small one blocks every pull request until someone
backfills tests for code they never touched. This action grades only the diff. If your
change adds ten executable lines and seven of them are exercised by the test suite, the
change is at 70%, no matter what the rest of the repository looks like.

It reads five coverage formats and merges them, so a Python backend and a TypeScript
frontend are graded together in a single report.

```
  INCREMENTAL COVERAGE  ·  31.2%
  ──────────────────────────────────────────────────────────────────────
  main (f65e05f) → feature/discounts (c875d0a)
  coverage: backend/coverage.json (coverage.py, 1 file), vue/coverage/lcov.info (lcov, 1 file)

     FILE                            COV  LINES  UNCOVERED
  ──────────────────────────────────────────────────────────────────────
  ✗  backend/orders/reports.py       0%    0/6  1-6  (no coverage data)
  !  backend/orders/services.py     50%    3/6  7, 11-12
  !  vue/src/components/table.ts    50%    2/4  5-6
  ──────────────────────────────────────────────────────────────────────
     TOTAL                        31.2%   5/16  ████████░░░░░░░░░░░░░░░░

  2 files not graded (1 ignored by pattern, 1 not a source file)

  ✗ FAIL   5 of 16 changed lines covered (31.2%), threshold 80%
```

The same report is written to the job summary and posted as a single, self-updating
comment — on the pull request, or on the pushed commit when there is no pull request —
with every uncovered range linked to the exact lines. Uncovered lines are additionally
annotated in the **Files changed** tab.

---

## Contents

- [Quick start](#quick-start)
- [A dual-environment project](#a-dual-environment-project)
- [Supported coverage formats](#supported-coverage-formats)
- [Path matching](#path-matching)
- [How the score is calculated](#how-the-score-is-calculated)
- [Which files are graded](#which-files-are-graded)
- [Inputs](#inputs)
- [Outputs](#outputs)
- [Reporting](#reporting)
- [Choosing the base revision](#choosing-the-base-revision)
- [Running it locally](#running-it-locally)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Quick start

```yaml
name: Coverage

on: pull_request

permissions:
  contents: read
  pull-requests: write        # only needed for the pull request comment
                              # on `push`, use `contents: write` for commit comments

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0      # the check needs history to find the merge base

      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - name: Test
        run: |
          coverage run -m pytest
          coverage json

      - name: Incremental coverage check
        uses: velis74/incremental-coverage-check@v3
        with:
          coverage-files: coverage.json
          threshold: "80"
```

The action needs no `pip install` of its own — it runs on the standard library alone.

## A dual-environment project

Django on the back, Vue on the front, one report:

```yaml
jobs:
  coverage:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      # ---- Python ----------------------------------------------------------
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Django tests
        run: |
          coverage run manage.py test
          coverage json                       # -> coverage.json

      # ---- TypeScript ------------------------------------------------------
      - uses: actions/setup-node@v7
        with:
          node-version: "22"
      - run: npm ci
        working-directory: vue
      - name: Vitest
        run: npm run test -- --coverage       # -> vue/coverage/clover.xml
        working-directory: vue

      # ---- One report over both --------------------------------------------
      - uses: velis74/incremental-coverage-check@v3
        with:
          coverage-files: |
            coverage.json
            vue/coverage/clover.xml
          threshold: "80"
```

Paths inside the coverage reports do not have to match the repository layout. Vitest run
inside `vue/` reports `src/components/table.ts`; istanbul reports absolute paths from the
build machine. Both are matched back onto `vue/src/components/table.ts`. See
[Path matching](#path-matching) for how, and for what happens when a path is genuinely
ambiguous.

## Supported coverage formats

The format of each file is detected from its content, so any mix can be listed together.

| Format | Produced by | Typical file |
| :-- | :-- | :-- |
| coverage.py JSON | `coverage json` (pytest, pytest-cov, Django) | `coverage.json` |
| Cobertura XML | `coverage xml`, `pytest --cov-report=xml` | `coverage.xml` |
| istanbul JSON | vitest / jest `json` reporter | `coverage/coverage-final.json` |
| Clover XML | vitest default reporters, jest `clover` | `coverage/clover.xml` |
| LCOV | vitest / jest `lcov` reporter | `coverage/lcov.info` |

Glob patterns are allowed, which is handy in a monorepo:

```yaml
coverage-files: |
  coverage.json
  packages/*/coverage/lcov.info
```

A pattern that matches nothing is an error, not a silent pass — otherwise a typo would
quietly grade every file as untested.

## Path matching

Coverage tools spell paths relative to wherever they ran, which on its own is ambiguous:
`src/index.ts` from `packages/a/coverage/lcov.info` and `src/index.ts` from
`packages/b/coverage/lcov.info` are different files with the same name. Guessing between
them can credit one package's tests to the other package's untested code, so the action
resolves paths against the working tree instead — a candidate only counts if that file
actually exists in the checkout.

Candidates are tried in two tiers:

1. **Anchored** — the workspace prefix stripped off an absolute path, a Cobertura
   `<source>` root joined on, or the directory the report itself sits in and each of its
   parents. A report at `packages/a/coverage/lcov.info` naming `src/index.ts` therefore
   resolves to `packages/a/src/index.ts`.
2. **Trimmed** — leading segments dropped one at a time, longest match first. This is what
   catches an istanbul report built in a container under `/build/app/vue/src/table.ts`.

If the first tier that produces a hit produces more than one, the answer is ambiguous. The
entry is then **discarded rather than guessed**: the file ends up with no coverage data,
counts as uncovered, and the report explains which candidates collided. The check fails
closed, which for a gate is the only safe direction.

The usual cause of an ambiguity warning is a coverage tool configured with several source
roots that contain identically named files (`source = api, worker` in `.coveragerc`, with
both packages holding a `handlers.py`). Running the tool from the repository root, or
producing one report per root, resolves it.

## How the score is calculated

1. **Take the diff.** `head` is compared against the point at which it forked from
   `base` (the merge base), so commits that landed on the base branch afterwards are not
   attributed to this change.
2. **Keep the added and modified lines**, numbered against the new revision. Deleted
   lines and context are irrelevant to "is the new code tested".
3. **Keep the executable ones.** Comments, blank lines and anything else the coverage
   tool does not consider executable are dropped. A pull request that only adds comments
   scores 100%, not 0%.
4. **Intersect with the misses.** Every remaining line that the coverage report marks as
   not executed is counted against you.

```
coverage = covered changed executable lines / all changed executable lines
```

Files that appear in the diff but in **no** coverage report count as fully uncovered.
This is deliberate: a brand new module nobody wrote a test for is exactly what this
check exists to catch, and skipping it would be the simplest way to sneak untested code
past the gate. Such a file is marked 🚫 *no coverage data* in the report.

A change with nothing gradable in it — only documentation, only migrations, only
comments — passes.

## Which files are graded

Two gates, in order.

**Source extensions (allow-list).** Only files that could ever be instrumented are
graded. Default: `.py .pyi .js .jsx .mjs .cjs .ts .tsx .mts .cts .vue .svelte`. Replace
the list with `source-extensions`.

**Ignore patterns (deny-list).** Real source that should not be graded. Defaults:

```
test_*.py  *_test.py  tests.py  conftest.py  test/*  tests/*  __tests__/*  __mocks__/*
*.spec.*   *.test.*
migrations/*  settings.py  settings/*  manage.py  setup.py  asgi.py  wsgi.py
*.d.ts  *.min.js
.*  .github/*  *.config.js  *.config.ts  vite.config.*  vitest.config.*
```

A pattern without a slash matches the file name anywhere in the tree; a pattern with a
slash matches the path at any directory level, so `migrations/*` covers
`backend/apps/orders/migrations/0042.py`.

Use `extra-ignore-patterns` to add to this list, or `ignore-patterns` to replace it
outright:

```yaml
with:
  extra-ignore-patterns: |
    backend/legacy/*
    vue/src/generated/*
```

Every skipped file is listed with its reason in a collapsed section of the report — the
check never quietly drops something.

## Inputs

| Input | Default | Description |
| :-- | :-- | :-- |
| `coverage-files` | — | Coverage reports to read, newline- or comma-separated. Globs allowed. |
| `threshold` | `70` | Minimum percentage of changed executable lines that must be covered. |
| `base-ref` | PR base branch, or `github.event.before` on a push | Revision to compare against. |
| `head-ref` | `HEAD` | Revision under test. |
| `working-directory` | `github.workspace` | Repository checkout to inspect. |
| `ignore-patterns` | see above | Replace the built-in ignore globs. |
| `extra-ignore-patterns` | — | Additional ignore globs, kept alongside the built-in ones. |
| `source-extensions` | see above | Replace the list of extensions treated as source code. |
| `threshold-fails-build` | `true` | Set to `false` to report without failing the job. |
| `comment` | `auto` | `auto` (always on a pull request, on a commit only when something is wrong), `always`, `never`. |
| `commit-comments` | `true` | Comment on the pushed commit when there is no pull request. |
| `annotations` | `true` | Annotate uncovered lines in the Files changed tab. |
| `job-summary` | `true` | Write the report to the job summary. |
| `github-token` | `github.token` | Token used for the comment. Needs `pull-requests: write`, or `contents: write` for commit comments. |
| `pr-number` | detected | Pull request to comment on. |
| `log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

`py_coverage_json`, `clover_coverage_json`, `base_ref`, `head_ref`, `gh_token`,
`pr_number` and `logging_level` are still accepted as deprecated aliases from v1.

## Outputs

| Output | Example |
| :-- | :-- |
| `coverage` | `83.33` |
| `changed-lines` | `16` |
| `covered-lines` | `5` |
| `uncovered-lines` | `11` |
| `files-checked` | `3` |
| `files-skipped` | `2` |
| `passed` | `true` / `false` |
| `markdown` | the full report as Markdown |

```yaml
- id: coverage
  uses: velis74/incremental-coverage-check@v3
  with:
    coverage-files: coverage.json

- if: steps.coverage.outputs.passed == 'false'
  run: echo "only ${{ steps.coverage.outputs.coverage }}% covered"
```

## Reporting

**Job summary.** The full report, always, whether the check passed or failed.

**Comment.** One comment, updated in place on every run rather than a new one each time.
It is recognised by a hidden `<!-- incremental-coverage-check -->` marker, so an old
comment is edited even if the workflow was renamed.

Where it goes depends on the event:

| Event | Comment lands on | Permission |
| :-- | :-- | :-- |
| `pull_request` | the pull request | `pull-requests: write` |
| `push` | the pushed commit | `contents: write` |

The default, `comment: auto`, treats the two differently. A pull request always gets the
report, passing or not: one comment serves the whole branch, later runs edit it rather
than adding to it, and it is the only place the numbers are visible without opening the
run. A commit gets the report only when something is wrong, because every push is a
different sha and therefore a new comment, which notifies the commit author each time.
Use `comment: always` to have passing pushes commented too, or `comment: never` to keep
the report to the job summary.

On a push, everything between `github.event.before` and the pushed commit is graded as a
single range — push five commits at once and you get one verdict over all five, posted as
a commit comment on the last of them. Set `commit-comments: false` to keep the report to
pull requests only.

Pull requests from **forks** get a read-only token, where posting is impossible; the
action logs a warning and carries on rather than turning a passing check into a failing
one. The same applies when GitHub itself is unavailable.

**Annotations.** Uncovered ranges are emitted as `::warning` commands, which GitHub draws
onto the changed lines in the Files changed tab. At most 40 are emitted; if there are
more, a notice says how many were left out and the full list stays in the summary.

## Choosing the base revision

- **Pull requests** — the default (`github.base_ref`) is right. The comparison uses the
  merge base, so unrelated commits on the target branch are not counted against you.
- **Pushes** — `github.event.before` is used, so a push of several commits is graded as
  one range rather than commit by commit. When it is the all-zero sha, which is what git
  reports for the first push to a new branch, the action falls back to the default branch,
  then to `HEAD~1`, then to the empty tree.
- **Shallow checkouts** — `actions/checkout` defaults to `fetch-depth: 1`, which usually
  lacks the base branch. The action fetches what it needs and deepens the history if the
  merge base is missing, but `fetch-depth: 0` is faster and more reliable.

## Running it locally

The check is a plain Python program with no dependencies:

```bash
python3 main.py --base main --coverage coverage.json --threshold 80
```

```
usage: incremental-coverage-check [-h] [--version] [-c PATH] [-b REF] [-H REF] [-w DIR]
                                  [--files PATH [PATH ...]] [-t THRESHOLD] [--ignore GLOB]
                                  [--extra-ignore GLOB] [--source-extensions EXT]
                                  [--comment {auto,always,never}] [--annotations BOOL]
                                  [--job-summary BOOL] [--markdown-out PATH]
                                  [--github-token GITHUB_TOKEN] [--repository OWNER/REPO]
                                  [--pr-number N] [-l LEVEL]
```

Useful while iterating:

```bash
# What exactly is being graded, and why?
python3 main.py --base main --coverage coverage.json --log-level DEBUG

# Write the Markdown report to a file instead of reading a terminal table
python3 main.py --base main --coverage coverage.json --markdown-out report.md

# Grade a single file
python3 main.py --base main --coverage coverage.json --files backend/orders/services.py
```

Exit codes: `0` passed, `1` below the threshold, `2` a configuration or input error.

Every input also has an `ICC_*` environment variable equivalent (`ICC_COVERAGE_FILES`,
`ICC_THRESHOLD`, `ICC_BASE_REF`, …); that is how the action passes its inputs, which is
why no input is ever interpolated into a shell command.

## Troubleshooting

**Everything shows as "no coverage data".** The paths in the coverage report do not line
up with the repository, and no candidate exists in the checkout. Run with
`--log-level DEBUG` to see the paths on both sides. The usual cause is a report produced in
a container whose paths share no suffix with the checkout. See [Path matching](#path-matching).

**A file is reported as ambiguous and counted uncovered.** Two files in the checkout match
the same report entry equally well. Run the coverage tool from the repository root, or emit
one report per source root.

**`cannot resolve base revision`.** The clone does not have the base branch. Use
`fetch-depth: 0` in `actions/checkout`.

**The score dropped after merging the base branch in.** It should not — the comparison
uses the merge base. If it did, check that `base-ref` names a branch and not a fixed sha.

**A file is graded that should not be.** Add it to `extra-ignore-patterns`. The collapsed
"not graded" section of the report shows which pattern caught each skipped file, which
makes it easy to see what is missing.

**The comment is not posted.** Check `permissions: pull-requests: write`, and remember
that fork pull requests cannot post at all.

## Development

```bash
pip install -e ".[dev]"                      # coverage + ruff, pinned in pyproject.toml
python3 -m unittest discover -s test -t .    # tests
ruff check . && ruff format --check .        # lint
```

Layout:

```
incremental_coverage/
    cli.py           argument parsing, orchestration, exit codes
    gitutil.py       every git invocation, with real error messages
    diff.py          unified diff -> added line numbers per file
    paths.py         resolving report paths onto files in the checkout
    coverage/
        model.py     the format-independent coverage model
        parsers.py   the five report formats
    ignore.py        which changed files must be covered
    analysis.py      the scoring rules
    render.py        terminal, Markdown and annotation output
    gha.py           job summary, outputs and the REST API, standard library only
```

The repository runs the check on itself using `uses: ./`, so a change to the action is
graded by the version being changed.

## License

MIT - see [LICENSE](LICENSE).
