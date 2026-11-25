"""Client for the OmniVoice multipart WAV endpoint."""
import hashlib
import io
import os
import re
import shutil
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests

RATE = 44100


@dataclass(frozen=True)
class Options:
    speed: float = 1.0
    gap: float = 0.18
    normalize: bool = True
    trim: bool = False
    sample_seconds: int = 10
    workers: int = 2
    timeout: int = 120
    instruct: str = ""


def endpoint(value):
    value = value.strip()
    if "://" not in value:
        authority = urlsplit("//" + value).netloc
        host = urlsplit("//" + value).hostname
        scheme = "http" if ":" in authority or host == "localhost" else "https"
        value = f"{scheme}://{value}"
    url = urlsplit(value)
    if (url.scheme not in ("http", "https") or not url.hostname or
            url.username or url.password or url.fragment or re.search(r"\s", value)):
        raise ValueError("Nhập địa chỉ VPS HTTP hoặc HTTPS hợp lệ.")
    if url.port == 0:
        raise ValueError("Cổng VPS phải lớn hơn 0.")
    path = re.sub(r"(?:/v1/tts|/tts|/v1)$", "", url.path.rstrip("/"))
    return urlunsplit((url.scheme, url.netloc, path + "/v1/tts", url.query, ""))


def split_text(text, limit=280):
    for paragraph in text.splitlines():
        for sentence in re.split(r"""(?<=[.!?…。！？])\s+""", paragraph.strip()):
            sentence = " ".join(sentence.split())
            while len(sentence) > limit:
                boundary = sentence.rfind(" ", 0, limit + 1)
                boundary = boundary if boundary > limit // 2 else limit
                yield sentence[:boundary]
                sentence = sentence[boundary:].lstrip()
            if sentence:
                yield sentence


def ffmpeg(*args):
    executable = shutil.which("ffmpeg")
    if not executable:
        from imageio_ffmpeg import get_ffmpeg_exe

        executable = get_ffmpeg_exe()
    process = subprocess.run(
        [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", "replace").strip() or "FFmpeg xử lý audio lỗi.")


def valid_wave(data):
    try:
        with wave.open(io.BytesIO(data)) as audio:
            frames = audio.getnframes()
            return frames >= audio.getframerate() / 10 and len(audio.readframes(frames)) == (
                frames * audio.getnchannels() * audio.getsampwidth()
            )
    except (EOFError, wave.Error):
        return False


class Cancelled(Exception):
    pass


def request_audio(url, text, sample, options, stop):
    for attempt in range(3):
        if stop.is_set():
            raise Cancelled()
        try:
            with requests.post(
                url, data={"text": text, "instruct": options.instruct},
                files={"ref_audio_file": ("reference.wav", sample, "audio/wav")},
                timeout=(10, options.timeout), allow_redirects=False,
            ) as reply:
                status = reply.status_code
                if status == 200 and valid_wave(reply.content):
                    return reply.content
                if status == 422:
                    raise RuntimeError("VPS báo HTTP 422. Kiểm tra endpoint OmniVoice và định dạng mẫu.")
                if status != 200 and status not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError(f"VPS báo HTTP {status}. Kiểm tra địa chỉ và quyền truy cập.")
                error = "VPS trả WAV rỗng, quá ngắn hoặc hỏng." if status == 200 else f"VPS báo HTTP {status}."
        except requests.exceptions.SSLError as exc:
            raise RuntimeError("Không xác thực được TLS. Kiểm tra giao thức và chứng chỉ VPS.") from exc
        except requests.exceptions.Timeout:
            error = "VPS phản hồi quá thời gian chờ."
        except requests.exceptions.ConnectionError:
            error = "Không kết nối được VPS. Kiểm tra địa chỉ và trạng thái máy thuê."
        if attempt < 2 and stop.wait(2 ** (attempt + 1)):
            raise Cancelled()
    raise RuntimeError(error)


def render(url, sample_path, sentences, destination, cache, options, stop, progress, preview=False):
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="voice-") as temporary:
        work = Path(temporary)
        sample_wav = work / "reference.wav"
        cut = ("-t", options.sample_seconds) if options.sample_seconds else ()
        ffmpeg("-i", sample_path, *cut, "-vn", "-ac", "1", "-c:a", "pcm_s16le", sample_wav)
        sample = sample_wav.read_bytes()
        if not valid_wave(sample):
            raise ValueError("File giọng mẫu quá ngắn hoặc hỏng.")
        fingerprint = hashlib.sha256(sample).hexdigest()

        def synthesize(index, sentence):
            if stop.is_set():
                raise Cancelled()
            key = hashlib.sha256(repr((url, options.instruct, fingerprint, sentence)).encode()).hexdigest()
            cached = cache / (key + ".wav")
            data = cached.read_bytes() if not preview and cached.exists() else b""
            if not valid_wave(data):
                try:
                    data = request_audio(url, sentence, sample, options, stop)
                except RuntimeError as exc:
                    raise RuntimeError(f"Câu {index + 1}: {exc}") from exc
                if not preview:
                    partial = cache / (uuid4().hex + ".tmp")
                    try:
                        partial.write_bytes(data)
                        partial.replace(cached)
                    finally:
                        partial.unlink(missing_ok=True)
            raw = work / f"raw-{index}.wav"
            wav = work / f"{index}.wav"
            raw.write_bytes(data)
            filters = [f"atempo={options.speed:.2f}"]
            if options.trim:
                trim = "silenceremove=start_periods=1:start_threshold=-45dB"
                filters[:0] = [trim, "areverse", trim, "areverse"]
            ffmpeg("-i", raw, "-af", ",".join(filters), "-ac", "1", "-ar", RATE, "-c:a", "pcm_s16le", wav)
            if not valid_wave(wav.read_bytes()):
                raise RuntimeError(f"Câu {index + 1} không đủ audio sau xử lý. Thử tắt cắt im lặng.")
            return wav

        parts = [None] * len(sentences)
        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            futures = {pool.submit(synthesize, i, s): i for i, s in enumerate(sentences)}
            try:
                for count, future in enumerate(as_completed(futures), 1):
                    parts[futures[future]] = future.result()
                    progress(count, len(sentences))
            except BaseException:
                stop.set()
                for future in futures:
                    future.cancel()
                raise

        if stop.is_set():
            raise Cancelled()
        joined = work / "joined.wav"
        with wave.open(str(joined), "wb") as audio:
            audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            gap = b"\x00\x00" * round(options.gap * RATE)
            for index, part in enumerate(parts):
                if index:
                    audio.writeframesraw(gap)
                with wave.open(str(part), "rb") as source:
                    audio.writeframesraw(source.readframes(source.getnframes()))

        filters = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if options.normalize else []
        partial = destination.with_name(f".{uuid4().hex}{destination.suffix}")
        try:
            ffmpeg("-i", joined, *filters, "-ar", RATE, "-ac", "1", partial)
            if stop.is_set():
                raise Cancelled()
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
