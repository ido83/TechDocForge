---
name: techdocforge-create-pr
description: "Use when preparing, drafting, or opening a GitHub pull request for TechDocForge from repository changes."
---

# Create a TechDocForge pull request

1. Verify the working tree, current branch, and remote using Git. Use the requested base branch or confirm the repository's default branch. Compare the full branch diff against the base with `git diff <base>...HEAD` and review staged and unstaged changes separately. If Git metadata or remote access is unavailable, identify the gap and prepare the title and body from the evidence at hand.
2. Keep the branch and commit scope aligned with the request. Preserve unrelated work and stage intended paths explicitly if a commit is needed. Check for credentials, local settings, generated documents, and build artifacts before including files. Include generated deliverables when the request or repository requires them.
3. Run the repository's relevant checks. For Python changes, run focused tests with the project environment's `python -m pytest`. For a Windows packaging change, verify the requested build and startup if the environment permits. Record what ran, its outcome, and any check that was blocked or skipped.
4. Write an action-oriented title and a body that reviewers can understand without the conversation. Summarize the user-visible change, explain why it was needed, and list verification results. Link an issue or add screenshots when provided or useful. Mention known limitations or rollout steps only when they apply.
5. If the user asks to open the PR, use the available GitHub integration or `gh`. Look for an existing PR for the head branch before creating one. Push only the intended branch if needed and authorized. Apply the requested draft or review-ready state. With `gh pr create`, set the base and head explicitly and pass the body through `--body-file`.
6. Verify the PR URL, base, head, and status after creation. For a draft-writing request, return the title and body in the requested format.
