"""Paths shared by the desktop and the engine runner."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
VOICES = DATA / "voices"
OUTPUT = DATA / "output"
WORK = DATA / "work"
RUNTIME = DATA / "runtime"
LEGACY = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ToolLongTieng"


def sample_path(value):
    return ROOT / Path(value.strip().strip('"'))
