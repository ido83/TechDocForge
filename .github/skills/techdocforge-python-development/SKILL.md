---
name: techdocforge-python-development
description: "Use when writing or reviewing TechDocForge Python code, designing classes or modules, or managing pip dependencies and virtual environments."
---

# Python development

## Environments and dependencies

- Use a project virtual environment created with `python -m venv .venv`, or use the project's existing environment. Run pip and tests through the same interpreter. Activate that environment or invoke its Python directly, for example `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux.
- Install runtime dependencies with `python -m pip install -r requirements.txt` and development tools with `python -m pip install -r requirements-dev.txt`. The development file already includes runtime requirements.
- Check the standard library and existing dependencies before adding a package. Record direct runtime dependencies in `requirements.txt` and test, build, or audit tooling in `requirements-dev.txt`. Follow the existing version constraint conventions and check compatibility with the project's supported environments.
- Keep dependency updates scoped to the task. For reproducible production builds, follow the hash-pinned lock workflow documented in `requirements.txt` and install the resulting lock with `python -m pip install --require-hashes -r requirements.lock`. Keep lock changes tied to the corresponding requirement changes.
- After changing dependencies, run `python -m pip check` in the target environment and run the tests that exercise the changed dependency. Use the existing build workflow for packaging verification.

## Functions, classes, and modules

- Use a function for a stateless transformation. Use a class when related operations share state, enforce invariants, or manage a resource. Consider a dataclass for structured values such as configuration or request data, with `default_factory` for mutable defaults.
- Give each class a clear responsibility. Prefer composition for replaceable collaborators and use inheritance where subtypes genuinely honor the same contract. Keep constructors focused on initializing valid state and make network calls or file writes explicit.
- Keep UI wiring in the Qt layer and put document transformation or business logic in ordinary Python modules when it can run independently. Reuse existing services and pass collaborators at I/O boundaries when that makes behavior easier to test.
- Use type hints on interfaces, `pathlib.Path` for filesystem paths, and context managers for owned resources. Catch specific exceptions where the caller can recover or report meaningful context. Preserve public signatures and existing configuration compatibility unless the task calls for a change.

## Verification

- Run focused tests with `python -m pytest`. Use the project's existing fixtures, temporary paths, and fakes for filesystem or network boundaries. Cover observable behavior and any specific regression introduced by the change.
