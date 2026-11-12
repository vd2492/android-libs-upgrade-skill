"""Shared parsing of the project's Gradle version catalog for the lib-upgrade scripts.

Parses line by line instead of with a TOML library so that comments - which a TOML parser
throws away, and which are half of what these scripts check - survive, and so that any git
revision's copy can be parsed from a string.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass, field

DEFAULT_CATALOG = "gradle/libs.versions.toml"
WRAPPER = "gradle/wrapper/gradle-wrapper.properties"
CONFIG_FILE = "lib-upgrade.config.json"

# Catalog keys that make up the platform (Part 1 of the skill) are recognised by NAME, ignoring
# case and the - _ . separators, so `agp`, `androidGradlePlugin` and `android-gradle-plugin` all
# count. Several keys can feed one dimension (kotlin_plugin and jetbrain_kotlin are both
# "kotlin"). A project whose names differ maps them in lib-upgrade.config.json:
#   {"toolchainKeys": {"agp": "myAgpKey", "kotlin": "myKotlinKey"}}
KEY_ALIASES = {
    "agp": {"agp", "androidgradleplugin", "androidplugin", "androidbuildgradle"},
    "kotlin": {"kotlin", "kotlinplugin", "kotlinversion", "kotlingradleplugin", "kotlinjvm",
               "kotlinandroid", "jetbrainkotlin"},
    "ksp": {"ksp", "kspversion", "googleksp"},
    "compileSdk": {"compilesdk", "compilesdkversion", "compilesdkapi"},
    "targetSdk": {"targetsdk", "targetsdkversion", "targetsdkapi"},
    "minSdk": {"minsdk", "minsdkversion", "minsdkapi"},
}
# When no key matches by name, the same dimensions are read off a plugin or library id.
PLUGIN_IDS = {
    "agp": ("com.android.application", "com.android.library", "com.android.tools.build:gradle"),
    "kotlin": ("org.jetbrains.kotlin.android", "org.jetbrains.kotlin:kotlin-gradle-plugin"),
    "ksp": ("com.google.devtools.ksp", "com.google.devtools.ksp:com.google.devtools.ksp.gradle.plugin"),
}


def _norm(key: str) -> str:
    return re.sub(r"[-_.]", "", key).lower()


def resolve_keys(keys, overrides: dict | None = None) -> dict[str, str]:
    """{catalog key: dimension} for the keys that make up the platform."""
    out: dict[str, str] = {}
    for dim, key in (overrides or {}).items():
        if key in keys:
            out[key] = dim
    for key in keys:
        if key in out:
            continue
        for dim, names in KEY_ALIASES.items():
            if _norm(key) in names:
                out[key] = dim
    return out


class _LazyToolchainKeys:
    """`key in TOOLCHAIN_KEYS` / `.items()` for the catalog of the repo being worked on."""

    def _map(self) -> dict[str, str]:
        try:
            return resolve_keys(load().versions.keys(), config().get("toolchainKeys"))
        except SystemExit:
            return {}

    def __contains__(self, k): return k in self._map()
    def __iter__(self): return iter(self._map())
    def __getitem__(self, k): return self._map()[k]
    def get(self, k, default=None): return self._map().get(k, default)
    def items(self): return self._map().items()


TOOLCHAIN_KEYS = _LazyToolchainKeys()

VERSION_LINE = re.compile(r"""^([A-Za-z0-9_.\-]+)\s*=\s*(['"])([^'"]*)\2\s*(?:#\s?(.*))?$""")
KEY_ONLY = re.compile(r"^([A-Za-z0-9_.\-]+)\s*=")


@dataclass
class Version:
    key: str
    value: str
    comment: str
    line: int


@dataclass
class Artifact:
    alias: str
    group: str
    name: str
    version_ref: str | None
    version: str | None
    is_plugin: bool = False


@dataclass
class Catalog:
    versions: dict[str, Version] = field(default_factory=dict)
    libraries: dict[str, Artifact] = field(default_factory=dict)
    plugins: dict[str, Artifact] = field(default_factory=dict)
    bundles: dict[str, list[str]] = field(default_factory=dict)
    standalone_comments: list[tuple[int, str, str]] = field(default_factory=list)
    malformed: list[tuple[int, str]] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    rev: str | None = None   # git revision this copy was read at; None = the working tree

    def artifacts_for(self, ref: str) -> list[Artifact]:
        return [a for a in list(self.libraries.values()) + list(self.plugins.values())
                if a.version_ref == ref]

    def toolchain(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, dim in resolve_keys(self.versions.keys(), config().get("toolchainKeys")).items():
            if dim not in out:
                out[dim] = self.versions[key].value
        # No key matched by name: read the dimension off a plugin / library id instead.
        for dim, ids in PLUGIN_IDS.items():
            if dim in out:
                continue
            for art in list(self.plugins.values()) + list(self.libraries.values()):
                ident = art.group if art.is_plugin else f"{art.group}:{art.name}"
                ref = art.version_ref
                if ident in ids and ref in self.versions:
                    out[dim] = self.versions[ref].value
                    break
        if self.rev is None:  # SDK levels often live in a build file, not the catalog
            for dim, value in sdk_from_build_files().items():
                out.setdefault(dim, value)
        return out


def _inline_table(body: str) -> dict[str, str]:
    """Parse `{ module = "g:a", version.ref = "x" }` into a flat dict."""
    return dict(re.findall(r"""([A-Za-z.]+)\s*=\s*['"]([^'"]*)['"]""", body))


def parse(text: str) -> Catalog:
    cat = Catalog(lines=text.split("\n"))
    section = None
    bundle_name, bundle_buf = None, ""
    for i, raw in enumerate(cat.lines, 1):
        line = raw.strip()
        if bundle_name is not None:  # continuing a multi-line bundle array
            bundle_buf += " " + line
            if "]" in line:
                cat.bundles[bundle_name] = re.findall(r"""['"]([^'"]+)['"]""", bundle_buf)
                bundle_name = None
            continue
        if not line:
            continue
        if line.startswith("["):
            section = line
            continue
        if line.startswith("#"):
            cat.standalone_comments.append((i, section or "", line.lstrip("#").strip()))
            continue
        if section == "[versions]":
            m = VERSION_LINE.match(line)
            if not m:
                cat.malformed.append((i, raw))
                continue
            key, _, value, comment = m.groups()
            if key in cat.versions:
                cat.duplicate_keys.append(key)
            cat.versions[key] = Version(key, value, (comment or "").strip(), i)
        elif section in ("[libraries]", "[plugins]"):
            km = KEY_ONLY.match(line)
            if not km:
                continue
            alias = km.group(1)
            rhs = line.split("=", 1)[1].split("#", 1)[0].strip()
            is_plugin = section == "[plugins]"
            if rhs.startswith("{"):
                t = _inline_table(rhs)
                if is_plugin:
                    group, name = t.get("id", ""), ""
                elif "module" in t:
                    group, _, name = t["module"].partition(":")
                else:
                    group, name = t.get("group", ""), t.get("name", "")
                art = Artifact(alias, group, name, t.get("version.ref"), t.get("version"), is_plugin)
            else:
                parts = rhs.strip("'\"").split(":")
                if is_plugin:
                    art = Artifact(alias, parts[0], "", None, parts[1] if len(parts) > 1 else None, True)
                else:
                    art = Artifact(alias, parts[0], parts[1] if len(parts) > 1 else "", None,
                                   parts[2] if len(parts) > 2 else None, False)
            (cat.plugins if is_plugin else cat.libraries)[alias] = art
        elif section == "[bundles]":
            km = KEY_ONLY.match(line)
            if not km:
                continue
            body = line.split("=", 1)[1]
            if "]" in body:
                cat.bundles[km.group(1)] = re.findall(r"""['"]([^'"]+)['"]""", body)
            else:
                bundle_name, bundle_buf = km.group(1), body
    return cat


def repo_root() -> str:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return out.stdout.strip() or os.getcwd()


def check_rev(rev: str | None) -> None:
    """Refuse a revision git would read as an option (`--output=...` writes files)."""
    if rev is not None and (rev.startswith("-") or "\n" in rev or "\0" in rev):
        raise SystemExit(f"invalid git revision {rev!r}")


def safe_name(name: str) -> str:
    """A run / snapshot name that cannot escape build/lib-upgrade/ (it becomes part of a file name)."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name or ""):
        raise SystemExit(f"invalid name {name!r}: use letters, digits, . _ - and start with a letter or digit")
    return name


def read_file_at(path: str, rev: str | None) -> str:
    """Contents of `path` at git revision `rev`, or the working tree when rev is None."""
    check_rev(rev)
    if rev is None:
        with open(os.path.join(repo_root(), path), encoding="utf-8") as fh:
            return fh.read()
    out = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True,
                         cwd=repo_root())
    if out.returncode != 0:
        raise SystemExit(f"cannot read {path} at {rev}: {out.stderr.strip()}")
    return out.stdout


def config() -> dict:
    """Optional lib-upgrade.config.json at the repo root: per-project overrides, all optional."""
    try:
        with open(os.path.join(repo_root(), CONFIG_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def catalog_path() -> str:
    """Repo-relative path of the version catalog: config, then the standard location, then a search."""
    root = repo_root()
    configured = config().get("catalog")
    if configured:
        return configured
    if os.path.exists(os.path.join(root, DEFAULT_CATALOG)):
        return DEFAULT_CATALOG
    found = sorted(glob.glob(os.path.join(root, "gradle", "*.versions.toml")))
    if not found:
        found = sorted(p for p in glob.glob(os.path.join(root, "**", "libs.versions.toml"), recursive=True)
                       if "/build/" not in p and "node_modules" not in p)
    if found:
        return os.path.relpath(found[0], root)
    raise SystemExit("no Gradle version catalog found (looked for gradle/libs.versions.toml and any "
                     "*.versions.toml). This skill reads a version catalog; set \"catalog\" in "
                     f"{CONFIG_FILE} if it lives elsewhere, or migrate the project to one first.")


SDK_PATTERNS = {
    "compileSdk": r"compileSdk(?:Version)?\s*(?:=|\()?\s*(?:release\()?\s*['\"]?(\d+)",
    "targetSdk": r"targetSdk(?:Version)?\s*(?:=|\()?\s*['\"]?(\d+)",
    "minSdk": r"minSdk(?:Version)?\s*(?:=|\()?\s*['\"]?(\d+)",
}


def sdk_from_build_files() -> dict[str, str]:
    """compileSdk / targetSdk / minSdk found in build files, for projects that keep them out of the catalog."""
    root = repo_root()
    files = (glob.glob(os.path.join(root, "gradle.properties"))
             + glob.glob(os.path.join(root, "*", "build.gradle*"))
             + glob.glob(os.path.join(root, "build-logic", "**", "*.kt"), recursive=True)
             + glob.glob(os.path.join(root, "build-logic", "**", "*.gradle*"), recursive=True)
             + glob.glob(os.path.join(root, "buildSrc", "**", "*.kt"), recursive=True))
    out: dict[str, str] = {}

    def priority(f: str) -> tuple:   # gradle.properties, then app/, then the rest - and always sorted
        rel = os.path.relpath(f, root)
        return (0 if rel == "gradle.properties" else 1 if rel.startswith("app" + os.sep) else 2, rel)

    for f in sorted(set(files), key=priority):
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for dim, pat in SDK_PATTERNS.items():
            if dim not in out:
                m = re.search(pat, text)
                if m:
                    out[dim] = m.group(1)
    return out


def load(rev: str | None = None) -> Catalog:
    cat = parse(read_file_at(catalog_path(), rev))
    cat.rev = rev
    return cat


def gradle_version(rev: str | None = None) -> str | None:
    try:
        text = read_file_at(WRAPPER, rev)
    except (SystemExit, FileNotFoundError):
        return None
    m = re.search(r"gradle-([0-9][^-/]*)-(?:bin|all)\.zip", text)
    return m.group(1) if m else None


def platform(rev: str | None = None) -> dict[str, str]:
    """Every platform dimension at `rev`: agp, kotlin, ksp, compileSdk, targetSdk, minSdk, gradle."""
    p = load(rev).toolchain()
    g = gradle_version(rev)
    if g:
        p["gradle"] = g
    return p


def accessor(alias: str) -> str:
    """Gradle's type-safe accessor for a catalog alias: foo-bar_baz -> foo.bar.baz."""
    return re.sub(r"[-_.]", ".", alias)
