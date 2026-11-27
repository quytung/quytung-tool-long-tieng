# -*- coding: utf-8 -*-
"""Máy đọc chạy TẠI CHỖ (trên chính PC này): OmniVoice · Chatterbox Multilingual V3 · VieNeu-TTS.

Tệp này KHÔNG chạy bằng Python của tool mà bằng Python riêng của từng máy đọc
(runtime/<máy>/), vì mỗi máy đòi một bộ torch/thư viện khác nhau. Dựng lại theo mẫu
các worker của LD Motion Studio (he_thong/app/voice/workers), gọn cho video 15 giây.

Chạy:  <python của máy> tts_worker.py <may>          (may = omnivoice | chatterbox | vieneu)
Giao tiếp: mỗi dòng stdin là 1 việc JSON {"text","ref","out","lang","steps"};
mỗi việc trả 1 dòng stdout bắt đầu bằng "LDR " + JSON {"ok", "out"|"error", "secs"}.
Model nạp MỘT lần rồi giữ trong bộ nhớ cho cả phiên làm việc (đọc câu sau không phải nạp lại).
Thư viện hay in rác ra stdout, nên dòng trả lời luôn có tiền tố "LDR ".
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import wave
from pathlib import Path


def say(**d) -> None:
    sys.__stdout__.write("LDR " + json.dumps(d, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


def note(msg: str) -> None:
    say(log=msg)


def wav_secs(p: Path) -> float:
    try:
        with wave.open(str(p), "rb") as f:
            return f.getnframes() / float(f.getframerate() or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def pick_device():
    """NVIDIA → cuda (thử một phép tính thật, card đời mới có thể báo có mà không chạy được), Mac → mps, còn lại cpu."""
    import torch
    note(f"Torch {torch.__version__} · CUDA build {torch.version.cuda or 'CPU'} · CUDA khả dụng: {torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        note("⚠ Torch không mở được CUDA. Kiểm tra CUDA build và driver trước khi cài lại.")
    else:
        try:
            x = torch.zeros(8, 8, device="cuda")
            (x + 1).sum().item()
            torch.cuda.synchronize()
            return "cuda", torch.cuda.get_device_name(0)
        except Exception as ex:  # noqa: BLE001
            note(f"Card NVIDIA có nhưng torch chưa chạy được trên card ({str(ex)[:120]}). Dùng CPU (chậm hơn).")
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps", "Apple"
    return "cpu", "CPU"


# ───────────────────────────── OmniVoice (k2-fsa) ─────────────────────────────
class Omni:
    def __init__(self, device="auto"):
        import torch
        from omnivoice import OmniVoice
        if device not in ("auto", "cpu", "cuda"):
            raise ValueError(f"Thiết bị OmniVoice không hợp lệ: {device}")
        dev, name = ("cpu", "CPU") if device == "cpu" else pick_device()
        if device == "cuda" and dev != "cuda":
            raise RuntimeError("Không chạy được CUDA. Kiểm tra Torch và driver NVIDIA hoặc chọn CPU.")
        # fp16 chỉ trên NVIDIA. Trên Mac fp16 từng làm sập tiến trình (ghi chú của LD), CPU thì fp16 chậm hơn.
        dtype = torch.float16 if dev == "cuda" else torch.float32
        self.model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=("cuda:0" if dev == "cuda" else dev),
                                               dtype=dtype)
        self.prompts: dict[str, object] = {}
        note(f"OmniVoice chạy trên {name} ({dev}).")

    def _prompt(self, ref: str):
        """Nhân giọng MỘT lần cho mỗi file mẫu rồi dùng lại: nhanh hơn và mọi câu bám cùng một giọng."""
        if ref in self.prompts:
            return self.prompts[ref]
        from omnivoice import VoiceClonePrompt
        cache = Path(ref).with_suffix(".omnivoice.pt")
        pr = None
        try:
            if cache.is_file() and cache.stat().st_mtime >= Path(ref).stat().st_mtime:
                pr = VoiceClonePrompt.load(str(cache))
            else:
                pr = self.model.create_voice_clone_prompt(ref_audio=ref)
                try:
                    pr.save(str(cache))
                except Exception:  # noqa: BLE001
                    pass
        except Exception as ex:  # noqa: BLE001
            note(f"Nhân giọng sẵn không được ({str(ex)[:100]}), gửi thẳng file mẫu từng câu.")
            pr = None
        self.prompts[ref] = pr
        return pr

    def say(self, job: dict, out: Path) -> None:
        import numpy as np
        import soundfile as sf
        kw: dict = {"text": job["text"], "num_step": int(job.get("steps") or 32)}
        ref = job.get("ref") or ""
        if ref:
            pr = self._prompt(ref)
            if pr is not None:
                kw["voice_clone_prompt"] = pr
            else:
                kw["ref_audio"] = ref
        audio = self.model.generate(**kw)
        sf.write(str(out), np.asarray(audio[0]), 24000)


# ───────────────────────── Chatterbox Multilingual V3 ─────────────────────────
def _fix_perth():
    """Bản cài Perth có lúc không lộ lớp dấu chìm ra ngoài → Chatterbox chết lúc nạp. Lấy lớp thật trong gói con."""
    import perth
    if getattr(perth, "PerthImplicitWatermarker", None) is not None:
        return
    try:
        from perth.perth_net.perth_net_implicit.perth_watermarker import PerthImplicitWatermarker
        perth.PerthImplicitWatermarker = PerthImplicitWatermarker
    except Exception:  # noqa: BLE001
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
        note("Perth thiếu lớp dấu chìm: dùng bản rỗng để vẫn đọc được.")


class Cosy:
    def __init__(self):
        from huggingface_hub import snapshot_download
        source = Path(sys.prefix) / "source"
        sys.path[:0] = [str(source), str(source / "third_party" / "Matcha-TTS")]
        from cosyvoice.cli.cosyvoice import AutoModel
        model = snapshot_download("FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
                                  revision="29e01c4e8d000f4bcd70751be16fa94bf3d85a18",
                                  allow_patterns=["cosyvoice3.yaml", "config.json", "configuration.json",
                                                  "campplus.onnx", "speech_tokenizer_v3.onnx", "llm.pt",
                                                  "flow.pt", "hift.pt", "CosyVoice-BlankEN/*"])
        self.model = AutoModel(model_dir=model)
        if self.model.frontend.device.type == "cpu":
            for module in (self.model.model.llm, self.model.model.flow, self.model.model.hift):
                module.float()
        note(f"CosyVoice chạy trên {self.model.frontend.device} ({self.model.frontend.device.type}).")

    def say(self, job, out):
        import soundfile as sf
        transcript = job["ref_text"].strip()
        if not transcript or not job["ref"]:
            raise ValueError("CosyVoice cần giọng mẫu và lời thoại tương ứng.")
        if sf.info(job["ref"]).duration > 30:
            raise ValueError("Mẫu CosyVoice dài quá 30 giây. Cắt mẫu trong Kho giọng và nhập lời của đoạn đã cắt.")
        prompt = "You are a helpful assistant.<|endofprompt|>" + transcript
        chunks = self.model.inference_zero_shot(job["text"], prompt, job["ref"], stream=False)
        with sf.SoundFile(out, "w", self.model.sample_rate, 1, subtype="PCM_16") as audio:
            for chunk in chunks:
                audio.write(chunk["tts_speech"].detach().cpu().reshape(-1).numpy())


class Chatter:
    def __init__(self):
        import inspect

        import torch
        _fix_perth()
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        dev, name = pick_device()
        if dev == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        else:
            torch.set_num_threads(max(2, (os.cpu_count() or 4) - 2))
        kw = {"device": dev}
        if "t3_model" in inspect.signature(ChatterboxMultilingualTTS.from_pretrained).parameters:
            kw["t3_model"] = "v3"
        try:
            self.model = ChatterboxMultilingualTTS.from_pretrained(**kw)
        except Exception as ex:  # noqa: BLE001
            if dev == "cpu":
                raise
            note(f"Nạp trên {dev} lỗi ({str(ex)[:100]}), thử lại bằng CPU.")
            dev, name = "cpu", "CPU"
            kw["device"] = "cpu"
            self.model = ChatterboxMultilingualTTS.from_pretrained(**kw)
        self.ref = ""
        note(f"Chatterbox Multilingual{' V3' if 't3_model' in kw else ''} chạy trên {name} ({dev}).")

    def say(self, job: dict, out: Path) -> None:
        import torch
        import torchaudio as ta
        ref = job.get("ref") or ""
        if ref and ref != self.ref:
            # nạp mẫu một lần cho mọi câu cùng giọng
            self.model.prepare_conditionals(ref, exaggeration=0.5)
            self.ref = ref
        torch.manual_seed(1234)        # khóa hạt giống: các câu cùng một chất giọng, chạy lại ra y hệt
        wav = self.model.generate(job["text"], language_id=(job.get("lang") or "en"),
                                  exaggeration=0.5, cfg_weight=0.5, temperature=0.8)
        ta.save(str(out), wav, self.model.sr)


# ─────────────────────────────── VieNeu-TTS ───────────────────────────────
class Vieneu:
    def __init__(self):
        n = str(max(1, (os.cpu_count() or 4) - 2))
        os.environ.setdefault("OMP_NUM_THREADS", n)
        os.environ.setdefault("ORT_NUM_THREADS", n)
        from vieneu import Vieneu as V
        try:
            self.model = V(backend="onnx", precision="int8")
        except TypeError:
            self.model = V(backend="onnx")
        note(f"VieNeu chạy bằng ONNX trên CPU ({n} luồng).")

    def say(self, job: dict, out: Path) -> None:
        kw: dict = {"text": job["text"], "apply_watermark": False}
        if job.get("ref"):
            kw["ref_audio"] = job["ref"]
        while True:
            try:
                audio = self.model.infer(**kw)
                break
            except TypeError as ex:      # bản VieNeu cũ không nhận vài tham số: bỏ dần theo tên lỗi
                bad = next((k for k in ("apply_watermark",) if k in kw and k in str(ex)), None)
                if not bad:
                    raise
                kw.pop(bad)
        self.model.save(audio, str(out))


ENGINES = {"cosyvoice": Cosy, "omnivoice": Omni, "chatterbox": Chatter, "vieneu": Vieneu}


def main() -> None:
    which = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if which not in ENGINES:
        say(ok=False, error=f"Máy đọc lạ: {which}")
        sys.exit(2)
    # thư viện in gì ra stdout cũng dồn sang stderr, stdout chỉ dành cho dòng LDR
    sys.stdout = sys.stderr
    t0 = time.time()
    try:
        eng = Omni(sys.argv[2]) if which == "omnivoice" and len(sys.argv) > 2 else ENGINES[which]()
    except Exception as ex:  # noqa: BLE001
        say(ok=False, fatal=True, error=f"Nạp {which} lỗi: {type(ex).__name__}: {ex}",
            trace=traceback.format_exc()[-1500:])
        sys.exit(1)
    say(ready=True, secs=round(time.time() - t0, 1))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError:
            continue
        if job.get("quit"):
            break
        out = Path(job["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        st = time.time()
        try:
            eng.say(job, out)
            if not out.is_file() or wav_secs(out) < 0.2:
                raise RuntimeError("máy đọc chạy xong nhưng không ra tiếng")
            say(ok=True, out=str(out), secs=round(time.time() - st, 1))
        except Exception as ex:  # noqa: BLE001
            say(ok=False, error=f"{type(ex).__name__}: {str(ex)[:300]}", trace=traceback.format_exc()[-1200:])


if __name__ == "__main__":
    main()
