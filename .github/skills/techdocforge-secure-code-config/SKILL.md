---
name: techdocforge-secure-code-config
description: "Use when writing, reviewing, or updating TechDocForge code, scripts, configuration, or packaging rules to protect secrets, keep private files out of outputs, and preserve secure defaults."
---

# Secure code and configuration

Apply these checks to the files and behavior involved in the task.

## Credentials and local files

- Load credentials at runtime through the project's established configuration mechanism or the deployment secret store. Use clearly fake placeholders in examples, fixtures, and sample configurations. When a required credential is missing, report the setting name without exposing its value.
- Keep real environment files, private keys, credential exports, and machine-specific secret overrides out of version control. When introducing a local secret file, add a narrowly scoped ignore rule and keep any safe template trackable. For example, ignore `.env` and `.env.*`, with an explicit exception for `.env.example` if the project uses it.
- Ignore rules affect untracked files. Where Git metadata is available, verify the rule with `git check-ignore` and detect already tracked paths with `git ls-files`. If a credential is exposed, report the file path without repeating the value and flag it for rotation or revocation. Handle any history cleanup as a separate authorized operation.
- Keep values out of diffs, logs, exceptions, screenshots, command arguments, and support bundles. Inspect setting names and file paths when they are sufficient.

## Scans, packages, and exports

- Exclude `.git` directories and worktree `.git` pointer files from recursive scans, indexing, archives, and packaged output. Use Git commands for Git operations. Git manages its own metadata, so a repository ignore rule is unnecessary for this exclusion.
- Prefer explicit include lists for packages and exports. Keep local secret files, virtual environments, caches, and generated output out unless the task specifically needs an item. Configure exclusions in each tool that collects files since `.gitignore` only governs Git.

## Code and configuration changes

- Validate external inputs at the boundary. For file handling, constrain paths to the intended directory and account for traversal and symlinks. For subprocesses, pass arguments as a list and avoid shell evaluation of external input.
- Preserve certificate verification, authorization checks, and least-privilege defaults. Keep debug settings opt-in and scope permissions to the files, services, and operations required.
- For changes to credential loading or file collection, verify the relevant behavior with fake credentials. Inspect output names or an archive manifest to confirm private paths are excluded and required files are present.
