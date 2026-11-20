#!/usr/bin/env bash
# Build a standalone Linux executable in dist/TechDocForge/
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv-build
. .venv-build/bin/activate
python -m pip install --upgrade pip
if [[ -f requirements.lock ]]; then
  pip install --require-hashes -r requirements.lock
else
  echo "WARNING: requirements.lock not found; installing unpinned dependencies." >&2
  pip install -r requirements.txt
fi
pip install -r requirements-dev.txt

echo "== Security checks =="
pip-audit -r requirements.txt || { echo "pip-audit reported vulnerabilities" >&2; exit 1; }
bandit -q -r techdocforge
python -m pytest -q tests

echo "== Building =="
pyinstaller --noconfirm --clean --windowed \
  --name TechDocForge \
  --icon assets/TechDocForge.png \
  --collect-submodules keyring.backends \
  --add-data assets/TechDocForge.png:assets \
  --add-data VERSION:. \
  run.py

( cd dist && sha256sum -b TechDocForge/TechDocForge > TechDocForge.sha256 )
echo "Done: dist/TechDocForge/TechDocForge"
