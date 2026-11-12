# Catalog comments - policy and enforcement

_Examples in this file come from the project the skill was first written for; the rules are general._

The comments next to the libs in `gradle/libs.versions.toml` must be true after **every**
upgrade. This is enforced by a script, not left to judgement, because the judgement-based rule
("sweep for stale comments") already existed and one session still shipped four stale ones.

## Policy

1. **Every blocked, capped or held ref says why**, naming the exact blocker with version
   numbers: `# 3.5.0 blocked: declares kotlin-stdlib 2.4.0 ... Needs Kotlin 2.4, which has no KSP.`
   Not `# blocked`, not `# do not bump`.
2. **Every ref the code depends on points at that dependency.** The MoEngage entry names the
   `disableIntegrationValidator()` guard in `RiderApp.onCreate` and says not to remove it -
   someone editing the version must see it.
3. **After every bump, run the checker and resolve every finding** - update the comment, or
   confirm it is still accurate and pass the key to `--ack`. No commit message until it is clean.
4. **When a gate value moves** (compileSdk, Kotlin, AGP, KSP, Gradle), re-screen every blocked row
   (`screen_gates.py --blocked`) and update any comment whose blocker cleared.
5. **Write comments that can be checked.** State the project value you depend on
   (`project is on 2.3.20`, `compiles against 36`) and the condition you are waiting for
   (`Retry when compileSdk 37 lands`). The checker verifies both forms. Never cite a line number.

## Running it

```bash
python3 $S/check_comments.py            # after a bump: tree vs HEAD
python3 $S/check_comments.py --audit    # standing sweep
python3 $S/check_comments.py --ack glide  # glide confirmed accurate
```

## The checks, and the real comment each one exists for

| Check | Catches | Real example |
|---|---|---|
| **OWN** | a ref's value moved but its comment did not | any bump that leaves `# 1.6.0 blocked` on a ref now at 1.6.0 |
| **MENTION** | another comment names the bumped ref, or cites the toolchain value it replaced - grep the *claim*, not just the edited line | `map_utils`: "Retry after **kotlin_plugin** moves" / "project is on **2.2.20**" |
| **CLAIM** | "project is on X", "compiles against N", "we are X" where X is not a current platform value | `maps_compose`, `inbox_ui`: "project is on 2.2.20" after Kotlin went to 2.3.20 |
| **CONDITION** | "retry after / until / re-check before / when … lands" whose subject moved since the comment was written (dated with `git blame`) | NewRelic "Pinned until … verified for any AGP/Gradle upgrade" after AGP 9.2.1; `slack-compose-lint-checks` "Retry after the AGP/Kotlin bump" |
| **RELATION** | `lockstep with <ref>`, `matches <bom> exactly` (checked against the BOM's own POM), `key = "v"`, `key version (v)` | `mockk = "1.14.5"` (it is 1.14.11); `lifecycle version (2.8.7)` (it is 2.10.0) |
| **LINEREF** | `(line N)` that no longer points at the key it names | "(line 64)" - the key is on line 65; "(line 248)" - it is on 269 |
| **TRACKER** | tracker and catalog drifting apart | `okhttp3` blocked in the catalog but absent from the tracker; `agp` "done at 9.4.0" in the tracker while the catalog is at 9.2.1 |

## Proven against history

The checker was validated on the project this skill was first written for, by replaying a
toolchain commit where five comments went stale at once: it caught all five, most through
several independent checks, with no false positives. If you change the checker, replay it on a
commit pair of your own where you know which comments went stale, and confirm it still flags
them:

```bash
python3 $S/check_comments.py --base <commit before>~0 --head <the toolchain commit>
```

## Writing a good blocked comment

```toml
coil = "3.4.0" # 3.5.0 blocked: declares kotlin-stdlib 2.4.0 ... Needs Kotlin 2.4, which has no KSP. 3.6.0+ also need compileSdk 37.
```

Keep the whole comment on the entry's own line. A continuation `#` line below it parses as a
separate standalone comment, so the checker cannot tie it to the ref. Say what was tried, the
exact blocker and its numbers, and what would unblock it. Keep the current version, and never
downgrade below what is committed.
