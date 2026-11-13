# android-libs-upgrade

A skill that upgrades an Android app's SDK, Gradle toolchain and libraries safely, one at a time,
on any Gradle project that keeps its versions in a **version catalog** (`libs.versions.toml`).

It does the mechanical checks with scripts and keeps the judgement - majors, behaviour changes,
approvals - with the AI agent and you:

- screens every candidate before editing (`minCompileSdk`, `minAndroidGradlePluginVersion` and the
  declared `kotlin-stdlib`);
- flags upgrades that depend on another upgrade, on Android Studio, or on a held-back version;
- reads changelogs, diffs the real API for majors, and **stops for your approval on any code change**;
- runs the consuming modules' unit tests and only trusts results from the current run;
- verifies the comments next to each version in the catalog after every bump;
- optional opt-in batching of patch releases (one test run, one build, one commit);
- a staging/R8 gate at the end, since a debug build does not minify;
- keeps a resumable tracker workbook; can publish it to Google Drive, post to Slack and comment on Jira.

It never builds the app or commits without your go-ahead.

## Quick start

```bash
# 1. install (see "Install" for the other options)
claude plugin marketplace add vd2492/android-libs-upgrade-skill
claude plugin install android-libs-upgrade@android-libs-upgrade-skill

# 2. in your Android project: work on a branch, install the tracker's one dependency
git checkout -b lib-upgrade
python3 -m pip install --user openpyxl
```

Then start Claude Code in the project (a new session, so the skill loads) and ask:

1. "run the preflight check for the lib upgrade skill" - shows what it could read from your project;
2. "scan which libs are outdated, don't change anything" - a read-only first look;
3. "upgrade the libs" - the real run; you approve the queue and every code change.

## Requirements

- Python 3.9+ on the machine (`python3`), and `openpyxl` for the tracker: `python3 -m pip install --user openpyxl`
- A Gradle Android project with a version catalog. **No catalog = not supported** (migrate first).
- `git`; `gh` if the repo uses GitHub.
- Optional: Google Drive and Slack connectors, a Jira ticket, for the end-of-run steps
  (see "Connectors" below).

## Install

### Claude Code (CLI and desktop app)

Pick **one** of the two options - not both, or the skill is installed twice.

**Option A - plugin marketplace (managed updates).** From a terminal:

```bash
claude plugin marketplace add vd2492/android-libs-upgrade-skill
claude plugin install android-libs-upgrade@android-libs-upgrade-skill
```

Or, inside a running Claude Code session:

```
/plugin marketplace add vd2492/android-libs-upgrade-skill
/plugin install android-libs-upgrade@android-libs-upgrade-skill
```

Update with `claude plugin update android-libs-upgrade@android-libs-upgrade-skill` (or `/plugin update`),
remove with `claude plugin uninstall android-libs-upgrade@android-libs-upgrade-skill`. Restart Claude
Code (or start a new session) after installing or updating so the change is picked up.

**Option B - one command (clone):**

```bash
git clone https://github.com/vd2492/android-libs-upgrade-skill ~/.claude/skills/android-libs-upgrade
```

For one project only, clone into `<project>/.claude/skills/android-libs-upgrade` instead.
Update with `git pull` inside that folder; remove by deleting the folder.

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

Work on a branch cut from your release (or main), with a clean working tree. Then:

```bash
python3 <skill>/scripts/preflight.py     # what it found in this project
```

(In Claude Code you can simply ask for the preflight; the skill runs it for you.)

It prints the catalog it found and the AGP, Kotlin, compileSdk and other values it could read. If a line says
MISSING, add `lib-upgrade.config.json` at the project root (see "Project settings" in `SKILL.md`).

What you will be asked, and what you do, is spelled out in `SKILL.md` ("What the user does"): approve the
queue, approve each code change, build the variant the skill names, and commit with the message it writes.

## Connectors: Slack, Google Drive and Jira

The skill works without any of these. Connecting them removes the manual end-of-run chores: moving
the tracker file around, writing the status post, and updating the ticket. **Connect them before you
start** - the skill asks about them first, and cannot connect them for you.

| Connector | What it does for you | What you give it |
|---|---|---|
| **Google Drive** | Publishes the tracker workbook as a **Google Sheet** titled `Lib Upgrade Tracker — <TICKET> — <date>`, so the team has a live link instead of an `.xlsx` on someone's laptop | A folder name (suggested: `Lib Upgrade Trackers`; it is created if missing) |
| **Slack** | Posts the completion summary: counts upgraded / blocked / skipped, the tracker link, upstream bugs and ANRs fixed, what is still blocked and why, what QA still owes | A channel |
| **Jira** (optional) | Comments on your ticket at each milestone, with the agent session name so work can be resumed | A ticket key or link, or "none" |

Tips for getting the most out of them:

- **Connect them in your Claude settings** (the Connectors / integrations page of your Claude app; in
  Claude Code, `/mcp` lists and manages MCP-based connectors). The exact menu names depend on your app
  version.
- **Check they are connected before you begin.** If Drive or Slack is missing mid-run, the skill skips
  that step and lists it in the final report instead of failing.
- **Use Drive for the hand-off.** Publishing the tracker once at the end gives QA, your lead and the
  Slack post one shared link; the file is private to you until you share it.
- **Use a dedicated channel for Slack** (for example your team's release or tech-debt channel) so the
  post lands where people act on it.
- **Nothing is posted without your approval.** The skill shows you the exact Slack text and channel,
  and the exact Jira comment, and waits for a yes for each - an approval for one does not cover the
  others.
- **Without a connector,** the skill leaves the `.xlsx` at the repo root and prints the Slack and Jira
  text for you to paste.
- **Google Docs:** the skill does not write Google Docs. The tracker is a Sheet (via Drive). Use the
  Drive connector; no separate Docs connector is needed.
- **Mind what you publish.** The tracker names your internal modules and blockers. Keep the Drive folder
  and the Slack channel to the audience that should see them.

## Safety

- **Nothing outward without a yes.** No builds, commits, Slack posts, Jira comments or Drive uploads
  happen without your approval.
- **Offline is never "up to date".** If a Maven repository cannot be reached, results are `UNKNOWN`,
  not "no newer version". Failed downloads are retried and never cached.
- **Gates fail closed.** A library whose `kotlin-stdlib` version cannot be read (parent POM, range,
  unresolved property) is `UNKNOWN`, not a pass.
- **Downloads are checked** against the repository's published `.sha256` / `.sha1`.
- **Repositories:** Google, Maven Central and the Gradle plugin portal only. `jitpack.io` is off by default
  because anyone can publish a lookalike artifact there; add it per project with `extraRepos` in
  `lib-upgrade.config.json` if you need it.
- **Web content is data.** Changelogs, POMs and build logs are read for facts; instructions found in
  them are quoted to you, not followed.
- **Input is validated:** git revisions that look like options, run names that could escape the output
  folder, and spreadsheet text starting with `=` are all neutralised.

Found a vulnerability? See [SECURITY.md](SECURITY.md) - please report it privately.

## Project settings

All optional, in `lib-upgrade.config.json` at the project root (details under "Project settings" in
`SKILL.md`): `catalog`, `toolchainKeys`, `appModuleDir`, `unitTestVariant`, `r8Variant`,
`androidPlugins`, `stagingOnly`, `extraRepos`. Do not put secrets or email addresses in it - it is
usually committed.

## What is in the folder

| Path | What |
|---|---|
| `SKILL.md` | the workflow and rules the agent follows |
| `references/` | per-part detail: SDK update, libs update, the screening gates, comment policy, traps, tracker/commit formats |
| `scripts/` | the mechanical checks (`preflight.py`, `scan_catalog.py`, `screen_gates.py`, `studio.py`, `batch.py`, `r8_check.py`, ...) |
| `scripts/tests/` | unit tests: `python3 -m unittest discover -s scripts/tests` (no network needed) |
| `.claude-plugin/` | the plugin marketplace manifest |

## Troubleshooting

| You see | Meaning |
|---|---|
| `MISSING` in preflight | The scripts could not read that value - map it in `lib-upgrade.config.json` (the line says how) |
| `UNKNOWN` for a library | A gate could not be decided (repository unreachable, unreadable metadata, or an undeclared `kotlin-stdlib` version). Check by hand or retry online; it is never treated as a pass |
| `FAIL ... AGP x needed` | The library's AAR requires a newer Android Gradle Plugin than the project has |
| A module's tests marked `STALE` | Gradle did not run them in this run (up to date or cached) - the result proves nothing |
| Tracker commands fail on import | Install `openpyxl`: `python3 -m pip install --user openpyxl` |

## Status

Written from a skill used on one production app, then generalised: the version catalog, the
platform keys, the module layout and the build variants are detected or configured instead of assumed.
The scripts have unit tests that run against fake repositories; real-project verification is so far
limited to that one app. Expect to tune `lib-upgrade.config.json` the first time you run it
on a different project, and please report what the scripts got wrong.

Known limits: Android Studio detection is macOS-tested (Linux/Windows paths are best-effort); the
Studio-to-AGP table is dated and reports UNKNOWN outside it; the R8 check reads only R8's
missing-class warnings, so launching the minified build is still your check.

## License

[MIT](LICENSE)
