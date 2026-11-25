"""Prepare local reference clips and import LD/DubOne voice libraries."""
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from storage import VOICES
from voice import ffmpeg, ffmpeg_executable, valid_wave

EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".mp4"}
FILTER = "Audio / video (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.opus *.mp4)"
LD_NAMES = ("DubOneVoice", "LDVoiceStudioV2", "LDVoiceStudio", "DubOne", "LD Motion Studio")


@dataclass(frozen=True)
class Reference:
    name: str
    language: str
    source: Path
    start: float = 0
    duration: float | None = None


def audio_duration(source):
    result = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-i", str(source)],
        capture_output=True, timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    info = result.stderr.decode("utf-8", "replace")
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    if not duration or "Audio:" not in info:
        raise ValueError("Không đọc được thời lượng hoặc file thiếu track âm thanh.")
    hours, minutes, seconds = map(float, duration.groups())
    return hours * 3600 + minutes * 60 + seconds


def prepare(source, destination, start=0, duration=None, denoise=False):
    if start < 0 or (duration is not None and duration < 0.4):
        raise ValueError("Chọn đoạn mẫu dài ít nhất 0,4 giây.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        temporary = Path(directory) / "sample.wav"
        cut = ("-t", str(duration)) if duration is not None else ()
        filters = ("-af", "afftdn") if denoise else ()
        ffmpeg("-ss", start, "-i", source, *cut, "-map", "0:a:0", *filters,
               "-vn", "-ac", 1, "-ar", 24000, "-c:a", "pcm_s16le", temporary)
        if not valid_wave(temporary.read_bytes(), minimum=0.4):
            raise ValueError("Đoạn đã chọn quá ngắn hoặc nằm ngoài file.")
        temporary.replace(destination)


def save_reference(reference, denoise=False):
    name = re.sub(r'[^\w .-]', '_', reference.name).strip(' .')[:75] or "Giong mau"
    if PureWindowsPath(name + ".wav").is_reserved():
        name = "_" + name
    VOICES.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        destination = VOICES / f"{name}{suffix}.wav"
        try:
            with destination.open("xb"):
                pass
            break
        except FileExistsError:
            index += 1
    try:
        prepare(reference.source, destination, reference.start, reference.duration, denoise)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def _source(library, key, value):
    if not isinstance(value, str) or not value.strip():
        return None
    hint = Path(value)
    leaf = PureWindowsPath(value).name
    candidates = [library / hint, library / key / leaf, library / leaf]
    return next((p for p in candidates if p.suffix.lower() in EXTENSIONS and p.is_file()), None)


def read_library(index):
    items = json.loads(index.read_text(encoding="utf-8-sig"))
    if not isinstance(items, list):
        raise ValueError(f"Kho LD không đúng định dạng: {index.name}")
    for item in items:
        if not isinstance(item, dict):
            continue
        key = PureWindowsPath(str(item.get("ma") or item.get("id") or "")).name
        name = str(item.get("ten") or item.get("name") or key or "Giọng LD")
        language = str(item.get("ngon_ngu") or item.get("language") or "")
        prepared = _source(index.parent, key, item.get("duong_mau") or item.get("prepared_reference_path"))
        original = _source(index.parent, key, item.get("duong_goc") or item.get("reference_path"))
        if not (prepared or original) and key and key not in (".", ".."):
            folder = index.parent / key
            if folder.is_dir():
                files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
                prepared = next((p for p in files if p.stem.startswith("mau_nhan_giong")), None)
                original = next(iter(files), None)
        if prepared:
            yield Reference(name, language, prepared)
        elif original:
            try:
                start = max(0, float(item.get("giay_bat_dau") or item.get("reference_start_sec") or 0))
                duration = float(item.get("dai_giay") or item.get("reference_duration_sec") or 8)
                if not 0.4 <= duration <= 60 or not start < 86_400:
                    raise ValueError()
            except (TypeError, ValueError):
                start, duration = 0, 8
            yield Reference(name, language, original, start, duration)


def find_ld(root):
    def indexes(folder):
        for current, dirs, files in os.walk(folder):
            depth = len(Path(current).relative_to(folder).parts)
            dirs[:] = [d for d in dirs if d.lower() not in
                       {"models", "runtime", "cache", "jobs", "output", "node_modules", "site-packages", "venv"}
                       and not d.startswith(".") and depth < 4]
            if "library.json" in files:
                yield Path(current) / "library.json"

    local_indexes = list(indexes(root))
    if not local_indexes:
        roots = [Path(os.environ[key]) for key in ("LOCALAPPDATA", "APPDATA") if os.environ.get(key)]
        roots.append(Path.home() / ".local" / "share")
        local_indexes = [base / name / "voices" / "library.json" for base in roots for name in LD_NAMES
                         if (base / name / "voices" / "library.json").is_file()]
    voices = {}
    errors = []
    for index in dict.fromkeys(local_indexes):
        try:
            for reference in read_library(index):
                voices.setdefault((reference.source.resolve(), reference.start, reference.duration), reference)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if not voices and errors:
        raise ValueError(errors[0])
    return sorted(voices.values(), key=lambda reference: reference.name.casefold())
