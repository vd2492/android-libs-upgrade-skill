# Part 2 - Libs update

Every catalog ref that is not part of the platform. One lib at a time, one commit each.

```bash
S=<absolute path of this skill's scripts/ folder>
python3 $S/scan_catalog.py --part libs                          # also computes modules affected
python3 $S/tracker.py init --scan build/lib-upgrade/scan-libs.json --part libs
```

Before the first bump, record R8's current state so the wrap-up can tell new warnings from old
(free - it reads whatever minified (staging/release) build already exists, and never triggers one):

```bash
python3 $S/r8_check.py snapshot baseline
```

If Part 1 is stale - the scan's platform line shows compileSdk behind the latest API level -
say so first ("compileSdk 37 is out; running the SDK part first would unblock ~11 libs").

## 1. Screen everything up front

```bash
for ref in $(python3 $S/tracker.py list --status pending --part libs | awk '{print $2}'); do
  python3 $S/screen_gates.py "$ref" --bisect
done
```

- A failing candidate prints **`depends on:`** - the compileSdk or Kotlin it needs (`screen_gates.py`
  works it out from the gates). Show it in the queue as "X needs Y first": Y is a Part 1 step that
  gets its own approval and commit, not a quiet edit alongside this lib.
- A candidate that fails a gate: take the **highest version that passes** (`--bisect`). If that is
  the current version, mark the row `blocked` with the exact reason, and write the catalog comment.
- **Dependent upgrades (rule 8):** where the highest passing version still depends on another
  upgrade (a gate that only a toolchain/SDK/lib bump clears, or a lib that forces another to move),
  list it in a "depends on" column of the queue: the upgrade, what it needs, and why. Before asking
  for approval, run the changelog / API-diff / gate / modules-affected checks on the **dependent**
  upgrade too, and show what it would change. Update the dependent only once the user approves.
- **Held-back report (rule 9):** for every row whose chosen version is below the latest stable,
  show `latest | chosen | constraint | hard/soft` in the queue. A soft constraint gets the
  higher version offered, with the check that settles it.
- **DIVERGED** refs cannot be bumped as one - report them; splitting the ref is a code change.
- Show the user the queue - T1 patches, then T2 minors, then T3 majors - and the blocked list
  with reasons. **Wait for approval or a reorder.**

### 1b. Optional: batch the patches

Ask once, after the queue is approved: "N patches can go as one batch - one test run, one device
build, one commit. Batch them?" **Default is no**: if the user declines, or says nothing, every
lib runs the normal loop below, unchanged.

```bash
python3 $S/batch.py plan              # eligible patches, target versions, union of test tasks
```

Only Tier 1 rows that pass both gates qualify. The plan leaves out, with the reason, anything
whose catalog comment says blocked, "moves with" (a coupled set), or needs its own device pass,
plus `references/staging-only-libs.txt` (a plugin that skips the debug variant is the usual case). Minors and majors are never batched.

For a batch the user accepts:
1. Do step 2a for **every** lib in it - the changelog read and the highlights still happen (the
   Slack post reads them). Any lib needing a code change (2c) leaves the batch and runs the
   normal loop; **report it, wait for a yes.**
2. Edit every version line, then verify once:
   ```bash
   python3 $S/validate_catalog.py --expect <ref1,ref2,...>
   python3 $S/check_comments.py
   bash    $S/run_gradle.sh batch1 $(python3 $S/batch.py tasks <ref1> <ref2> ...)
   python3 $S/test_report.py batch1 $(python3 $S/batch.py tasks <ref1> <ref2> ...)
   ```
   and check resolution (2d) for the batch's refs.
3. **A failure is halved, not blamed on the batch**: re-run at the same versions first
   (`--rerun-tasks`), then `python3 $S/batch.py split <refs>`, revert one half's version lines,
   and re-run. Repeat until a single ref fails; it goes back to the normal loop (cap, fix or
   block as in 2f - never below the committed version). The rest of the batch lands.
4. Set every row `awaiting device` with the same evidence (`BATCH n: <N> modules, <M> tests, 0
   failures`), give one hand-off (2e) and one commit message (`tracker-and-commit.md`). Build
   the variant the **most demanding** lib in the batch needs.

## 2. The per-lib loop

For the next row (`tracker.py next`):

### a. Changelog
Read every release from current to target on the row's `official docs` link. Record:
- breaking changes and deprecations that touch this code;
- **bugs, ANRs, crashes and leaks fixed upstream** → `tracker.py set <ref> --highlights "..."`.
  These are what the Slack post reports at the end.

### b. Majors (T3): diff the real API
```bash
python3 $S/api_diff.py <group:artifact> <old> <new> --class <FQCN> [--follow]
```
- Collect the classes the code actually imports (`grep -rh "^import <package>"`) and check each
  one with `--symbols`.
- An umbrella artifact (tiny `classes.jar`) needs `--follow`.
- A gained `throws` breaks **Java** call sites only. Kotlin ignores it.
- A changed return type (`void` → `MoETask<...>`): check whether the task is eager before
  ignoring it.
- Signatures are not behaviour. If a method now throws, **read the bytecode to see when** - MoEngage
  15 throws only while its integration validator is on, and that defaults to on.

### c. Code change needed? Stop.
If anything beyond the version number must change, **stop and report it before implementing**:
what breaks, which files, the smallest fix, and what behaviour it preserves or changes. Wait for a
yes. This is the user's standing rule, not a courtesy.

The best fix is often one line at the source, not N call sites: MoEngage 15 needed one
`disableIntegrationValidator()` call ahead of the deferred init, plus three Java `try/catch` blocks
the checked exception forced - not guards on 49 call sites.

### d. Bump and verify
```bash
# edit the one version line (anchor to the full line - `newrelic = ` matches two lines)
python3 $S/validate_catalog.py --expect <ref>            # shape + exactly this ref moved
python3 $S/check_comments.py                              # every finding updated or --ack'ed
bash    $S/run_gradle.sh <ref> $(python3 $S/modules.py tasks <ref>)
python3 $S/test_report.py <ref> $(python3 $S/modules.py tasks <ref>)
```
- `modules.py tasks` prints nothing runnable when no consumer has tests. Say so plainly - the
  verification is then the dependency report, not a unit test.
- More than ~20 modules: batch them - a foreground run is capped at 10 minutes. If a run moves to
  the background, the `.rc` file is still the answer.
- A test failure: re-run **at the same version** (`--rerun-tasks`) before blaming the bump.
- Check resolution, not just compilation:
  `./gradlew :app:dependencyInsight --configuration debugRuntimeClasspath --dependency <group:artifact>`
  - is the new version the one that wins? Did anything downgrade? Is kotlin-stdlib still on the
  project's line?
- Test-only libs (roborazzi, mockk, kover) never reach the APK. Verify them entirely on the JVM -
  for roborazzi, **baseline `verifyRoborazziDebug` before the bump**, then verify all goldens after
  (214/214 unchanged the first time) - and skip the device step.

### e. Update the tracker and hand off
```bash
python3 $S/tracker.py set <ref> --status "awaiting device" --new-version <v> \
  --build "JVM: N modules, M tests, 0 failures" --dev "..." --qa "..." --changes "..."
```
Tell the user:
- **which variant to build** - a plugin that excludes debug (NewRelic) needs staging
  (`references/staging-only-libs.txt` lists them);
- **what to test**, from the row's `qa testing`. Be explicit when a build proves little: MoEngage
  needed push and in-app campaigns exercised, AppsFlyer needed install attribution checked in its
  dashboard.

### f. After "build successful"
```bash
python3 $S/tracker.py set <ref> --status done --build "DEVICE VERIFIED: ..."
```
Write the commit message (`tracker-and-commit.md`). **The user commits.** Then `tracker.py next`.

If the user pastes a build failure instead: diagnose it (`dependencyInsight`, the stack), then fix
it, cap at the highest version that works, or mark it blocked - **never below the version already
committed.** The coil failure was a kotlin-stdlib 2.4.0 leak: coil was capped at 3.4.0 and the
row marked blocked with that reason.

## 3. Blocked rows

Keep the highest version that works, and write the reason on the catalog line, naming the exact
blocker and its numbers (`comments.md`). A row blocked for a non-gate reason - a code change,
APK size, an API removal - stays blocked until that reason is resolved, whatever the gates say.

## 4. When the queue is empty

```bash
python3 $S/check_comments.py --audit
python3 $S/screen_gates.py --blocked
python3 $S/tracker.py summary
```

Then run the wrap-up in `SKILL.md` - it starts with the staging / R8 gate - publish the tracker to Drive, post to Slack, write the Jira (each only if connected)
comment, offer push + PR.
