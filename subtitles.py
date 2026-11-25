"""SRT input for narration."""
import re
from dataclasses import dataclass
from html import unescape

STAMP = r"(\d{1,6}):(\d{2}):(\d{2})[,.](\d{3})"
TIMING = re.compile(rf"^\s*{STAMP}\s*-->\s*{STAMP}(?:\s+.*)?$")


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _milliseconds(groups):
    hours, minutes, seconds, milliseconds = map(int, groups)
    if minutes >= 60 or seconds >= 60:
        raise ValueError("Phút hoặc giây trong SRT không hợp lệ.")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def read_srt(path):
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Hãy lưu SRT dưới dạng UTF-8 hoặc UTF-16 có BOM.") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_srt(text):
    cues = []
    for index, block in enumerate(re.split(r"\n[ \t]*\n+", text.strip()), 1):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        match = TIMING.fullmatch(lines[0]) if lines else None
        if not match:
            raise ValueError(f"Mục SRT {index} thiếu mốc thời gian hợp lệ.")
        start = _milliseconds(match.groups()[:4])
        end = _milliseconds(match.groups()[4:])
        if end <= start:
            raise ValueError(f"Mục SRT {index} có mốc kết thúc sai.")
        cleaned = " ".join(lines[1:])
        cleaned = unescape(re.sub(r"</?(?:i|b|u|s|font)\b[^>]*>|\{\\[^}]*\}", "", cleaned, flags=re.I))
        if cleaned.strip():
            cues.append(Cue(start, end, " ".join(cleaned.split())))
    if not cues:
        raise ValueError("SRT không có lời thoại.")
    return sorted(cues, key=lambda cue: cue.start_ms)
