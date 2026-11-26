"""3 máy đọc chạy ngay trên PC này (không cần máy chủ thuê): OmniVoice · Chatterbox · VieNeu.

Dựng lại theo cách LD Motion Studio làm (he_thong/app/voice/voice_may.py):
  - Mỗi máy đọc một môi trường Python RIÊNG ở runtime/<máy>/ (torch mỗi máy một bản, không đụng Python của tool).
  - Nút «Cài» làm hết: dựng môi trường, cài gói, máy có card NVIDIA thì cài torch bản CUDA khớp driver,
    rồi đọc thử 1 câu (lần này tải luôn model về, để lúc làm video thật không phải chờ tải).
  - Lúc đọc: mỗi máy chạy 1 tiến trình nạp model MỘT lần rồi giữ suốt phiên (app/tts_worker.py),
    các câu xếp hàng đọc lần lượt trên card.
"""
from __future__ import annotations

import atexit
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from storage import DATA, RUNTIME, WORK
WORKER = Path(__file__).resolve().parent / "tts_worker.py"

# mã máy đọc trong Cài đặt → tên môi trường
ENGINES = {
    "omni_local": "omnivoice",
    "chatterbox": "chatterbox",
    "vieneu": "vieneu",
}
LABELS = {
    "omni_local": "OmniVoice trên máy này (nhân giọng mẫu, 646 thứ tiếng)",
    "chatterbox": "Chatterbox Multilingual V3 trên máy này (nhân giọng mẫu)",
    "vieneu": "VieNeu-TTS trên máy này (mạnh tiếng Việt, chạy CPU)",
}
SIZES = {"omnivoice": "~3–4 GB", "chatterbox": "~4–5 GB", "vieneu": "~1–2 GB"}

CHATTERBOX_PYPI = "chatterbox-tts==0.1.7"
CHATTERBOX_SRC = "https://github.com/resemble-ai/chatterbox/archive/5de7a54.zip"   # có Multilingual V3
PERTH_SRC = "https://github.com/resemble-ai/Perth/archive/e5cae4d.zip"
TIMEOUT_SENT = 900          # giây cho 1 câu (máy chỉ có CPU có thể rất chậm)
TIMEOUT_LOAD = 1800         # giây cho lần nạp đầu (có thể phải tải model)

_NOWIN = 0x08000000 if os.name == "nt" else 0     # Windows: không bật cửa sổ đen


def is_local(engine: str) -> bool:
    return engine in ENGINES


def env_dir(engine: str) -> Path:
    return RUNTIME / ENGINES[engine]


def env_python(engine: str) -> Path:
    d = env_dir(engine)
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_installed(engine: str) -> bool:
    return env_python(engine).is_file() and (env_dir(engine) / ".ok").is_file()


def status(engine: str) -> str:
    if not is_local(engine):
        return ""
    if is_installed(engine):
        info = (env_dir(engine) / ".ok").read_text(encoding="utf-8", errors="ignore").strip()
        return "✔ Đã cài" + (f" · {info}" if info else "")
    if env_python(engine).is_file():
        return "⚠ Cài dở: bấm Cài lại"
    return f"Chưa cài (tải {SIZES[ENGINES[engine]]}, một lần)"


# ─────────────────────────────── dò máy ───────────────────────────────
def _env() -> dict:
    e = dict(os.environ)
    e.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    WORK.mkdir(parents=True, exist_ok=True)
    cache = DATA / "models"
    e.update(HF_HOME=str(cache / "huggingface"), HF_HUB_CACHE=str(cache / "huggingface" / "hub"),
             HUGGINGFACE_HUB_CACHE=str(cache / "huggingface" / "hub"),
             XDG_CACHE_HOME=str(cache), TORCH_HOME=str(cache / "torch"),
             PIP_CACHE_DIR=str(DATA / "cache" / "pip"), TEMP=str(WORK), TMP=str(WORK), TMPDIR=str(WORK))
    e.pop("PYTHONHOME", None)
    e.pop("PYTHONPATH", None)
    return e


def nvidia() -> tuple[str, tuple[int, int]]:
    """(tên card, bản CUDA driver hỗ trợ) — ("", (0,0)) nếu không có card NVIDIA."""
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(p) if p.is_file() else None
    if not exe:
        return "", (0, 0)
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=20, creationflags=_NOWIN).stdout
        name = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                              timeout=20, creationflags=_NOWIN).stdout.strip().splitlines()
    except Exception:  # noqa: BLE001
        return "", (0, 0)
    m = re.search(r"CUDA(?: UMD)? Version:\s*(\d+)\.(\d+)", out or "")
    return (name[0].strip() if name else "NVIDIA"), ((int(m.group(1)), int(m.group(2))) if m else (0, 0))


def _cuda_index(cuda: tuple[int, int], choices: tuple[str, ...]) -> str:
    """Bản build torch CUDA cao nhất mà driver chạy được, trong các bản torch đó có."""
    for tag in choices:                       # choices xếp từ mới tới cũ, vd ("cu128", "cu126", "cu124")
        need = (int(tag[2:-1]), int(tag[-1]))
        if cuda >= need:
            return tag
    return ""


def find_python(log=print) -> str:
    """Torch/Chatterbox hợp nhất Python 3.11–3.12 (LD cũng chốt vậy). Có thì dùng, không thì dùng Python đang chạy tool."""
    cands: list[list[str]] = []
    if os.name == "nt" and shutil.which("py"):
        cands += [["py", "-3.11"], ["py", "-3.12"]]
    for n in ("python3.11", "python3.12"):
        if shutil.which(n):
            cands.append([shutil.which(n)])
    for c in cands:
        try:
            r = subprocess.run(c + ["-c", "import sys,venv;print(sys.executable)"], capture_output=True, text=True,
                               timeout=30, creationflags=_NOWIN)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().splitlines()[-1]
        except Exception:  # noqa: BLE001
            pass
    v = sys.version_info
    if (v.major, v.minor) not in ((3, 11), (3, 12)):
        log(f"⚠ Máy không có Python 3.11/3.12, dùng Python {v.major}.{v.minor} của tool. Nếu cài lỗi: cài thêm "
            "Python 3.11 từ python.org (tick «Add to PATH») rồi bấm Cài lại.")
    return sys.executable


# ─────────────────────────────── cài đặt ───────────────────────────────
class InstallError(RuntimeError):
    pass


def _run(cmd: list[str], log, ok_fail: bool = False, timeout: int = 3600) -> int:
    """Chạy lệnh, đẩy từng dòng ra nhật ký (lọc bớt dòng tiến trình tải)."""
    log("  $ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))[:220])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=_env(), creationflags=_NOWIN)
    last = ""
    t0 = time.time()
    tail: list[str] = []
    assert p.stdout
    for line in p.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail = (tail + [line])[-15:]
        # chỉ hiện các dòng có ý nghĩa, tránh ngập nhật ký
        if re.match(r"^(Collecting|Downloading|Installing|Successfully|ERROR|error|WARNING: (?!Retrying))", line) \
                or "OK" in line or "LDR" in line:
            if line != last:
                log("    " + line[:200])
                last = line
        if time.time() - t0 > timeout:
            p.kill()
            raise InstallError("Lệnh chạy quá lâu, đã dừng.")
    code = p.wait()
    if code != 0 and not ok_fail:
        raise InstallError("Lệnh cài lỗi:\n" + "\n".join(tail[-8:]))
    return code


def _pip(py: Path, args: list[str], log, ok_fail: bool = False) -> int:
    return _run([str(py), "-m", "pip", "install", "--prefer-binary", *args], log, ok_fail)


def _gpu_ok(py: Path) -> str:
    r = subprocess.run([str(py), "-c",
                        "import torch\ntry:\n x=torch.zeros(8,8,device='cuda');(x+1).sum().item()\n"
                        " print('CUDA_OK',torch.__version__,torch.cuda.get_device_name(0))\n"
                        "except Exception as e: print('CUDA_NO',torch.__version__,str(e)[:150])"],
                       capture_output=True, text=True, env=_env(), timeout=300, creationflags=_NOWIN)
    return (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "CUDA_NO"


def _torch_ver(py: Path) -> str:
    r = subprocess.run([str(py), "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True,
                       env=_env(), timeout=300, creationflags=_NOWIN)
    return (r.stdout.strip().splitlines() or [""])[-1].split("+")[0]


def install(engine: str, log=print, sample: Path | None = None) -> str:
    """Cài 1 máy đọc từ đầu tới lúc đọc thử được 1 câu. Trả về dòng tóm tắt."""
    name = ENGINES[engine]
    d = env_dir(engine)
    stop(engine)
    shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    card, cuda = nvidia()
    log(f"[1/4] Dò máy: {'card ' + card + f' (CUDA {cuda[0]}.{cuda[1]})' if card else 'không có card NVIDIA → chạy CPU'}")
    base = find_python(log)
    log(f"[1/4] Dựng môi trường riêng: runtime/{name} (Python: {base})")
    _run([base, "-m", "venv", "--clear", str(d)], log)
    py = env_python(engine)
    if not py.is_file():
        raise InstallError(f"Không dựng được môi trường Python ở {d}.")
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip", "wheel"], log)

    gpu_note = "CPU"
    if name == "omnivoice":
        log("[2/4] Cài Torch (cài TRƯỚC gói chính để gói không kéo nhầm bản)…")
        tag = _cuda_index(cuda, ("cu128", "cu126")) if card else ""
        if card and not tag:
            log(f"  ⚠ Driver card quá cũ (CUDA {cuda[0]}.{cuda[1]}), torch mới cần ≥ 12.6. Cập nhật driver NVIDIA rồi "
                "Cài lại để chạy bằng card. Tạm cài bản CPU.")
        idx = ["--index-url", f"https://download.pytorch.org/whl/{tag}"] if tag else []
        if tag or platform.system() != "Linux":
            _pip(py, ["torch>=2.8", "torchaudio>=2.8", *idx], log)
        else:   # Linux không card: bản CPU nhẹ hơn nhiều
            _pip(py, ["torch>=2.8", "torchaudio>=2.8", "--index-url", "https://download.pytorch.org/whl/cpu"], log)
        log("[3/4] Cài OmniVoice…")
        if _pip(py, ["omnivoice", "soundfile"], log, ok_fail=True) != 0:
            log("  Bản PyPI lỗi, cài thẳng từ GitHub…")
            _pip(py, ["git+https://github.com/k2-fsa/OmniVoice.git", "soundfile"], log)
        if tag:
            gpu_note = _gpu_ok(py)
    elif name == "chatterbox":
        log("[2/4] Ghim setuptools 80.9 (Perth còn cần pkg_resources)…")
        _pip(py, ["setuptools==80.9.0"], log)
        log("[3/4] Cài Chatterbox (bộ phụ thuộc từ PyPI, rồi mã nguồn Multilingual V3)…")
        _pip(py, [CHATTERBOX_PYPI], log)
        _run([str(py), "-m", "pip", "uninstall", "-y", "chatterbox-tts"], log, ok_fail=True)
        if _pip(py, ["--no-cache-dir", "--force-reinstall", "--no-deps", CHATTERBOX_SRC], log, ok_fail=True) != 0:
            # không tải được mã V3 từ GitHub (mạng chặn…): dùng bản PyPI, vẫn đọc tiếng Anh tốt, chỉ thiếu V3
            log("  ⚠ Không tải được Chatterbox V3 từ GitHub, dùng bản PyPI 0.1.7 (vẫn đọc tốt).")
            _pip(py, ["--no-deps", CHATTERBOX_PYPI], log)
        _run([str(py), "-m", "pip", "uninstall", "-y", "resemble-perth", "perth"], log, ok_fail=True)
        if _pip(py, ["--no-cache-dir", "--force-reinstall", "--no-deps", PERTH_SRC], log, ok_fail=True) != 0:
            _pip(py, ["resemble-perth"], log)
        _pip(py, ["soundfile"], log, ok_fail=True)
        if card and os.name == "nt":
            # pip trên Windows kéo torch bản CPU → thay bằng bản CUDA cùng số bản (giữ nguyên các gói khác)
            tv = _torch_ver(py) or "2.6.0"
            tag = _cuda_index(cuda, ("cu126", "cu124", "cu118"))
            if tag:
                log(f"[3/4] Có card NVIDIA: thay torch {tv} bản CPU bằng bản {tag} (~2,5 GB)…")
                _pip(py, ["--force-reinstall", "--no-deps", f"torch=={tv}", f"torchaudio=={tv}",
                          "--index-url", f"https://download.pytorch.org/whl/{tag}"], log, ok_fail=True)
            gpu_note = _gpu_ok(py)
            if "CUDA_OK" not in gpu_note and _cuda_index(cuda, ("cu128",)):
                # card RTX 50xx: torch 2.6 không có nhân cho đời card này → nâng lên bản có cu128
                log("[3/4] Card đời mới cần torch có cu128, nâng torch 2.7.1…")
                _pip(py, ["--force-reinstall", "--no-deps", "torch==2.7.1", "torchaudio==2.7.1",
                          "--index-url", "https://download.pytorch.org/whl/cu128"], log, ok_fail=True)
                gpu_note = _gpu_ok(py)
        elif card:
            gpu_note = _gpu_ok(py)
    else:  # vieneu
        log("[2/4] Cài VieNeu-TTS (chạy ONNX trên CPU, không cần card)…")
        if _pip(py, ["vieneu"], log, ok_fail=True) != 0:
            log("  Bản mới lỗi dựng gói, thử bản dựng sẵn cũ hơn của numba/llvmlite…")
            _pip(py, ["numba<0.62", "llvmlite<0.45"], log, ok_fail=True)
            _pip(py, ["vieneu"], log)
        log("[3/4] Xong phần gói.")
    if "CUDA_OK" in gpu_note:
        log("  ✔ Chạy bằng card: " + gpu_note.replace("CUDA_OK", "").strip())
        gpu_note = "card " + (card or "NVIDIA")
    elif card and name != "vieneu":
        log("  ⚠ Chưa chạy được bằng card (" + gpu_note[:150] + "). Vẫn đọc được bằng CPU, chậm hơn.")
        gpu_note = "CPU"

    log("[4/4] Đọc thử 1 câu (lần đầu tải model về máy, có thể mất vài phút)…")
    stop(engine)
    test = d / "doc_thu.wav"
    text = "Xin chào, đây là giọng đọc thử." if engine == "vieneu" else "This is a short test of the local voice."
    secs = synth(engine, text, sample, test, log=log, lang="en")
    summary = f"{gpu_note} · câu thử {secs:.1f}s"
    (d / ".ok").write_text(summary, encoding="utf-8")
    log(f"✔ Cài xong {LABELS[engine].split(' (')[0]}: {summary}")
    return summary


# ─────────────────────────────── chạy đọc ───────────────────────────────
class _Worker:
    def __init__(self, engine: str, log, device: str):
        self.device = device
        self.engine = engine
        self.lock = threading.Lock()
        self.q: queue.Queue = queue.Queue()
        py = env_python(engine)
        if not py.is_file():
            raise RuntimeError(f"Chưa cài máy đọc «{LABELS[engine]}». Vào Cài đặt › Giọng đọc, bấm «Cài máy đọc».")
        self.p = subprocess.Popen([str(py), str(WORKER), ENGINES[engine], device], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  errors="replace", env=_env(), bufsize=1, creationflags=_NOWIN)
        self.err: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        log(f"Nạp model {LABELS[engine].split(' (')[0]} (lần đầu trong phiên, mất chút thời gian)…")
        t0 = time.time()
        msg = self._wait(TIMEOUT_LOAD, log)
        if not msg.get("ready"):
            self.kill()
            raise RuntimeError(msg.get("error") or "máy đọc không khởi động được: " + " | ".join(self.err[-4:]))
        log(f"Model sẵn sàng sau {time.time() - t0:.0f}s.")

    def _read(self):
        assert self.p.stdout
        for line in self.p.stdout:
            if line.startswith("LDR "):
                try:
                    self.q.put(json.loads(line[4:]))
                except ValueError:
                    pass
        self.q.put({"ok": False, "dead": True, "error": "máy đọc tắt đột ngột"})

    def _read_err(self):
        assert self.p.stderr
        for line in self.p.stderr:
            line = line.rstrip()
            if line:
                self.err = (self.err + [line[:200]])[-30:]

    def _wait(self, timeout: float, log) -> dict:
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                self.kill()
                return {"ok": False, "error": f"quá {int(timeout)} giây không xong"}
            try:
                msg = self.q.get(timeout=min(left, 5))
            except queue.Empty:
                continue
            if "log" in msg:
                log("  " + msg["log"])
                continue
            if msg.get("dead") and self.err:
                msg["error"] += ": " + " | ".join(e for e in self.err[-3:])
            return msg

    def alive(self) -> bool:
        return self.p.poll() is None

    def say(self, job: dict, log) -> dict:
        with self.lock:          # 1 card đọc lần lượt từng câu
            if not self.alive():
                return {"ok": False, "dead": True, "error": "máy đọc đã tắt"}
            assert self.p.stdin
            self.p.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
            return self._wait(TIMEOUT_SENT, log)

    def kill(self):
        try:
            if self.p.poll() is None:
                self.p.kill()
        except Exception:  # noqa: BLE001
            pass


_WORKERS: dict[str, _Worker] = {}
_GUARD = threading.Lock()


def _get(engine: str, log, device: str) -> _Worker:
    with _GUARD:
        w = _WORKERS.get(engine)
        if w is not None and w.device != device:
            w.kill()
            w.p.wait()
            _WORKERS.pop(engine)
            w = None
        if w is None or not w.alive():
            w = _Worker(engine, log, device)
            _WORKERS[engine] = w
        return w


def stop(engine: str | None = None) -> None:
    with _GUARD:
        for k in list(_WORKERS):
            if engine is None or k == engine:
                _WORKERS.pop(k).kill()


atexit.register(stop)


def synth(engine: str, text: str, sample: Path | None, out: Path, log=print, lang: str = "en",
          steps: int = 16, device: str = "auto") -> float:
    """Đọc 1 câu ra file WAV. Trả về số giây máy đọc mất."""
    job = {"text": text, "ref": str(sample) if sample else "", "out": str(out), "lang": lang, "steps": steps}
    for attempt in (1, 2):
        w = _get(engine, log, device)
        msg = w.say(job, log)
        if msg.get("ok"):
            return float(msg.get("secs") or 0)
        if msg.get("dead") and attempt == 1:
            log("  Máy đọc tắt giữa chừng, khởi động lại…")
            continue
        break
    raise RuntimeError(f"{LABELS[engine].split(' (')[0]}: {msg.get('error') or 'lỗi không rõ'}")
