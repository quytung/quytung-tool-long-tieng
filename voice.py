"""Client for the OmniVoice multipart WAV endpoint."""
import asyncio
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
    engine: str = "vps"
    language: str = "en"


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


def ffmpeg_executable():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    from imageio_ffmpeg import get_ffmpeg_exe

    return get_ffmpeg_exe()


def ffmpeg(*args):
    process = subprocess.run(
        [ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", "replace").strip() or "FFmpeg xử lý audio lỗi.")


def valid_wave(data, minimum=0.1):
    try:
        with wave.open(io.BytesIO(data)) as audio:
            frames = audio.getnframes()
            return frames >= audio.getframerate() * minimum and len(audio.readframes(frames)) == (
                frames * audio.getnchannels() * audio.getsampwidth()
            )
    except (EOFError, wave.Error):
        return False


class Cancelled(Exception):
    pass


def request_audio(url, text, sample, options, stop, log=lambda _: None):
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
        if attempt < 2:
            log(f"CẢNH BÁO · {error} Thử lại sau {2 ** (attempt + 1)} giây.")
            if stop.wait(2 ** (attempt + 1)):
                raise Cancelled()
    raise RuntimeError(error)


def render(url, sample_path, sentences, destination, cache, options, stop, progress, preview=False, cues=None, artifacts=False, log=lambda _: None, resume=None):
    if cues:
        if cues[-1].end_ms > 14_400_000:
            raise ValueError("Mốc SRT vượt 4 giờ. Tắt giữ mốc để đọc nối tiếp.")
        if any(first.end_ms > second.start_ms for first, second in zip(cues, cues[1:])):
            raise ValueError("SRT có mốc chồng nhau. Chỉnh mốc hoặc tắt giữ mốc.")
        groups = [list(split_text(cue.text)) for cue in cues]
        sentences = [segment for group in groups for segment in group]
    cache.mkdir(parents=True, exist_ok=True)
    from storage import WORK

    WORK.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="voice-", dir=WORK) as temporary:
        work = Path(temporary)
        sample_wav = work / "reference.wav"
        cut = ("-t", options.sample_seconds) if options.sample_seconds else ()
        sample = b""
        if options.engine != "edge":
            ffmpeg("-i", sample_path, *cut, "-vn", "-ac", "1", "-c:a", "pcm_s16le", sample_wav)
            sample = sample_wav.read_bytes()
            if not valid_wave(sample):
                raise ValueError("File giọng mẫu quá ngắn hoặc hỏng.")
        fingerprint = hashlib.sha256(sample).hexdigest()

        log(f"Chuẩn bị xong · {len(sentences)} câu · {options.engine}")
        clips = destination.parent / "tung-cau"
        if artifacts:
            clips.mkdir(exist_ok=True)
        cleanup_resume = resume is None
        resume = resume or destination.parent / ".resume"
        if artifacts:
            resume.mkdir(exist_ok=True, parents=True)

        def synthesize(index, sentence):
            if stop.is_set():
                raise Cancelled()
            key = hashlib.sha256(repr((url, fingerprint, options, sentence)).encode()).hexdigest()
            processed = resume / f"{key}.wav"
            if artifacts and processed.is_file() and valid_wave(processed.read_bytes()):
                log(f"Câu {index + 1}/{len(sentences)} · dùng lại")
                return processed
            log(f"Câu {index + 1}/{len(sentences)} · đang tạo")
            try:
                result = generate(index, sentence)
            except Cancelled:
                raise
            except Exception as exc:
                raise RuntimeError(f"Câu {index + 1}: {exc}") from exc
            if artifacts:
                result.replace(processed)
                return processed
            return result

        def generate(index, sentence):
            if stop.is_set():
                raise Cancelled()
            if options.engine != "vps":
                raw = work / f"raw-{index}.wav"
                if options.engine == "edge":
                    import edge_tts

                    source = raw.with_suffix(".mp3")
                    asyncio.run(edge_tts.Communicate(sentence, url, connect_timeout=10,
                                                     receive_timeout=options.timeout).save(str(source)))
                    ffmpeg("-i", source, "-c:a", "pcm_s16le", raw)
                else:
                    import local_tts

                    local_tts.synth(options.engine, sentence, sample_wav, raw, log=log, lang=options.language)
                return standardize(index, raw)
            key = hashlib.sha256(repr((url, options.instruct, fingerprint, sentence)).encode()).hexdigest()
            cached = cache / (key + ".wav")
            data = cached.read_bytes() if not preview and cached.exists() else b""
            if not valid_wave(data):
                try:
                    data = request_audio(url, sentence, sample, options, stop, log)
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
            raw.write_bytes(data)
            return standardize(index, raw)

        def standardize(index, raw):
            wav = work / f"{index}.wav"
            filters = [f"atempo={options.speed:.2f}"]
            if options.trim:
                trim = "silenceremove=start_periods=1:start_threshold=-45dB"
                filters[:0] = [trim, "areverse", trim, "areverse"]
            ffmpeg("-i", raw, "-af", ",".join(filters), "-ac", "1", "-ar", RATE, "-c:a", "pcm_s16le", wav)
            if not valid_wave(wav.read_bytes()):
                raise RuntimeError(f"Câu {index + 1} không đủ audio sau xử lý. Thử tắt cắt im lặng.")
            return wav

        parts = [None] * len(sentences)
        with ThreadPoolExecutor(max_workers=options.workers if options.engine in ("vps", "edge") else 1) as pool:
            futures = {pool.submit(synthesize, i, s): i for i, s in enumerate(sentences)}
            try:
                for count, future in enumerate(as_completed(futures), 1):
                    parts[futures[future]] = future.result()
                    log(f"Câu {futures[future] + 1}/{len(sentences)} · xong")
                    progress(count, len(sentences))
            except BaseException:
                stop.set()
                for future in futures:
                    future.cancel()
                raise

        if stop.is_set():
            raise Cancelled()
        joined = work / "joined.wav"
        records = []
        log("Đang ghép audio và tính mốc phụ đề")
        if cues:
            records = join_timed(joined, parts, groups, cues, work, stop)
        else:
            with wave.open(str(joined), "wb") as audio:
                init_wave(audio)
                cursor = 0
                gap = round(options.gap * RATE)
                for index, part in enumerate(parts):
                    if index:
                        silence(audio, gap)
                        cursor += gap
                    count = copy_wave(audio, part)
                    records.append((sentences[index], cursor, cursor + count, part))
                    cursor += count

        filters = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if options.normalize else []
        partial = destination.with_name(f".{uuid4().hex}{destination.suffix}")
        try:
            ffmpeg("-i", joined, *filters, "-ar", RATE, "-ac", "1", partial)
            if stop.is_set():
                raise Cancelled()
            if artifacts:
                for index, (_, _, _, clip) in enumerate(records, 1):
                    shutil.copyfile(clip, clips / f"{index:04d}.wav")
                subtitles = "\n\n".join(
                    f"{index}\n{srt_stamp(start)} --> {srt_stamp(end)}\n{text}"
                    for index, (text, start, end, _) in enumerate(records, 1)
                )
                (destination.parent / "phu-de.srt").write_text(subtitles + "\n", encoding="utf-8-sig")
            os.replace(partial, destination)
            if artifacts and cleanup_resume:
                shutil.rmtree(resume, ignore_errors=True)
        finally:
            partial.unlink(missing_ok=True)


def init_wave(audio):
    audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))


def silence(audio, frames):
    while frames > 0:
        count = min(frames, RATE)
        audio.writeframesraw(bytes(count * 2))
        frames -= count


def copy_wave(destination, path):
    with wave.open(str(path), "rb") as source:
        count = source.getnframes()
        destination.writeframesraw(source.readframes(count))
        return count


def join_timed(path, parts, groups, cues, work, stop):
    cursor = 0
    part_index = 0
    records = []
    with wave.open(str(path), "wb") as output:
        init_wave(output)
        for index, (cue, group) in enumerate(zip(cues, groups), 1):
            if stop.is_set():
                raise Cancelled()
            start = round(cue.start_ms * RATE / 1000)
            end = round(cue.end_ms * RATE / 1000)
            slot = work / f"cue-{index}.wav"
            with wave.open(str(slot), "wb") as audio:
                init_wave(audio)
                for part in parts[part_index:part_index + len(group)]:
                    copy_wave(audio, part)
            part_index += len(group)
            with wave.open(str(slot), "rb") as audio:
                count = audio.getnframes()
            available = end - start
            if count > available:
                ratio = count / available
                if ratio > 2:
                    raise ValueError(f"Mục SRT {index} cần đọc nhanh hơn 2× để vừa mốc. Rút lời hoặc tắt giữ mốc.")
                fitted = work / f"cue-{index}-fit.wav"
                ffmpeg("-i", slot, "-af", f"atempo={min(2, ratio * 1.03):.6f}",
                       "-ar", RATE, "-ac", 1, "-c:a", "pcm_s16le", fitted)
                slot = fitted
            with wave.open(str(slot), "rb") as audio:
                count = audio.getnframes()
                if count > available:
                    raise ValueError(f"Mục SRT {index} chưa vừa mốc sau khi chỉnh tốc độ.")
                silence(output, start - cursor)
                output.writeframesraw(audio.readframes(count))
            cursor = start + count
            records.append((cue.text, start, cursor, slot))
        silence(output, end - cursor)
    return records


def srt_stamp(frames):
    milliseconds = round(frames * 1000 / RATE)
    seconds, fraction = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{fraction:03}"
