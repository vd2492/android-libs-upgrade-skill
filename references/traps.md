# Traps

Every entry here happened, on the project this skill was first written for - the library and
vendor names are real examples, the lessons are general. Most were made by the agent running this
workflow, and each rule exists because the obvious approach failed.

## Finding versions

| Trap | Rule | Enforced by |
|---|---|---|
| Maven Central Solr search with `rows=40` returned an arbitrary version window and reported 11 outdated libs as current - one never entered the tracker | Read `maven-metadata.xml`, the complete list, from every repo | `scan_catalog.py` |
| The same search only hit Maven Central, so 5 refs on Google Maven were marked "check by hand" | Query Google Maven, Maven Central, the Gradle plugin portal and JitPack | `_maven.py` |
| "Gradle is already current" was said without checking - it was two minors behind | The platform is scanned like everything else | `scan_catalog.py --part sdk` |
| A size figure was wrong because a ranged `curl` was mixed with a `HEAD` request | Measure one way, from the artifact itself | - |

## Screening

| Trap | Rule | Enforced by |
|---|---|---|
| coil 3.5.0 passed 2,902 unit tests, then broke `:app:hiltJavaCompileDebug` via kotlin-stdlib 2.4.0 | Screen gate 2 before bumping; it fails far from its cause | `screen_gates.py` |
| Only AndroidX libs were screened for minCompileSdk, so coil and glide were missed | Screen every candidate, whatever the group | `screen_gates.py` |
| "Latest is blocked" was recorded as "no upgrade" for core_ktx, compose_bom, map_utils and maps_compose | Always bisect to the highest version that works | `screen_gates.py --bisect` |
| A comparison used the wrong artifact on a two-artifact ref (litert vs litert-support-api) | Screen every artifact on the ref; report DIVERGED | `scan_catalog.py` |
| A blocked row stayed "blocked" after the Kotlin bump cleared it | Re-screen blocked rows whenever a gate value moves | `screen_gates.py --blocked` |

## Running Gradle

| Trap | Rule | Enforced by |
|---|---|---|
| The harness reported "exit code 0" for a Gradle run whose real exit was 1 - **twice** | Gradle's exit code is written to a file the instant it returns; read that file | `run_gradle.sh` |
| An `UP-TO-DATE` task leaves the previous run's pass in place | Judge freshness by the XML's internal `timestamp`, never file mtime | `test_report.py` |
| A `FROM-CACHE` task rewrites the XML with new mtimes without running a test | Same - the internal timestamp survives a cache restore | `test_report.py` |
| One module going green (`:base`) was taken as done; a fifth failing site sat in another module | Run every consuming module, not the first one that passes | `modules.py tasks` |
| A single test failed with `UncaughtExceptionsBeforeTest` and looked like the bump's fault | Re-run at the **same** version first; it passed - an order-dependent leak, not the bump | discipline |
| A batch passed 10 minutes and moved to the background; its "exit 0" notification was the shell's | Read the `.rc` file | `run_gradle.sh` |
| `platform/webrtc-call` is `:platform:webrtccall`; projects use both `include ':x'` and `include(":x")` | Take project paths from `settings.gradle`, never from directory names | `modules.py` |
| `:platform:linter` is pure JVM - `testDebugUnitTest` does not exist and failed the whole invocation | Take the test task from the build file | `modules.py` |

## Shell (zsh)

| Trap | Rule |
|---|---|
| `for m in $MODS` did not word-split and created a directory named after a 400-character string | zsh does not split unquoted variables - use arrays: `tasks=(a b); ./gradlew "${tasks[@]}"` |
| `":$m:testDebugUnitTest"` became `:baseestDebugUnitTest` - `:t` is a zsh modifier | Brace it: `"${m}:testDebugUnitTest"`, or use the scripts, which run under bash |
| `M="--base x --head y"; cmd $M` passed one argument | Same as the first row - spell the arguments out |

## Code changes

| Trap | Rule |
|---|---|
| "Kotlin has no checked exceptions, so everything compiles" - three **Java** files did not (MoEngage `SdkNotInitializedException extends Exception`) | A newly declared `throws` breaks Java call sites only; `api_diff.py` flags gained `throws` clauses |
| `MoESdkStateHelper` was imported as a class and did not compile - it is a Kotlin file facade (no `INSTANCE`) | Check `javap` for `INSTANCE`; call a facade's members as top-level functions |
| `moe-android-sdk` ships a 1.4 KB `classes.jar` - the real classes are in `core`, `pushbase`, ... | `api_diff.py --follow` |
| MoEngage 15 throws only while its integration validator is on - which defaults to **on** | Read the bytecode for the runtime path, not just the signature |
| `showInApp()` changed from `void` to `MoETask<...>` | Check whether the returned task is eager or lazy before ignoring it |
| A behaviour change was declared "restores the old behaviour" before checking what the old version did with the same flag | Compare **both** versions' bytecode for the path in question |

## Build variants and the IDE

| Trap | Rule |
|---|---|
| NewRelic's plugin sets `excludeVariantInstrumentation("debug")` - a debug build never runs its transform | Tell the user which variant exercises the change (here: staging) |
| AGP 9.4.0 built and tested clean from the CLI; Android Studio refused to sync ("Latest supported version is AGP 9.2.1") | Android Studio caps AGP; ask for the IDE version before choosing AGP. If a newer Studio would lift the cap, ask the user to update - their choice - rather than pinning silently |
| Kotlin 2.4 is published but no KSP is built for it, and KSP drives Room and Hilt | KSP caps Kotlin |
| NewRelic 7.7.4 capped AGP at 9.0.0; it stayed pinned after AGP moved to 9.2.1, silently out of range | Plugins carry their own AGP window - check it (`BuildHelper` constants) |
| AGP held at 9.4.0 while Studio allowed 9.4.1; the only reason was NewRelic's warn-only ceiling, and it was never stated or tested | A version below latest needs its constraint named, hard or soft; test soft ones instead of assuming |
| A debug build and green unit tests were treated as proof an upgrade was safe, but R8 only runs on staging/release | The wrap-up staging / R8 gate: `r8_check.py compare baseline` after the user builds staging |
| A patch batch ignored a coupled set (`Moves with the other three`) and a recorded blocker | `batch.py plan` reads the catalog comments and leaves those out; never override it |
| A green device build was reported as "verified" for MoEngage and AppsFlyer | A build proves it installs. Campaigns and attribution need their dashboards |

## targetSdk

| Trap | Rule |
|---|---|
| Edge-to-edge and `elegantTextHeight` were presented as API 36 risks - both were already enforced at 35 | For every item on the new level's behaviour-changes page, check the previous level's page too |
| Robolectric's SDK 36 sandbox needs Java 21; the project builds on Java 17 | Pin the sandbox with `robolectric.properties` `sdk=35` until the test toolchain moves |
| Three inset fixes were bundled in that were pre-existing bugs, not targetSdk fallout | Only change what the new level actually requires |

## Editing files

| Trap | Rule | Enforced by |
|---|---|---|
| A comment edit twice left `compose_bom = "2026.09.00" blocked: …` - a new value and no `#` | Validate after every edit, including comment-only ones | `validate_catalog.py` |
| `newrelic = ` matched both the version line and the library alias | Anchor edits to the full line; assert scope | `validate_catalog.py --expect` |
| Three columns were inserted into the tracker and an index-based writer overwrote two cells | Resolve columns by header name | `tracker.py` |
| Helper scripts lived in the session scratchpad, which was wiped overnight | State lives in the tracker (repo root) and `build/lib-upgrade/` | `tracker.py`, `run_gradle.sh` |
| `supply/profile` hardcoded `moe-android-sdk:13.06.00` outside the catalog | Flag hardcoded coordinates | `modules.py consumers` |
