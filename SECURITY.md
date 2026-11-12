# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub:
**Security tab → Report a vulnerability** on this repository
(https://github.com/vd2492/android-libs-upgrade-skill/security/advisories/new).

Do not open a public issue for a vulnerability. Include what you found, how to reproduce it, and
the impact you expect. Reports are handled on a best-effort basis.

## Scope

This repository is a Claude Code skill: Markdown instructions plus Python and shell scripts that
read a project's Gradle version catalog and query public Maven repositories. Relevant issues
include unsafe handling of untrusted input (catalog files, repository metadata, downloaded
archives), unexpected file writes, and anything that could make the skill act on instructions
found in web content instead of from the user.

## Supported versions

Only the latest commit on `main` is supported.
