"""Local file/folder opener — backend for link-styled Open/Reveal UI.

Streamlit runs on the same machine as the files, so a button click can
open File Explorer / default app server-side. Browsers block
`file://` hrefs, so this is used instead of `<a href>`.
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple


def _resolve(path_str: str) -> Path | None:
    try:
        p = Path(str(path_str).strip().strip('"\'')).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()
        return p if p.exists() else None
    except Exception:
        return None


def open_path(path_str: str) -> Tuple[bool, str]:
    """Open file/folder with the OS default app. Returns (ok, message)."""
    p = _resolve(path_str)
    if p is None:
        return False, f"Not found: {path_str}"
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # noqa: S606 — local user-initiated open
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return True, f"Opened {p.name}"
    except Exception as e:
        return False, str(e)


def reveal_path(path_str: str) -> Tuple[bool, str]:
    """Show file/folder in File Explorer (select file, open folder)."""
    p = _resolve(path_str)
    if p is None:
        return False, f"Not found: {path_str}"
    try:
        if sys.platform.startswith("win"):
            if p.is_dir():
                subprocess.Popen(["explorer", str(p)])
            else:
                subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            target = str(p if p.is_dir() else p.parent)
            subprocess.Popen(["xdg-open", target])
        return True, f"Revealed {p.name}"
    except Exception as e:
        return False, str(e)
