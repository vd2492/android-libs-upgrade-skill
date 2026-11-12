# The two gates

Screen every candidate **before** editing the catalog. A bump that fails a gate either fails to
build or - worse - builds, passes unit tests, and fails somewhere far from its cause.

```bash
python3 $S/screen_gates.py <ref> --bisect
```

## Gate 1 - minCompileSdk

Every AAR carries `META-INF/com/android/build/gradle/aar-metadata.properties`. Its
`minCompileSdk` must be ≤ the project's `compileSdk`. Violations fail
`checkDebugAarMetadata` - loud and early.

compileSdk 37 is this gate: it blocks `core_ktx` 1.19.0 (52 modules), the Compose 1.12 family,
lifecycle/navigation 2.11/2.10, coil 3.6.x and glide 5.0.9.

## Gate 2 - declared kotlin-stdlib

The artifact's module metadata (or POM) declares a `kotlin-stdlib` version. Its **major.minor
line** must be ≤ the project's Kotlin. A patch difference on the same line is fine: hilt 2.60.1
declares 2.3.21 against our 2.3.20 - same 2.3 metadata line, no problem.

This gate is dangerous because it fails far from its cause. coil 3.5.0 declared
kotlin-stdlib **2.4.0**. It compiled, passed 2,902 unit tests, then broke
`:app:hiltJavaCompileDebug` on the device build:

> `Provided Metadata instance has version 2.4.0, while maximum supported version is 2.3.0`

AGP's *consistent resolution* mirrors the runtime classpath onto the compile classpath, so one
library's 2.4 stdlib dragged the whole app onto it. `kotlin.Metadata` from a 2.4 stdlib is
metadata v2.4.0, and Hilt's shaded `kotlin-metadata-jvm` reads only up to 2.3.0. Diagnose with:

```bash
./gradlew :app:dependencyInsight --configuration debugRuntimeClasspath \
  --dependency org.jetbrains.kotlin:kotlin-stdlib --console=plain -q | head -40
```

## What the screener handles for you (each was a real miss)

| Case | Why it matters |
|---|---|
| **Kotlin Multiplatform roots** (`coil-compose`) | No AAR at the root - screening it reads "download failed". The screener follows `available-at` to the `-android` variant. Without this, coil's gate-2 failure is invisible. |
| **BOMs** (`compose-bom`) | POM-only. The screener opens `dependencyManagement` and screens the members this catalog uses. |
| **Family constraints** (AndroidX) | `navigation` 2.10.1 passes alone but constrains `navigation-compose` to 2.10.1 (compileSdk 37). Holding the other ref back does **not** help. Same-group constraints are screened too. |
| **"No metadata" vs "download failed"** | A valid AAR with no metadata file is *unconstrained* (play-services-ads). A failed download is *unknown*. They look identical in a naive probe. |
| **Plain JARs** (guava) | No AAR metadata can exist - gate 1 is n/a. |
| **Flavour suffixes** | guava `-android` and `-jre` are different artifacts, not different versions. Candidates keep the current flavour. |

## Highest that works - always bisect

The policy is **take the highest version that works; never go below what is committed.** "The
latest is blocked" is not "no upgrade". `--bisect` screens every stable candidate newest-first.

The first session got this wrong for four refs by recording the latest as blocked and stopping.
A later re-screen found `core_ktx` 1.16.0 → 1.18.0, `compose_bom` 2026.05.00 → 2026.06.01
(Compose 1.11.4), `map_utils` 3.8.2 → 4.5.2 and `maps_compose` 4.4.1 → 8.3.0 all available
on the same toolchain.

A ref whose artifacts' latest versions disagree is **diverged** (`litert` reached 2.2.0,
`litert-support-api` stopped at 1.4.2) and cannot be bumped as one ref - it needs splitting.

## Re-screen blocked rows whenever a gate value moves

A comment can be perfectly self-consistent and still false: `moengage_inbox_ui` said "blocked on
Kotlin" long after the Kotlin bump cleared it. After any change to compileSdk, Kotlin, AGP, KSP
or Gradle:

```bash
python3 $S/screen_gates.py --blocked
```

It sorts every blocked row into **CLEARED** (the target now passes), **PARTIAL** (a lower version
passes - take it), **NOT A GATE** (blocked for another reason - code change, size, API removal -
re-check that reason) or **STILL BLOCKED**.

## What a gate cannot tell you

Gates prove a version *can* be on the classpath. They say nothing about behaviour. For majors,
also run `api_diff.py` and read the changelog; see `libs-update.md`.
