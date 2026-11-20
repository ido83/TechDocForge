"""TechDocForge - turn simple documents into template-conformant technical docs using an LLM."""

from pathlib import Path
import sys

APP_NAME = "TechDocForge"


def _read_version() -> str:
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "VERSION"
    try:
        version = bundled.read_text(encoding="utf-8").strip()
        if version:
            return version
    except OSError:
        pass
    return "1.1.3"


__version__ = _read_version()
