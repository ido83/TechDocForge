---
name: techdocforge-github-actions-ci
description: "Use when creating, reviewing, or fixing TechDocForge GitHub Actions CI workflows for dependency scanning, static analysis, secret scanning, linting, or tests."
---

# GitHub Actions CI

## Workflow design

- Inspect existing workflows, dependency manifests, and build scripts before choosing commands. Extend the existing pull request checks where possible. For a new workflow, put it under `.github/workflows/` and run checks on pull requests and pushes to the repository's default branch. Use separate, clearly named jobs so one failure leaves the other results visible.
- If a job needs more than one line of shell commands, put its check steps in a local composite action at `.github/actions/<check>/action.yml` and call it from CI after checkout. Keep job settings and checkout in the workflow. Declare `shell` for each composite `run` step and pass event data through action inputs and environment variables.
- Use `pull_request` for checks that execute PR code. Use GitHub-hosted runners for untrusted contributions, start with `permissions: contents: read`, and disable persisted checkout credentials when the job only reads source. Keep secrets and write tokens out of PR jobs. Keep untrusted event fields out of inline shell expressions.
- Pin third-party Actions to verified full commit SHAs and annotate the release version. Pin CI tool versions and verify the checksum of downloaded executables when available. Keep privileged publishing or report uploads in separately authorized jobs.

## Required checks

| Check | TechDocForge starting point |
| --- | --- |
| Software composition analysis (SCA) | Use `pip-audit`. Audit the resolved runtime dependency set, including transitive dependencies, using the production lock when available. Audit development and build dependencies too when CI installs or executes them. Keep the audited versions aligned with the versions installed by CI. |
| Static application security testing (SAST) | Reuse `bandit -r techdocforge`. Keep exclusions specific and explain any suppression next to the affected code or in the scanner configuration. |
| Secret scanning | Use Gitleaks with full redaction. On PRs, scan commits introduced by the PR, including changes later removed by another commit. Fetch enough history to resolve and scan the intended range. Reserve full-history scans for a deliberate baseline or scheduled job. Exclude Git metadata from generic filesystem scans and artifacts. Keep only redacted findings in logs or reports. |
| Lint | Use the repository's configured Python linter, or Ruff if introducing one. Check Python files and lint workflow YAML with actionlint. Run checks in read-only mode and keep unrelated formatting changes out of the PR. |

Run `python -m pytest` alongside the four checks when CI covers Python changes. Use `QT_QPA_PLATFORM=offscreen` for headless Qt tests and provision the runner's required Qt libraries.

## Verification and reporting

- Run each check with the intended scope and preserve its exit status. Distinguish findings from scanner or network failures. Keep any approved exceptions narrow, with a reason and a review or expiry date where appropriate.
- Validate workflow syntax with actionlint. Check composite action metadata, workflow input wiring, and shell syntax separately. Verify tool setup, event filters, permissions, and the secret scan's commit range. Use fake credentials when testing redaction. If hosted CI results are available, verify that the intended jobs ran and report the checks that remain unverified.
- Report suspected leaks by path and rule identifier without copying their values. Flag exposed credentials for revocation or rotation.

[GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use) · [Gitleaks usage](https://github.com/gitleaks/gitleaks)
