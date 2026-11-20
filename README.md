# TechDocForge

A desktop app for Windows and Linux. It takes a **simple document** (notes, a draft, meeting minutes) and a **template** (your house design-doc or spec format), and uses an LLM to write a **technical document** that follows the template's structure and your style guidelines.

- **LLM backends:** local **Ollama**, or any **OpenAI-compatible gateway** (LiteLLM, vLLM, OpenRouter, Azure/OpenAI proxies, Kong/Apigee AI gateways…)
- **Inputs:** `.docx`, `.md`, `.txt` (source and template)
- **Outputs:** `.md` or `.docx`. When the template is a `.docx`, the exported Word file takes the template's styles, page setup, headers and footers.
- Output streams into the editable **Markdown** tab. The animated bar beside the model shows when generation is active. Use **Preview** to review formatted text, lists, tables, and diagrams.
- Common Mermaid diagrams render locally. The **Show Mermaid source code** checkbox switches between source and images in Preview.
- The app version is stored in `VERSION`, shown in the Help menu and persistent status bar.
- Word exports support centered custom header and footer text and an optional header logo.

## Install a release

Download the archive and matching `.sha256` file for your platform from [GitHub Releases](https://github.com/ido83/TechDocForge/releases). Verify the archive before extracting the entire folder.

| Platform | Archive | Launch |
| --- | --- | --- |
| Windows x64 | `TechDocForge-v<version>-windows-x64.zip` | `TechDocForge/TechDocForge.exe` |
| Linux x64 | `TechDocForge-v<version>-linux-x64.tar.gz` | `./TechDocForge/TechDocForge` |

Replace `<version>` with the version in the downloaded filename. Run the command from the download directory.

**Windows (PowerShell)**

```powershell
$archive = 'TechDocForge-v<version>-windows-x64.zip'
$expected = ((Get-Content -LiteralPath "$archive.sha256" -Raw -ErrorAction Stop).Trim() -split '\s+', 2)[0]
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'Archive SHA-256 mismatch'
}
```

**Linux**

```bash
sha256sum --check 'TechDocForge-v<version>-linux-x64.tar.gz.sha256'
```

The Linux x64 build targets Ubuntu 22.04 with glibc 2.35. Run `ldd --version` to check your system. On Ubuntu, install `libxcb-cursor0` if Qt reports that the XCB platform plugin cannot load.

## Quick start (from source)

```bash
python -m venv .venv
# Linux:   . .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run.py
```

1. **Provider:** choose *Ollama (local)* (default `http://127.0.0.1:11434`) or *OpenAI-compatible gateway*. For a gateway, enter the base URL **including `/v1`**, e.g. `https://llm.corp.example/v1`.
2. Click **Refresh** to load the available models, then pick one.
3. For a gateway, paste the API key and click **Save** to store it in the OS keychain.
4. Choose the **Source doc** and the **Template** (try the files in `samples/`).
5. Change the **Style guidelines** if needed, then click **Generate**.
6. Review **Preview**, make any changes in **Markdown**, then click **Save as…** and choose `.docx` or `.md`. When a request times out, any partial text stays available for review.

Gaps in the source are never invented. They show up as `> TODO: …` lines, which become highlighted quote blocks in Word.

## Build standalone executables

| OS | Command | Output |
| --- | --- | --- |
| Linux | `./build.sh` | `dist/TechDocForge/TechDocForge` |
| Windows | `powershell -ExecutionPolicy Bypass -File build.ps1` | `dist\TechDocForge\TechDocForge.exe` |

PyInstaller builds on the target OS. Both scripts run **pip-audit**, **Bandit**, and the tests before packaging.

Linux runtime notes: you need a Secret Service keychain (GNOME Keyring or KWallet) to store API keys persistently. Without one, keys are kept for the session only. Qt also needs `libxcb-cursor0` on Debian/Ubuntu.

## CI and releases

The [CI workflow](.github/workflows/ci.yml) runs on pushes to `master` and pull requests targeting `master`. It runs Ruff, actionlint, pip-audit, Bandit, Gitleaks, and headless Qt tests. Windows and Ubuntu runners build the x64 archives, which are available from the workflow run after the build jobs succeed.

GitHub recognizes `[skip ci]` in commit messages. Use it for commits that should avoid retriggering automation. A skipped PR can leave required checks pending.

To publish a release, commit the new version and the release workflow to `master`. From the release commit, create and push a new annotated tag matching `VERSION` with a `v` prefix. Use a new patch version for fixes after a tag has been published.

```bash
version="$(git show HEAD:VERSION | tr -d '\r\n')"
git tag -a "v$version" -m "TechDocForge $version"
git push origin "refs/tags/v$version"
```

The [release workflow](.github/workflows/release.yml) waits for the CI checks and both platform builds. It verifies and publishes the Windows ZIP, Linux tarball, and both checksum files.

## Mermaid diagrams

The model may create fenced `mermaid` blocks for architecture, flow and sequence diagrams. Flowcharts and sequence diagrams are rendered locally in Python with Pillow as PNG images; no Node.js, `npm`, or command window is used for these diagrams.

Optional local Mermaid CLI support can render additional diagram types. TechDocForge looks for `mmdc` on Linux and a native `mmdc.exe` on Windows. Windows batch launchers such as `mmdc.cmd` are skipped. The app renders diagrams locally.

Rendered diagrams appear at the end of the document. Markdown exports reference PNGs in a sibling `assets/` directory, while Word exports embed them. If no diagram renders, the rendered preview and exports omit the **Diagrams** heading.

## Security design

| Area | Control |
| --- | --- |
| **Secrets** | API keys live in the OS credential store (Windows Credential Manager / Linux Secret Service) through `keyring`. They are never written to settings or logs. Without a keychain, keys stay in memory for the session only. `TECHDOCFORGE_API_KEY` env var is supported for managed deployments. Keys are excluded from object `repr`. |
| **Transport** | TLS verification is on by default, with a TLS 1.2 minimum. The **OS trust store** is used (via `truststore`), so corporate CAs work without turning verification off. A custom CA bundle is optional. Turning verification off needs explicit confirmation. |
| **Endpoint policy** | Only `http`/`https` URLs are accepted. Plain HTTP is allowed **only to localhost** unless you explicitly opt in. API keys are **never sent over plain HTTP** to a remote host. URLs containing credentials, query strings or fragments are rejected. |
| **Redirects** | Redirects are never followed, so the bearer token can't leak to another host. |
| **Prompt injection** | Source and template are wrapped in delimiters that use a random per-request nonce, and the system prompt marks them as untrusted data. The model is told not to invent facts. |
| **Untrusted model output** | Editable output is plain text. Preview renders Markdown and loads images only from its generated diagram directory. Links are inactive. Output length and request duration are capped. |
| **File parsing** | File types are allow-listed, with size limits. `.docx` files are checked for **zip bombs** (entry count, total size, compression ratio) before parsing. python-docx parses XML with entity resolution off (no XXE). Control characters are stripped. |
| **File writing** | Writes are atomic (temp file + `fsync` + `os.replace`). Config and log directories are `0700` and settings are `0600` (POSIX). |
| **Settings** | Settings are plain JSON with a strict schema: unknown keys and wrong types are ignored, and nothing is ever deserialized as code. |
| **Logging** | Rotating log in the per-user log dir. Document content, prompts and keys are never logged. |
| **Privacy** | TechDocForge has no telemetry. It sends source and template text to the configured model endpoint. Use a local Ollama model and a loopback endpoint for local processing. |
| **Supply chain** | Version-bounded dependencies. Hash-pinned lock file workflow (`pip-compile --generate-hashes` → `--require-hashes`). `pip-audit` + `bandit` run in the build. SHA-256 of the binary is printed. Sign Windows builds with Authenticode. |

### Recommended for production

- Generate and commit `requirements.lock` with hashes.
- Code-sign the Windows executable (`signtool sign /fd SHA256 /tr <timestamp-url> …`), and sign Linux artifacts with GPG/cosign.
- Point the app at a gateway that enforces authentication, rate limits, audit logging and data-retention policies.
- For confidential documents, prefer local Ollama or a gateway with a zero-retention agreement.

## Settings & files

| Item | Linux | Windows |
| --- | --- | --- |
| Settings | `~/.config/TechDocForge/settings.json` | `%LOCALAPPDATA%\TechDocForge\settings.json` |
| Logs | `~/.local/state/TechDocForge/log/` | `%LOCALAPPDATA%\TechDocForge\Logs\` |

**File → Settings** holds TLS and CA options, proxy settings, the request timeout, the input-size limit, Ollama `num_ctx`, and Word header and footer options. Ollama requests disable extended thinking and cap output at 8,192 tokens.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests cover URL policy, settings validation, prompt delimiters, Word template export, archive safety, Markdown preview, Mermaid rendering, and both LLM streaming protocols against a local test server.

## Project layout

```
techdocforge/
  app.py            entry point, logging
  gui.py            Qt UI (PySide6), background workers
  llm.py            Ollama + OpenAI-compatible streaming clients, TLS
  prompting.py      prompt construction, injection hardening, output cleanup
  docio.py          safe .docx/.md/.txt reading, Markdown → .docx export
  mermaid.py        local diagram rendering and export assets
  config.py         settings schema, endpoint validation
  secrets_store.py  OS keychain integration
  fsutil.py         atomic writes, permissions
samples/            example template and source notes
tests/
.github/workflows/  CI and release workflow
.github/actions/    reusable composite actions
```

## TechDocForge wordmark and logo

```text
##### #####  #### #   # ####   ###   #### #####  ###  ####   #### #####
  #   #     #     #   # #   # #   # #     #     #   # #   # #     #
  #   ####  #     ##### #   # #   # #     ####  #   # ####  # ### ####
  #   #     #     #   # #   # #   # #     #     #   # #  #  #   # #
  #   #####  #### #   # ####   ###   #### #      ###  #   #  #### #####
```

![TechDocForge app logo](assets/TechDocForge.png)
