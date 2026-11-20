# Build a standalone Windows executable in dist\TechDocForge\
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

py -3 -m venv .venv-build
. .\.venv-build\Scripts\Activate.ps1
python -m pip install --upgrade pip
if (Test-Path requirements.lock) {
    pip install --require-hashes -r requirements.lock
} else {
    Write-Warning "requirements.lock not found; installing unpinned dependencies."
    pip install -r requirements.txt
}
pip install -r requirements-dev.txt

Write-Host "== Security checks =="
pip-audit -r requirements.txt; if ($LASTEXITCODE -ne 0) { throw "pip-audit reported vulnerabilities" }
bandit -q -r techdocforge;     if ($LASTEXITCODE -ne 0) { throw "bandit reported issues" }
python -m pytest -q tests;     if ($LASTEXITCODE -ne 0) { throw "tests failed" }

Write-Host "== Building =="
pyinstaller --noconfirm --clean TechDocForge.spec

Get-FileHash dist\TechDocForge\TechDocForge.exe -Algorithm SHA256 | Format-List
Write-Host "Done: dist\TechDocForge\TechDocForge.exe"
Write-Host "Recommended: sign the exe with signtool (Authenticode) before distribution."
