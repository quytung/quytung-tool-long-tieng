"""Desktop entry point."""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from voice import Cancelled, Options, endpoint, render, split_text

DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ToolLongTieng"
SETTINGS = DATA / "settings.json"
STYLES = {
    "Theo giọng mẫu": "",
    "Bản tin": "Đọc rõ ràng, nhịp chắc, nhấn nhẹ từ khóa và số liệu.",
    "Kể chuyện": "Đọc tự nhiên, có nhịp ngắt và nhấn vào các chuyển ý.",
    "Nhẹ nhàng": "Đọc chậm rãi, mềm và ấm, tránh nhấn gắt.",
    "Hào hứng": "Giọng hào hứng, nhấn rõ các ý quan trọng.",
}


class App:
    def __init__(self, root):
        self.root = root
        root.title("Lồng tiếng · OmniVoice VPS")
        root.geometry("780x690")
        root.minsize(650, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.events = queue.Queue()
        self.stop = None
        self.output = None

        defaults = dict(url="", sample="", style="Theo giọng mẫu", instruct="", speed=1.0,
                        gap=0.18, normalize=True, trim=False, sample_seconds=10, workers=2, timeout=120)
        try:
            saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
            for key, default in defaults.items():
                if isinstance(saved.get(key), type(default)):
                    defaults[key] = saved[key]
        except (OSError, ValueError, AttributeError):
            pass
        self.fields = {}
        types = {str: tk.StringVar, float: tk.DoubleVar, int: tk.IntVar, bool: tk.BooleanVar}
        for key, value in defaults.items():
            self.fields[key] = types[type(value)](value=value)

        page = ttk.Frame(root, padding=16)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="Lồng tiếng", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        connection = ttk.LabelFrame(page, text="OmniVoice VPS", padding=10)
        connection.pack(fill="x", pady=(8, 10))
        connection.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("url", "Địa chỉ VPS"), ("sample", "Giọng mẫu"))):
            ttk.Label(connection, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(connection, textvariable=self.fields[key]).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(connection, text="Chọn file…", command=self.pick_sample).grid(row=1, column=2, padx=(8, 0))
        ttk.Label(page, text="Văn bản cần đọc").pack(anchor="w")
        editor = ttk.Frame(page)
        editor.pack(fill="both", expand=True, pady=(3, 8))
        self.text = tk.Text(editor, wrap="word", height=8, undo=True, font=("Segoe UI", 11))
        scrollbar = ttk.Scrollbar(editor, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        tabs = ttk.Notebook(page)
        tabs.pack(fill="x", pady=(0, 8))
        sound = ttk.Frame(tabs, padding=8)
        server = ttk.Frame(tabs, padding=8)
        tabs.add(sound, text="Cách đọc")
        tabs.add(server, text="Thiết lập VPS")
        ttk.Label(sound, text="Phong cách").grid(row=0, column=0, sticky="w")
        ttk.Combobox(sound, textvariable=self.fields["style"], values=list(STYLES), state="readonly").grid(
            row=0, column=1, sticky="w")
        ttk.Label(sound, text="Lời dặn thêm").grid(row=1, column=0, sticky="w")
        ttk.Entry(sound, textvariable=self.fields["instruct"]).grid(row=1, column=1, columnspan=3, sticky="ew")
        sound.columnconfigure(3, weight=1)
        self.spin(sound, "speed", "Tốc độ ×", 2, 0, 0.5, 2.0, 0.05)
        self.spin(sound, "gap", "Nghỉ giữa câu (s)", 2, 2, 0, 3, 0.05)
        ttk.Checkbutton(sound, text="Chuẩn âm lượng −16 LUFS", variable=self.fields["normalize"]).grid(
            row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(sound, text="Cắt lặng đầu/cuối câu", variable=self.fields["trim"]).grid(
            row=3, column=2, columnspan=2, sticky="w")
        self.spin(server, "workers", "Số câu gửi cùng lúc", 0, 0, 1, 8, 1)
        self.spin(server, "timeout", "Thời gian chờ (s)", 1, 0, 15, 600, 15)
        self.spin(server, "sample_seconds", "Độ dài tối đa mẫu (s, 0 = cả file)", 2, 0, 0, 60, 1)

        actions = ttk.Frame(page)
        actions.pack(fill="x")
        self.preview = ttk.Button(actions, text="Nghe thử qua VPS", command=lambda: self.start(True))
        self.export = ttk.Button(actions, text="Xuất WAV / MP3…", command=lambda: self.start(False))
        self.cancel = ttk.Button(actions, text="Dừng", command=self.cancel_job, state="disabled")
        self.play = ttk.Button(actions, text="Mở audio", command=self.open_audio, state="disabled")
        for button in (self.preview, self.export, self.cancel, self.play):
            button.pack(side="left", padx=(0, 6))
        self.bar = ttk.Progressbar(page, mode="determinate")
        self.bar.pack(fill="x", pady=(9, 3))
        self.status = tk.StringVar(value="Dán địa chỉ VPS và chọn file giọng mẫu.")
        ttk.Label(page, textvariable=self.status, wraplength=610).pack(anchor="w")
        root.after(100, self.poll)

    def spin(self, parent, key, label, row, col, low, high, step):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=3)
        ttk.Spinbox(parent, textvariable=self.fields[key], from_=low, to=high, increment=step,
                    width=9, state="readonly").grid(row=row, column=col + 1, sticky="w", padx=(0, 10))

    def pick_sample(self):
        value = filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac")])
        if value:
            self.fields["sample"].set(value)

    def save(self, settings):
        DATA.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS)

    def start(self, preview):
        try:
            settings = {key: field.get() for key, field in self.fields.items()}
            url = endpoint(settings["url"])
            sample = Path(settings["sample"].strip().strip('"'))
            if not sample.is_file():
                raise ValueError("Chọn file giọng mẫu có trên máy.")
            sentences = list(split_text(self.text.get("1.0", "end")))
            if preview:
                sentences = sentences[:1] or ["Xin chào. Đây là bản nghe thử từ máy đọc OmniVoice."]
                destination = DATA / "preview.wav"
            else:
                if not sentences:
                    raise ValueError("Nhập văn bản cần đọc.")
                path = filedialog.asksaveasfilename(
                    defaultextension=".wav", filetypes=[("WAV", "*.wav"), ("MP3", "*.mp3")])
                if not path:
                    return
                destination = Path(path)
                if destination.suffix.lower() not in (".wav", ".mp3"):
                    raise ValueError("Đuôi file phải là WAV hoặc MP3.")
                if destination.resolve() == sample.resolve():
                    raise ValueError("Chọn tên đầu ra khác file giọng mẫu.")
            self.save(settings)
            style = STYLES.get(settings["style"], "")
            settings["instruct"] = " ".join(filter(None, (style, settings["instruct"].strip())))
            options = Options(**{key: settings[key] for key in Options.__dataclass_fields__})
        except (ValueError, OSError, tk.TclError) as exc:
            messagebox.showerror("Lồng tiếng", str(exc))
            return

        self.stop = stop = threading.Event()
        self.preview.configure(state="disabled")
        self.export.configure(state="disabled")
        self.cancel.configure(state="normal")
        self.bar.configure(value=0, maximum=len(sentences))
        self.status.set(f"Đang chuẩn bị {len(sentences)} câu…")

        def work():
            try:
                render(url, sample, sentences, destination, DATA / "cache", options, stop,
                       lambda done, total: self.events.put(("progress", (done, total))), preview)
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            else:
                self.events.put(("done", (destination, preview)))

        threading.Thread(target=work, daemon=True).start()

    def cancel_job(self):
        if self.stop:
            self.stop.set()
            self.cancel.configure(state="disabled")
            self.status.set("Đang dừng. Yêu cầu đã gửi có thể phải chờ VPS phản hồi.")

    def poll(self):
        while not self.events.empty():
            event, value = self.events.get_nowait()
            if event == "progress":
                done, total = value
                self.bar.configure(value=done)
                if not self.stop.is_set():
                    self.status.set(f"Đã xử lý {done}/{total} câu.")
                continue
            self.stop = None
            self.preview.configure(state="normal")
            self.export.configure(state="normal")
            self.cancel.configure(state="disabled")
            if event == "done":
                self.output, preview = value
                self.play.configure(state="normal")
                self.status.set("Đã tạo audio nghe thử." if preview else f"Đã xuất {self.output}")
                if preview:
                    self.open_audio()
            elif event == "cancelled":
                self.status.set("Đã dừng. Các câu đã đọc được giữ để lần sau dùng lại.")
            else:
                self.status.set(value)
        self.root.after(100, self.poll)

    def open_audio(self):
        try:
            if sys.platform == "win32":
                os.startfile(self.output)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.output)])
        except OSError as exc:
            messagebox.showerror("Mở audio", str(exc))

    def close(self):
        if self.stop:
            self.cancel_job()
            return
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
