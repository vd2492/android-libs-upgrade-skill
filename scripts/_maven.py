"""Shared Maven repository access for the lib-upgrade scripts.

Version lists always come from maven-metadata.xml, never from a search API: the first scan
this workflow ran used Maven Central's Solr search with rows=40, which returns an arbitrary
window of versions and silently reported 11 outdated libraries as current.
"""
from __future__ import annotations

import http.client
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from functools import lru_cache

REPOS = [
    "https://dl.google.com/dl/android/maven2",
    "https://repo1.maven.org/maven2",
    "https://plugins.gradle.org/m2",
    "https://jitpack.io",
]
# A pre-release marker is a whole word (or word + digits): "rc1", "-beta-2", "M3", "dev-1234".
# Not a substring: "2.0.0-src", "1.0.0-device" and "3.1-arch" are stable releases.
UNSTABLE = re.compile(r"(?<![a-z])(?:alpha|beta|rc|cr|dev|eap|snapshot|preview|milestone|prerelease|pre|m)(?![a-z])",
                      re.IGNORECASE)
AAR_METADATA = "META-INF/com/android/build/gradle/aar-metadata.properties"


class Unreachable(Exception):
    """A repository could not be reached - NOT the same as the artifact not existing.

    Without this distinction an offline run reads as "already the latest stable" for every library.
    """


MAX_BYTES = 200 * 1024 * 1024      # refuse absurd downloads instead of buffering them
CACHE_MAX_BODY = 1024 * 1024       # larger bodies (AARs) are not kept: the cache must not grow to GBs
_CACHE: dict[str, bytes | None] = {}
_FAILED: set[str] = set()          # URLs whose last attempt failed for a transient reason
_LOCK = threading.Lock()
RETRIES = 2


def failed(url: str) -> bool:
    """True when the last fetch of `url` failed transiently (so None means 'unknown', not 'absent')."""
    return url in _FAILED


def fetch(url: str, timeout: int = 40) -> bytes | None:
    """Body of `url`, or None. A definitive miss (404) is cached; a transient failure is retried,
    recorded in failed(), and never cached - one dropped connection must not poison the whole run."""
    with _LOCK:
        if url in _CACHE:
            return _CACHE[url]
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lib-upgrade-skill"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                body = None  # definitive: too large to be a catalog artifact
            with _LOCK:
                _FAILED.discard(url)
                if body is None or len(body) <= CACHE_MAX_BODY:
                    _CACHE[url] = body
            return body
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:   # 404, 403, ... - the server answered: definitive
                with _LOCK:
                    _FAILED.discard(url)
                    _CACHE[url] = None
                return None
        # IncompleteRead and friends are HTTPException, not OSError; uncaught, one dropped
        # download aborts the whole thread-pooled scan.
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
            pass
        if attempt < RETRIES:
            time.sleep(0.5 * (attempt + 1))
    with _LOCK:
        _FAILED.add(url)
    return None


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        _FAILED.clear()
    versions.cache_clear()


def path(group: str, artifact: str) -> str:
    return f"{group.replace('.', '/')}/{artifact}"


def plugin_marker(plugin_id: str) -> tuple[str, str]:
    """A Gradle plugin id resolves through its marker artifact <id>:<id>.gradle.plugin."""
    return plugin_id, f"{plugin_id}.gradle.plugin"


@lru_cache(maxsize=None)
def versions(group: str, artifact: str) -> tuple[str | None, list[str]]:
    """(repo, all published versions) from the first repo that has maven-metadata.xml.

    Raises Unreachable when nothing was found AND a repository could not be reached: an empty
    list then means "unknown", and must not be reported as "no newer version". (Exceptions are
    not cached by lru_cache, so a later call retries.)
    """
    unreachable = []
    for repo in REPOS:
        url = f"{repo}/{path(group, artifact)}/maven-metadata.xml"
        raw = fetch(url)
        if raw:
            found = re.findall(r"<version>([^<]+)</version>", raw.decode("utf-8", "replace"))
            if found:
                return repo, found
        elif failed(url):
            unreachable.append(repo)
    if unreachable:
        raise Unreachable(f"could not reach {', '.join(unreachable)} while looking up {group}:{artifact}")
    return None, []


def is_stable(v: str) -> bool:
    return not UNSTABLE.search(v)


def flavour(v: str) -> str:
    """Non-numeric suffix that names a variant rather than a release stage: 33.7.1-android -> 'android'.

    guava publishes -android and -jre side by side; they are different artifacts, not different
    versions, so a candidate must keep the current version's flavour.
    """
    m = re.match(r"^[\d.]+-([A-Za-z]+)$", v)
    return m.group(1).lower() if m else ""


def nums(v: str) -> tuple:
    """The numeric release components exactly as written: '1.0' -> (1, 0), '1.0.0' -> (1, 0, 0)."""
    return tuple(int(n) for n in re.findall(r"\d+", re.split(r"[-+]", v, maxsplit=1)[0]))


def key(v: str) -> tuple:
    """Numeric-aware sort key. Trailing zeros are dropped so 1.0 == 1.0.0 (not 'newer')."""
    n = list(nums(v))
    while n and n[-1] == 0:
        n.pop()
    return tuple(n)


def stable_candidates(all_versions: list[str], current: str) -> list[str]:
    """Stable versions newer than `current`, same flavour, oldest first."""
    fl = flavour(current)
    cur = key(current)
    out = [v for v in all_versions if is_stable(v) and flavour(v) == fl and key(v) > cur]
    return sorted(set(out), key=key)


def tier(current: str, target: str) -> str:
    """T1 patch, T2 minor, T3 major. A 0.x minor bump is treated as major (semver)."""
    a, b = nums(current), nums(target)
    if not a or not b:
        return "T3"
    if a[0] != b[0]:
        return "T3"
    if a[0] == 0 and len(a) > 1 and len(b) > 1 and a[1] != b[1]:
        return "T3"
    if len(a) > 1 and len(b) > 1 and a[1] != b[1]:
        return "T2"
    return "T1"


def artifact_url(repo: str, group: str, artifact: str, v: str, ext: str) -> str:
    return f"{repo}/{path(group, artifact)}/{v}/{artifact}-{v}.{ext}"


def _first(group: str, artifact: str, v: str, ext: str) -> bytes | None:
    for repo in REPOS:
        raw = fetch(artifact_url(repo, group, artifact, v, ext))
        if raw:
            return raw
    return None


def aar_metadata(group: str, artifact: str, v: str) -> dict:
    """minCompileSdk and friends from the AAR, with an explicit reason when absent.

    'none' (a valid AAR with no metadata file, i.e. unconstrained) must never be confused with
    'download-failed' (unknown): an empty probe looks identical to the first unless the two are
    told apart, and that mistake nearly reported play-services-ads as unscreened.
    """
    raw = _first(group, artifact, v, "aar")
    if raw is None:
        if _first(group, artifact, v, "jar") is not None:
            return {"status": "jar"}  # plain JAR: no AAR metadata can exist
        return {"status": "download-failed"}
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return {"status": "download-failed"}
    if AAR_METADATA not in zf.namelist():
        return {"status": "none"}
    props = dict(line.split("=", 1) for line in zf.read(AAR_METADATA).decode().splitlines()
                 if "=" in line)
    return {"status": "ok", **props}


def android_variant(group: str, artifact: str, v: str) -> tuple[str, str, str]:
    """Coordinates that actually ship the Android code for group:artifact:v.

    A Kotlin Multiplatform root (coil-compose) publishes no AAR; its module metadata delegates
    the androidJvm variant via `available-at` (-> coil-compose-android). Screening the root
    instead reads as "download failed" and misses the kotlin-stdlib 2.4.0 that broke the build.
    """
    raw = _first(group, artifact, v, "module")
    if raw:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            doc = {}
        for var in doc.get("variants", []):
            at = var.get("available-at")
            platform = var.get("attributes", {}).get("org.jetbrains.kotlin.platform.type")
            if at and platform == "androidJvm":
                return at["group"], at["module"], at["version"]
    return group, artifact, v


def packaging(group: str, artifact: str, v: str) -> str | None:
    pom = _first(group, artifact, v, "pom")
    if not pom:
        return None
    m = re.search(r"<packaging>([^<]+)</packaging>", pom.decode("utf-8", "replace"))
    return m.group(1).strip() if m else "jar"


def bom_managed(group: str, artifact: str, v: str) -> list[tuple[str, str, str]]:
    """(group, artifact, version) entries a BOM's dependencyManagement pins.

    A BOM is POM-only - no AAR, no JAR - so screening it directly reads as "download failed".
    Its gates are really its members' gates.
    """
    pom = _first(group, artifact, v, "pom")
    if not pom:
        return []
    text = pom.decode("utf-8", "replace")
    dm = re.search(r"<dependencyManagement>(.*?)</dependencyManagement>", text, re.S)
    out = []
    for block in re.findall(r"<dependency>(.*?)</dependency>", dm.group(1) if dm else "", re.S):
        g = re.search(r"<groupId>([^<]+)</groupId>", block)
        a = re.search(r"<artifactId>([^<]+)</artifactId>", block)
        ver = re.search(r"<version>([^<]+)</version>", block)
        if g and a and ver:
            out.append((g.group(1), a.group(1), ver.group(1)))
    return out


def dependencies(group: str, artifact: str, v: str) -> list[dict]:
    """Declared dependencies from Gradle module metadata, falling back to the POM."""
    raw = _first(group, artifact, v, "module")
    deps = []
    if raw:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            doc = {}
        seen = set()
        for var in doc.get("variants", []):
            for d in var.get("dependencies", []) + var.get("dependencyConstraints", []):
                ver = d.get("version", {}) or {}
                item = (d.get("group"), d.get("module"),
                        ver.get("strictly") or ver.get("requires") or ver.get("prefers"))
                if item not in seen:
                    seen.add(item)
                    deps.append({"group": item[0], "module": item[1], "version": item[2]})
        if deps:
            return deps
    pom = _first(group, artifact, v, "pom")
    if pom:
        text = pom.decode("utf-8", "replace")
        props = dict(re.findall(r"<([\w.\-]+)>([^<$]*)</\1>",
                                (re.search(r"<properties>(.*?)</properties>", text, re.S) or [None, ""])[1]))
        props.update({"project.version": v, "version": v})
        body = re.sub(r"<dependencyManagement>.*?</dependencyManagement>", "", text, flags=re.S)
        for block in re.findall(r"<dependency>(.*?)</dependency>", body, re.S):
            g = re.search(r"<groupId>([^<]+)</groupId>", block)
            a = re.search(r"<artifactId>([^<]+)</artifactId>", block)
            ver = re.search(r"<version>([^<]+)</version>", block)
            scope = re.search(r"<scope>([^<]+)</scope>", block)
            if scope and scope.group(1).strip() in ("test", "provided", "system"):
                continue   # not on the consumer's runtime classpath
            if g and a:
                version = ver.group(1).strip() if ver else None
                if version and version.startswith("${"):   # resolve ${prop} from this POM's <properties>
                    version = props.get(version[2:-1]) or version
                deps.append({"group": g.group(1), "module": a.group(1), "version": version})
    return deps


def _concrete(version: str | None) -> bool:
    """A version we can compare: starts with a digit (not '${prop}', not a '[1.0,2.0)' range)."""
    return bool(version) and version[0].isdigit()


def kotlin_stdlib_declared(group: str, artifact: str, v: str) -> tuple[list[str], bool]:
    """(concrete kotlin-stdlib versions declared, whether one was declared that cannot be read).

    The second value must make the gate UNKNOWN: a stdlib declared through a parent POM, a BOM or
    an unresolved ${property} has a version this script cannot see, and reading that as
    "declares none" would let exactly the dangerous artifact pass.
    """
    found, unresolved = set(), False
    for d in dependencies(group, artifact, v):
        if d["group"] == "org.jetbrains.kotlin" and (d["module"] or "").startswith("kotlin-stdlib"):
            if _concrete(d["version"]):
                found.add(d["version"])
            else:
                unresolved = True
    return sorted(found), unresolved


def kotlin_stdlib(group: str, artifact: str, v: str) -> list[str]:
    """kotlin-stdlib versions an artifact declares (empty when it declares none)."""
    return kotlin_stdlib_declared(group, artifact, v)[0]


def release_notes_url(group: str, artifact: str, v: str) -> str:
    """Best official changelog link: known Google pages, else the POM's GitHub /releases."""
    if group.startswith("androidx."):
        return f"https://developer.android.com/jetpack/androidx/releases/{group.split('.')[1]}"
    if group.startswith("com.google.firebase"):
        return "https://firebase.google.com/support/release-notes/android"
    if group.startswith("com.google.android.gms"):
        return "https://developers.google.com/android/guides/releases"
    pom = _first(group, artifact, v, "pom")
    if pom:
        text = pom.decode("utf-8", "replace")
        scm = re.search(r"<scm>(.*?)</scm>", text, re.S)
        text = (scm.group(1) if scm else "") + text  # the project's own repo before any developer url
        for url in re.findall(r"<url>\s*([^<\s]+)\s*</url>", text):
            gh = re.match(r"(?:scm:git:)?(https?://github\.com/[^/]+/[^/.]+)", url)
            if gh:
                return gh.group(1) + "/releases"
    return f"https://mvnrepository.com/artifact/{group}/{artifact}"
