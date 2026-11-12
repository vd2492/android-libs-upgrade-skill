# android-libs-upgrade

A skill that upgrades an Android app's SDK, Gradle toolchain and libraries safely, one at a time,
on any Gradle project that keeps its versions in a **version catalog** (`libs.versions.toml`).

It does the mechanical checks with scripts and keeps the judgement - majors, behaviour changes,
approvals - with the AI agent and you:

- screens every candidate before editing (`minCompileSdk` and the declared `kotlin-stdlib`);
- flags upgrades that depend on another upgrade, on Android Studio, or on a held-back version;
- reads changelogs, diffs the real API for majors, and **stops for your approval on any code change**;
- runs the consuming modules' unit tests and only trusts results from the current run;
- verifies the comments next to each version in the catalog after every bump;
- optional opt-in batching of patch releases (one test run, one build, one commit);
- a staging/R8 gate at the end, since a debug build does not minify;
- keeps a resumable tracker workbook; can publish it to Google Drive, post to Slack and comment on Jira.

It never builds the app or commits without your go-ahead.

## Requirements

- Python 3.9+ on the machine (`python3`), and `openpyxl` for the tracker: `python3 -m pip install --user openpyxl`
- A Gradle Android project with a version catalog. **No catalog = not supported** (migrate first).
- `git`; `gh` if the repo uses GitHub.
- Optional: Google Drive and Slack connectors, a Jira ticket, for the end-of-run steps.

## Install

### Claude Code

**Option A - one command (clone):**

```bash
git clone https://github.com/vd2492/android-libs-upgrade-skill ~/.claude/skills/android-libs-upgrade
```

For one project only, clone into `<project>/.claude/skills/android-libs-upgrade` instead.
Update with `git pull` inside that folder.

**Option B - plugin marketplace (managed updates):** run inside Claude Code:

```
/plugin marketplace add vd2492/android-libs-upgrade-skill
/plugin install android-libs-upgrade@android-libs-upgrade-skill
```

Update with `/plugin update`, remove with `/plugin uninstall`. Use one option, not both.

Then ask: "upgrade the libs", "bump AGP", "why is X blocked?" - the skill loads on its own, or
invoke it by name.

### Codex, Cursor, Antigravity and other AI IDEs

`SKILL.md` is Claude Code's skill format; other tools may not load it on their own. Everything else is
plain Markdown and Python, so the workflow is portable - **but I have not verified how each
tool loads instructions**. The tool-neutral way that should work anywhere an agent can read files and run
commands:

1. Copy this folder into the project (e.g. `tools/android-libs-upgrade/`).
2. Point the agent at it, using whatever your tool reads for standing instructions
   (`AGENTS.md`, `.cursor/rules`, a rules file, or just the first message):

   > To upgrade libraries, read `tools/android-libs-upgrade/SKILL.md` and follow it exactly. Its
   > scripts are in `tools/android-libs-upgrade/scripts/`; set `S` to that folder.

3. The agent needs shell access to run the scripts and file access to read `references/`.

The Drive, Slack and Jira steps need those tools to be connected in the IDE; without them the skill
skips the step and says so, and the agent writes the text for you to send.

## First run on a project

```bash
python3 <skill>/scripts/preflight.py     # what it found in this project
```

It prints the catalog it found and the AGP, Kotlin, compileSdk and other values it could read. If a line says
MISSING, add `lib-upgrade.config.json` at the project root (see "Project settings" in `SKILL.md`).

## What is in the folder

| Path | What |
|---|---|
| `SKILL.md` | the workflow and rules the agent follows |
| `references/` | per-part detail: SDK update, libs update, the screening gates, comment policy, traps, tracker/commit formats |
| `scripts/` | the mechanical checks (`preflight.py`, `scan_catalog.py`, `screen_gates.py`, `studio.py`, `batch.py`, `r8_check.py`, ...) |
| `scripts/tests/` | unit tests: `python3 -m unittest discover -s scripts/tests` |

## Status

Written from a skill used on one production app, then generalised: the version catalog, the
platform keys, the module layout and the build variants are detected or configured instead of assumed.
Verified so far only on that app. Expect to tune `lib-upgrade.config.json` the first time you run it
on a different project, and please report what the scripts got wrong.

Known limits: Android Studio detection is macOS-tested (Linux/Windows paths are best-effort); the
Studio-to-AGP table is dated and reports UNKNOWN outside it; the R8 check reads only R8's
missing-class warnings, so launching the minified build is still your check.
