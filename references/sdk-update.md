# Part 1 - SDK update (toolchain, compileSdk, targetSdk)

Run this before Part 2. Both library gates are platform values - minCompileSdk against compileSdk,
kotlin-stdlib against Kotlin - so moving the platform first maximises how far the libs can go.
A major release of an analytics SDK was once blocked outright on Kotlin 1.9, and another SDK's
major needed compileSdk 36.

Order: **toolchain → compileSdk → targetSdk**, one commit each. Rows go in the tracker's
"SDK & Toolchain" tab.

```bash
S=<absolute path of this skill's scripts/ folder>
python3 $S/scan_catalog.py --part sdk            # platform vs latest, incl. latest API level
python3 $S/tracker.py init --scan build/lib-upgrade/scan-sdk.json --part sdk
```

## Step 1 - establish the caps before choosing versions

Two caps sit outside the build. Record both in the catalog comments.

- **Android Studio caps AGP.** Run `python3 $S/studio.py --agp <candidate>` - it reads the installed
  Studio and says whether it syncs that AGP (exit 0 yes, 1 needs a newer Studio, 2 depends on the
  patch or is unknown). It sees only this machine: ask the user for their version when none is
  found, and whether the team is on it too. Otherwise have them sync:
  AGP 9.4.0 built and tested clean from the CLI, but the IDE refused
  (`Latest supported version is AGP 9.2.1`). A build the user cannot sync is no use to them.
  **If the newest AGP needs a newer Android Studio, do not settle for the lower AGP silently.**
  Tell the user the Studio version that unlocks it (Google's Studio-to-AGP table:
  developer.android.com/studio/releases) and ask them to update. **Updating is the user's
  choice** - offer it, then follow their answer: if they update, take the newer AGP; if they
  decline, cap AGP at what their Studio supports and record that as a user decision in the
  catalog comment (the 9.2.1 pin was once recorded as a technical cap, which hid that
  updating Studio would have lifted it). Never update, or demand an update of, their IDE yourself.
- **KSP caps Kotlin.** KSP embeds a Kotlin compiler and drives Room and Hilt codegen. Kotlin 2.4.x
  was published with no KSP built for it, so Kotlin stays at the version the newest KSP embeds.

Then read the changelog for every version from current to target, for each piece.

**A toolchain/SDK step that unblocks libs, or that is itself blocked by another upgrade, is a
dependency (SKILL.md rule 8):** highlight it, show what it would change, and wait for approval
before moving it.

**Held-back report (SKILL.md rule 9).** Before Step 2, list each toolchain item as
`latest | chosen | constraint | hard/soft`. Example: `AGP 9.5.1 | 9.4.0 | Studio 2026.2.1 caps at
9.4 (hard for 9.5.1); NewRelic 7.8.x declares 9.4.0 as its ceiling (soft - warns) so 9.4.1 is
untested | 9.5.1 hard, 9.4.1 soft`. Offer the highest soft-blocked version with the check that
settles it (for NewRelic: build staging and look for `newrelic.warning.agp`).

## Step 2 - toolchain (Gradle, AGP, Kotlin, KSP) as one set

- **Gradle:** `./gradlew wrapper --gradle-version <v>` regenerates the wrapper jar and scripts as
  well as `gradle-wrapper.properties`. AGP states a minimum Gradle; honour it.
- **AGP:** highest ≤ the Android Studio cap, once the user has chosen whether to update Studio
  (Step 1). `lint` tracks AGP (lint = AGP + 23: AGP 9.5.1 ↔ lint 32.5.1) - move them together.
- **Kotlin:** keep `kotlin_plugin` and `jetbrain_kotlin` in lockstep, at the Kotlin the chosen KSP
  embeds.
- **Every Gradle plugin carries its own AGP window.** NewRelic 7.7.4 capped AGP at 9.0.0 and was
  silently out of range after AGP moved to 9.2.1 - it only warns. Read the window from the plugin
  jar: `javap -p -constants -cp <jar> com.newrelic.agent.android.BuildHelper`.
- Run **every** module's tests (`modules.py list`, batched through `run_gradle.sh`). A toolchain
  bump reaches everything. Kotlin 2.3.20 promoted an intersection-type reification warning to an
  error at five test sites (`arrayOf(...)` → `arrayOf<Any?>(...)`), and `:base` going green hid
  the fifth, which was in another module.
- Any code change: **stop and report it first.**
- Hand-off: the user **syncs Android Studio**, then builds on device.

## Step 3 - compileSdk

API availability only - no behaviour change. AGP must support the new level.

1. Bump `compile_sdk_version`; fix new deprecation errors.
2. Run tests across all modules.
3. **Re-screen blocked rows** - this is the gate most of them wait on:
   `screen_gates.py --blocked`. At compileSdk 37 expect `core_ktx`, the Compose 1.12 family,
   lifecycle 2.11, navigation 2.10 and glide 5.0.9 to clear. coil 3.6 will **not** clear,
   because it also declares kotlin-stdlib 2.4.10.
4. `check_comments.py --audit` - every "Retry when compileSdk N lands" is now actionable.

## Step 4 - targetSdk

This is the one with behaviour changes. Work from the official page:
`https://developer.android.com/about/versions/<N>/behavior-changes-<N>`.

For **every** item on it:

1. Does it apply to this app? Find the code it touches.
2. **Was it already enforced at the previous level?** Check that level's page too. Edge-to-edge
   and `elegantTextHeight` were twice presented as API 36 risks - both were already in force at 35.
3. Only change what the new level actually requires. Three inset fixes once got bundled into the
   targetSdk change; they were pre-existing bugs and belonged in their own change.

List each needed change with its files and **wait for approval before implementing.** Then
implement with unit tests, following the repo's own testing rules.

What Android 16 (API 36) actually required, as a model:

| Change | What it took |
|---|---|
| Predictive back on by default - `onBackPressed()` no longer called | 8 activities moved to `onBackPressedDispatcher.addCallback` (Kotlin) / `OnBackPressedCallback` (Java) |
| Orientation / resizability ignored at ≥600dp | `PROPERTY_COMPAT_ALLOW_RESTRICTED_RESIZABILITY` in the manifest - an opt-out that expires at API 37 |
| Robolectric's SDK 36 sandbox needs Java 21; the project builds on Java 17 | `robolectric.properties` with `sdk=35` in 22 test source sets, until the test toolchain moves |

**The device checklist is the deliverable here**, because behaviour changes are exactly what unit
tests cannot see. For API 36: screens that must refuse to dismiss on back (order allocation,
the Qcom popup) still refuse; CustomPick still returns trip data; tablet orientation behaves.

## Step 5 - after the platform moves

```bash
python3 $S/screen_gates.py --blocked      # blockers that cleared
python3 $S/check_comments.py --audit      # comments the move made stale
```

Then continue to Part 2.
