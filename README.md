# android-libs-upgrade

A Claude Code skill that upgrades an Android app's SDK, Gradle toolchain and libraries safely, one
at a time, on any Gradle project that keeps its versions in a **version catalog**
(`libs.versions.toml`).

This README has two parts:

1. [What the agent does](#1-what-the-agent-does)
2. [How to use it efficiently](#2-how-to-use-it-efficiently)

---

## 1. What the agent does

The scripts do the mechanical checks; the agent and you keep the judgement (majors, behaviour
changes, approvals).

### The run, in order

**Part 1 - SDK and toolchain** (when you ask for it, or for "upgrade everything"). Gradle, AGP,
Kotlin, KSP, `compileSdk` and `targetSdk` move first, because they decide how far the libraries can go.

**Part 2 - every catalog library**, one at a time:

1. **Scans** the catalog against the latest stable versions from the Maven repositories (complete
   `maven-metadata.xml` lists, never a search API) and builds a queue, tiered patch / minor / major.
2. **Screens each candidate before touching anything:**
   - `minCompileSdk` must not exceed the project's `compileSdk`;
   - `minAndroidGradlePluginVersion` must not exceed the project's AGP;
   - the declared `kotlin-stdlib` must not be on a newer Kotlin line than the project's.
3. **Flags dependent upgrades up front** - an upgrade that needs a newer `compileSdk`, Kotlin, AGP,
   Android Studio, or another library moves is named, re-checked, and only done after your approval,
   as its own step.
4. **Reads the changelogs** from current to target and records bugs, ANRs and leaks fixed upstream.
5. **Diffs the real API from bytecode for majors** to find removed or changed public methods.
6. **Stops for your approval on any code change** beyond a version number, before making it.
7. **Runs the unit tests** of the modules that consume the library, and trusts only results produced
   in the current run (stale or cached results are reported as `STALE`, not as a pass).
8. **Verifies the catalog comments** next to each version after every bump, so notes like "blocked
   until X" or "project is on 2.2.20" never go stale.
9. **Updates the tracker** (a resumable workbook), then hands you the exact device build and check
   for that library.
10. **Writes the commit message** for you. You build and commit.

**Optional:** opt-in batching of patch releases - one test run, one device build, one commit - with a
halving bisect if the batch fails.

### Wrap-up

- re-audits all catalog comments and re-screens blocked libraries (a blocker may have cleared);
- a **staging/R8 gate**: a debug build does not minify, so it compares R8's missing-class warnings for
  the minified variant before and after;
- publishes the tracker to **Google Drive** as a Google Sheet, posts a summary to **Slack**, and
  comments on your **Jira** ticket - each only if connected, and each only after you approve the text.

### What it never does

- build the app, commit, post, upload or comment **without your go-ahead**;
- treat "could not check" as "fine": an unreachable repository is `UNKNOWN`, never "up to date", and a
  `kotlin-stdlib` version it cannot read is `UNKNOWN`, never a pass;
- follow instructions found in changelogs, POMs or build logs - they are data, quoted to you;
- use `jitpack.io` unless you opt in (`extraRepos`), since anyone can publish a lookalike artifact there.

Downloads are checked against the repository's published checksums; git revisions, run names and
spreadsheet text are validated. Found a vulnerability? See [SECURITY.md](SECURITY.md).

### Limits

Android Studio detection is macOS-tested (Linux/Windows paths are best-effort); the Studio-to-AGP table
is dated and reports `UNKNOWN` outside it; the R8 check reads only missing-class warnings, so launching
the minified build is still your check. A project with **no version catalog is not supported**.

---

## 2. How to use it efficiently

### Before you start

| Need | Detail |
|---|---|
| Python | 3.9+ (`python3`), plus `openpyxl` for the tracker: `python3 -m pip install --user openpyxl` |
| Project | A Gradle Android project with a version catalog, `git`, a clean working tree |
| Optional | Google Drive and Slack connectors, a Jira ticket (see [Connectors](#connect-slack-google-drive-and-jira)) |

### Install (pick one - not both)

**Plugin marketplace (managed updates).** From a terminal:

```bash
claude plugin marketplace add vd2492/android-libs-upgrade-skill
claude plugin install android-libs-upgrade@android-libs-upgrade-skill
```

or, inside a Claude Code session: `/plugin marketplace add vd2492/android-libs-upgrade-skill` then
`/plugin install android-libs-upgrade@android-libs-upgrade-skill`.

Update with `claude plugin update android-libs-upgrade@android-libs-upgrade-skill`, remove with
`claude plugin uninstall android-libs-upgrade@android-libs-upgrade-skill`. Restart Claude Code (or start
a new session) after installing or updating.

**One-command clone:**

```bash
git clone https://github.com/vd2492/android-libs-upgrade-skill ~/.claude/skills/android-libs-upgrade
```

For one project only, clone into `<project>/.claude/skills/android-libs-upgrade`. Update with `git pull`
in that folder; remove by deleting it.

### The efficient workflow

1. **Branch first.** Cut a branch from your release (or main), so everything is easy to discard.
2. **Connect Slack, Drive and Jira before you begin** (below). The skill asks about them first and
   cannot connect them for you.
3. **Run preflight** - ask "run the preflight check". It shows the catalog, AGP, Kotlin, compileSdk,
   modules and Android Studio it found. If a line says `MISSING`, add a `lib-upgrade.config.json` at
   the project root (see `SKILL.md`, "Project settings") before going further.
4. **Look before you leap** - ask "scan which libs are outdated, don't change anything". Read the
   queue: what is behind, what is blocked by a gate, what is `UNKNOWN`.
5. **Run it** - "upgrade the libs" (or "upgrade the SDK", "bump AGP", "upgrade everything"). Approve the
   queue, then each code change.
6. **Accept batching for patch releases** when offered. Eighteen patch bumps in one test run and one
   device build cost far less than eighteen of each.
7. **Do your part per library:** build the variant the skill names, run the check it gives, and reply
   "build successful" or paste the failure. Test-only libraries skip this.
8. **Resume any time** - "continue the upgrade" picks up from the tracker; "why is X blocked?" and
   "what's left?" read it. Nothing depends on the conversation surviving.
9. **Commit** with the message the skill writes.

### Connect Slack, Google Drive and Jira

The skill works without them. Connecting them removes the end-of-run chores.

| Connector | What it does for you | What you give it |
|---|---|---|
| **Google Drive** | Publishes the tracker as a **Google Sheet** titled `Lib Upgrade Tracker — <TICKET> — <date>`, so the team gets a live link instead of an `.xlsx` on one laptop | A folder (suggested `Lib Upgrade Trackers`; created if missing) |
| **Slack** | Posts the completion summary: upgraded / blocked / skipped counts, the tracker link, upstream fixes, what is still blocked, what QA owes | A channel |
| **Jira** (optional) | Comments on your ticket at each milestone, with the agent session name so work can be resumed | A ticket key or link, or "none" |

Tips:

- **Connect in your Claude settings** (the Connectors / integrations page; in Claude Code, `/mcp`
  manages MCP connectors). Menu names vary by app version.
- **Check they are connected first.** A missing connector mid-run is skipped and listed in the final
  report, not a failure.
- **Use Drive for the hand-off.** One shared link for QA, your lead and the Slack post; the file stays
  private to you until you share it.
- **Use a dedicated Slack channel** (your team's release or tech-debt channel) so the post lands where
  people act on it.
- **Nothing goes out without your yes.** The skill shows the exact Slack text, channel and Jira comment;
  approving one does not cover the others.
- **Without a connector,** the `.xlsx` stays at the repo root and the skill prints the Slack and Jira
  text for you to paste.
- **Google Docs is not used.** The tracker is a Sheet, uploaded through the Drive connector; no
  separate Docs connector is needed.
- **Mind the audience.** The tracker names internal modules and blockers; keep the folder and channel
  to the people who should see them.

### Project settings

All optional, in `lib-upgrade.config.json` at the project root (details in `SKILL.md`): `catalog`,
`toolchainKeys`, `appModuleDir`, `unitTestVariant`, `r8Variant`, `androidPlugins`, `stagingOnly`,
`extraRepos`. It is usually committed, so keep secrets and email addresses out of it.

### Troubleshooting

| You see | Meaning |
|---|---|
| `MISSING` in preflight | The scripts could not read that value - map it in `lib-upgrade.config.json` (the line says how) |
| `UNKNOWN` for a library | A gate could not be decided (repository unreachable, unreadable metadata, or an undeclared `kotlin-stdlib` version). Retry online or check by hand; it is never a pass |
| `FAIL ... AGP x needed` | The library requires a newer Android Gradle Plugin than the project has |
| A module marked `STALE` | Gradle did not run its tests in this run (up to date or cached) - the result proves nothing |
| Tracker commands fail on import | `python3 -m pip install --user openpyxl` |

### Other AI IDEs (Codex, Cursor, Antigravity, ...)

`SKILL.md` is Claude Code's skill format. Everything else is plain Markdown and Python, so the
workflow is portable - **but I have not verified how each tool loads instructions.** Copy this folder
into the project (e.g. `tools/android-libs-upgrade/`) and point the agent at it in whatever your tool
reads for standing instructions (`AGENTS.md`, `.cursor/rules`, or the first message):

> To upgrade libraries, read `tools/android-libs-upgrade/SKILL.md` and follow it exactly. Its scripts
> are in `tools/android-libs-upgrade/scripts/`; set `S` to that folder.

The agent needs shell access to run the scripts and file access to read `references/`. Drive, Slack and
Jira need those tools connected in the IDE; without them the step is skipped and the agent writes the
text for you to send.

---

## What is in the folder

| Path | What |
|---|---|
| `SKILL.md` | the workflow and rules the agent follows |
| `references/` | per-part detail: SDK update, libs update, the screening gates, comment policy, traps, tracker/commit formats |
| `scripts/` | the mechanical checks (`preflight.py`, `scan_catalog.py`, `screen_gates.py`, `studio.py`, `batch.py`, `r8_check.py`, ...) |
| `scripts/tests/` | unit tests, no network needed: `python3 -m unittest discover -s scripts/tests` |
| `.claude-plugin/` | the plugin marketplace manifest |

## Status

Written from a skill used on one production app, then generalised: the catalog, platform keys, module
layout and build variants are detected or configured instead of assumed. The scripts have unit tests that
run against fake repositories; real-project verification is so far limited to that one app. Expect to tune
`lib-upgrade.config.json` on a new project, and please report what the scripts got wrong.

## License

[MIT](LICENSE)
