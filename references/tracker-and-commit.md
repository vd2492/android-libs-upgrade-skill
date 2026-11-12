# Tracker, commit messages, Drive, Slack and Jira

## The tracker

`lib-upgrade-tracker.xlsx` at the repo root, managed only through `scripts/tracker.py`. It is
working state that goes to Google Drive, not into a commit - **add it to the project's `.gitignore`**
(ask the user first if the file is not already ignored). Two tabs
with the same columns: **SDK & Toolchain** (Part 1) and **Lib Upgrades** (Part 2).

| Column | Holds |
|---|---|
| lib name | the catalog `[versions]` key - exactly as written there |
| existing version | before the upgrade |
| new version | **what actually shipped** - not the latest available. When capped, record the shipped version and say what is held back in `code changes`. `check_comments.py` flags a `done` row whose value differs from the catalog. |
| official docs | the changelog link the changelog read used |
| code changes | `none - version only`, or every change beyond the version, with why |
| dev testing | what the developer verifies: modules, tests, goldens, the call sites that moved |
| qa testing | what to check on a device; `Nothing` for test-only libs |
| modules affected | from `modules.py consumers` |
| tier | Tier 1 patch / Tier 2 minor / Tier 3 major (a 0.x minor bump counts as major) |
| status | `pending` → `awaiting device` → `done`, or `blocked` / `skipped` |
| build result | the evidence: test counts, the device result, and anything still unverified |
| changelog highlights | bugs, ANRs, crashes and leaks fixed upstream - the Slack post reads this |

The status column is the resumable state. "continue the upgrade" runs `tracker.py next`.

## Commit messages

One commit per lib, or per coupled set (several artifacts of one SDK). **The skill writes it; the
user commits.**

```
build(deps): bump <lib> to <version> (<TICKET>)

<ref> <old> -> <new>

<Why it is safe: gates, breaking changes checked and why they do not apply.
 Every code change and why it was needed. What a reader would otherwise ask.>

Verification: <N> modules, <M> tests, 0 failures, 0 errors. <Device: variant, what was checked.>
<Anything NOT verified, stated plainly.>

<co-author trailer from the session's attribution guidance>
```

Variants:
- `build(sdk): bump compileSdk to 37 (<TICKET>)` / `build(sdk): target API 37 (<TICKET>)`
- `build(deps): bump the toolchain - Gradle, AGP, Kotlin, KSP (<TICKET>)`
- `docs(deps): record why <lib> is held (<TICKET>)` - comment-only changes
- `build(deps): bump <a> and <b>; hold <c> on <reason> (<TICKET>)` - a batch with one capped
- `build(deps): bump <N> patch releases (<TICKET>)` - an opted-in patch batch: list each
  `<ref> <old> -> <new>` in the body, with the batch's test counts and the device variant

## Publishing the tracker to Google Drive

Uses the Google Drive connector - check it with `session_connectors_status` - so there is no
download for the user to handle.

1. Find the folder the user gave (suggest **`Lib Upgrade Trackers`**) with the connector's search
   tool. If it is missing, create it with `mimeType` `application/vnd.google-apps.folder`.
2. Title: **`Lib Upgrade Tracker — <TICKET> — <YYYY-MM-DD>`**. Use an ISO date so files sort
   chronologically.
3. Search the folder for that exact title. If it exists (a second publish the same day), update
   it; otherwise create it.
4. Upload with `base64Content` = `base64 -i lib-upgrade-tracker.xlsx` and
   `contentMimeType` = `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.
   Leave conversion on, so it becomes a **Google Sheet**.
5. Give the user the link. It is private to them until they share it.

## The Slack post - when Part 2 finishes

Only when the libs part completes. A standalone SDK run does not post. Sending a message is
outward-facing: **show the exact text and the destination, and wait for a yes.** Find the channel
with the Slack connector's channel search; never guess an ID.

```
All libs upgraded - <TICKET> (<YYYY-MM-DD>)
<N> upgraded · <M> blocked · <K> skipped
Tracker: <Drive link>

Bugs / ANRs / leaks fixed upstream:
• <lib> <from>→<to>: <one line> (<link>)

Still blocked:
• <lib> <version>: <one-line reason>

QA still owed:
• <lib>: <what to check - e.g. push + in-app campaigns, install attribution>
```

## Jira

If the user gave a ticket, comment on it at each milestone - branch created, PR opened, merged -
and include the agent session name when the tool exposes one (`Session: <tool> — "<title>" (<id>)`),
so the work can be resumed in the same session. If no Jira tool is connected, write the comment
text for the user to paste rather than skipping it. No ticket given: skip, and say so in the report.
