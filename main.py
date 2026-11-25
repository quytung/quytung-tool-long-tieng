"""Qt desktop entry point."""
import json
import sys
import threading
from pathlib import Path

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

import local_tts
import updater
from version import VERSION
from library_ui import LibraryDialog
from storage import DATA, LEGACY, OUTPUT, ROOT, sample_path
from voice_library import Reference, save_reference
from subtitles import parse_srt, read_srt
from voice import Cancelled, Options, endpoint, render, split_text

SETTINGS = DATA / "settings.json"
ENGINES = {
    "vps": ("OmniVoice VPS", "GPU thuê · Dùng URL máy chủ và giọng mẫu"),
    "omni_local": ("OmniVoice Local", "Nhân giọng trên máy · Ưu tiên NVIDIA"),
    "chatterbox": ("Chatterbox Multilingual", "Nhân giọng trên máy · Chọn mã ngôn ngữ"),
    "vieneu": ("VieNeu-TTS", "Giọng tiếng Việt · Chạy trên CPU"),
    "edge": ("Microsoft Edge TTS", "Giọng dựng sẵn · Cần Internet"),
}
STYLES = {
    "Theo giọng mẫu": "",
    "Bản tin": "Đọc rõ ràng, nhịp chắc, nhấn nhẹ từ khóa và số liệu.",
    "Kể chuyện": "Đọc tự nhiên, có nhịp ngắt và nhấn vào các chuyển ý.",
    "Nhẹ nhàng": "Đọc mềm và ấm, tránh nhấn gắt.",
    "Hào hứng": "Giọng hào hứng, nhấn rõ các ý quan trọng.",
}
THEME = """
QWidget { background: #101620; color: #e5edf7; font: 14px 'Segoe UI'; }
QFrame#card { background: #161f2c; border: 1px solid #273548; border-radius: 12px; }
QFrame#card QLabel, QLabel[muted="true"] { background: transparent; }
QLabel[muted="true"] { color: #9caec4; }
QLabel#title { font-size: 30px; font-weight: 700; }
QLabel#section { font-size: 17px; font-weight: 650; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget {
    background: #0e1520; border: 1px solid #304156; padding: 7px; border-radius: 7px;
    selection-background-color: #126b65;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #53d9bd; }
QComboBox::drop-down { width: 23px; border: 0; }
QComboBox::down-arrow { image: none; width: 0; height: 0; border: 5px solid transparent;
    border-top-color: #a6c9ce; margin-top: 5px; }
QListWidget::item { padding: 9px 7px; border-radius: 6px; margin: 2px; }
QListWidget::item:selected { background: #184640; color: #b3f8e7; }
QPushButton { background: #223145; border: 1px solid #33485f; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover { background: #2c4157; }
QPushButton#primary { background: #65e2c2; border-color: #65e2c2; color: #102823; font-weight: 700; }
QPushButton#primary:hover { background: #97f4de; }
QPushButton:disabled { background: #1b2634; color: #71849a; border-color: #28394c; }
QProgressBar { background: #202c3c; border: 0; border-radius: 4px; height: 7px; text-align: center; }
QProgressBar::chunk { background: #65e2c2; border-radius: 4px; }
QCheckBox { background: transparent; spacing: 7px; }
"""


class ScriptEditor(W.QPlainTextEdit):
    subtitle_dropped = C.Signal(str)

    @staticmethod
    def subtitle_path(mime):
        urls = mime.urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.suffix.lower() == ".srt":
                return str(path)
        return ""

    def dragEnterEvent(self, event):
        if self.isEnabled() and self.subtitle_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.isEnabled() and self.subtitle_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self.subtitle_path(event.mimeData())
        if self.isEnabled() and path:
            self.subtitle_dropped.emit(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class Signals(C.QObject):
    progress = C.Signal(int, int)
    log = C.Signal(str)
    success = C.Signal(object)
    failure = C.Signal(str)
    update_result = C.Signal(str, object)


class App(W.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tool Lồng Tiếng")
        self.resize(1190, 780)
        self.setMinimumSize(900, 690)
        self.stop = None
        self.installing = False
        self.output = None
        self.updating = False
        self.settings = self.load_settings()
        self.signals = Signals(self)
        self.signals.progress.connect(self.progress_update)
        self.signals.log.connect(self.set_status)
        self.signals.success.connect(self.finished)
        self.signals.failure.connect(self.failed)
        self.signals.update_result.connect(self.update_finished)

        page = W.QVBoxLayout(self)
        page.setContentsMargins(24, 18, 24, 18)
        page.setSpacing(12)
        header = W.QHBoxLayout()
        page.addLayout(header)
        self.label(header, "Lồng tiếng", "title")
        header.addStretch()
        self.label(header, f"v{VERSION}", muted=True)
        self.update_button = self.button("Cập nhật", self.check_update)
        header.addWidget(self.update_button)
        self.update_button.setVisible(sys.platform == "win32")
        grid = W.QHBoxLayout()
        grid.setSpacing(12)
        page.addLayout(grid, 1)
        sidebar = self.card(grid, 0)
        sidebar.parentWidget().setFixedWidth(232)
        self.label(sidebar, "Máy đọc", "section")
        self.engines = W.QListWidget()
        self.engines.addItems([info[0] for info in ENGINES.values()])
        self.engines.setFixedHeight(224)
        sidebar.addWidget(self.engines)
        self.engine_note = self.label(sidebar, "", muted=True)
        self.install = self.button("Cài máy đọc", self.install_engine)
        sidebar.addWidget(self.install)
        sidebar.addStretch()

        editor = self.card(grid, 3)
        heading = W.QHBoxLayout()
        editor.addLayout(heading)
        self.label(heading, "Văn bản cần đọc", "section")
        heading.addStretch()
        self.open_srt = self.button("Mở SRT…", self.pick_srt)
        heading.addWidget(self.open_srt)
        self.text = ScriptEditor()
        self.text.setPlaceholderText("Dán văn bản hoặc thả một file .srt vào đây…")
        self.text.subtitle_dropped.connect(self.load_srt)
        editor.addWidget(self.text, 1)
        self.count = self.label(editor, "0 ký tự", muted=True)
        self.timed = W.QCheckBox("Giữ mốc thời gian SRT")
        self.timed.setToolTip("Mặc định đọc nối tiếp. Bật để giữ mốc, chèn khoảng lặng và tăng tốc câu dài khi cần.")
        editor.addWidget(self.timed)
        self.text.textChanged.connect(self.update_script)
        self.update_script()

        controls = self.card(grid, 2)
        self.label(controls, "Giọng và cách đọc", "section")
        self.url = W.QLineEdit(self.settings.get("url", ""))
        self.url.setPlaceholderText("Tên miền hoặc IP:cổng")
        self.url.setClearButtonEnabled(True)
        self.url_label = self.label(controls, "Địa chỉ VPS")
        controls.addWidget(self.url)
        self.sample = W.QLineEdit(self.settings.get("sample", ""))
        self.sample.setPlaceholderText("Chọn hoặc thêm trong kho giọng")
        self.sample.setReadOnly(True)
        self.sample_label = self.label(controls, "Giọng mẫu")
        self.sample_row = W.QWidget()
        sample_layout = W.QHBoxLayout(self.sample_row)
        sample_layout.setContentsMargins(0, 0, 0, 0)
        sample_layout.addWidget(self.sample, 1)
        sample_layout.addWidget(self.button("Kho giọng…", self.pick_sample))
        controls.addWidget(self.sample_row)
        self.edge = W.QComboBox()
        self.edge.setEditable(True)
        self.edge.addItems(["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural", "en-US-AriaNeural", "en-US-GuyNeural"])
        self.edge.setCurrentText(self.settings.get("edge", "vi-VN-HoaiMyNeural"))
        self.edge_label = self.label(controls, "Giọng Microsoft")
        controls.addWidget(self.edge)
        self.lang = W.QLineEdit(self.settings.get("language", "en"))
        self.lang.setPlaceholderText("en, fr, es, zh…")
        self.lang_label = self.label(controls, "Mã ngôn ngữ Chatterbox")
        controls.addWidget(self.lang)
        self.style = W.QComboBox()
        self.style.addItems(STYLES)
        self.style.setCurrentText(self.settings.get("style", "Theo giọng mẫu"))
        self.style_label = self.label(controls, "Phong cách OmniVoice VPS")
        controls.addWidget(self.style)
        self.instruct = W.QLineEdit(self.settings.get("instruct", ""))
        self.instruct.setPlaceholderText("Lời dặn nhấn nhá, ngữ điệu…")
        controls.addWidget(self.instruct)

        settings_form = W.QFormLayout()
        controls.addLayout(settings_form)
        self.speed = self.spin(settings_form, "speed", "Tốc độ", 0.5, 2.0, 1.0, 0.05, " ×")
        self.gap = self.spin(settings_form, "gap", "Nghỉ giữa câu", 0.0, 3.0, 0.18, 0.05, " s")
        self.cut = self.spin(settings_form, "sample_seconds", "Giới hạn mẫu", 0, 60, 10, 1, " s")
        self.cut.setSpecialValueText("Cả file")
        self.workers = self.spin(settings_form, "workers", "Số câu đồng thời", 1, 8, 2, 1)
        self.timeout = self.spin(settings_form, "timeout", "Thời gian chờ", 15, 600, 120, 15, " s")
        self.advanced = settings_form
        self.normalize = W.QCheckBox("Chuẩn âm lượng −16 LUFS")
        self.normalize.setChecked(self.settings.get("normalize", True))
        self.trim = W.QCheckBox("Cắt lặng đầu và cuối câu")
        self.trim.setChecked(self.settings.get("trim", False))
        controls.addWidget(self.normalize)
        controls.addWidget(self.trim)
        controls.addStretch()

        actions = W.QHBoxLayout()
        page.addLayout(actions)
        self.preview = self.button("Nghe thử", lambda: self.start(True))
        self.export = self.button("Xuất WAV / MP3…", lambda: self.start(False))
        self.export.setObjectName("primary")
        self.cancel = self.button("Dừng", self.cancel_job)
        self.play = self.button("Mở audio", self.open_audio)
        for button in (self.preview, self.export, self.cancel, self.play):
            actions.addWidget(button)
        actions.addStretch()
        self.progress = W.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        page.addWidget(self.progress)
        self.status = self.label(page, "", muted=True)

        self.engines.currentRowChanged.connect(self.refresh)
        saved = self.settings.get("engine", "vps")
        self.engines.setCurrentRow(list(ENGINES).index(saved) if saved in ENGINES else 0)
        self.set_busy(False)
        self.set_status("Chọn máy đọc và nhập văn bản.")
        draft = DATA / "updates" / "draft.txt"
        try:
            self.text.setPlainText(draft.read_text(encoding="utf-8"))
            draft.unlink()
        except OSError:
            pass

    @staticmethod
    def load_settings():
        try:
            src = SETTINGS if SETTINGS.exists() else LEGACY / "settings.json"
            data = json.loads(src.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def label(layout, text, name="", muted=False):
        label = W.QLabel(text)
        label.setObjectName(name)
        label.setProperty("muted", muted)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    @staticmethod
    def card(row, stretch):
        frame = W.QFrame()
        frame.setObjectName("card")
        row.addWidget(frame, stretch)
        layout = W.QVBoxLayout(frame)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(8)
        return layout

    @staticmethod
    def button(text, action):
        button = W.QPushButton(text)
        button.clicked.connect(action)
        return button

    def spin(self, form, key, label, low, high, value, step, suffix=""):
        widget = W.QDoubleSpinBox() if isinstance(value, float) else W.QSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setSuffix(suffix)
        saved = self.settings.get(key, value)
        widget.setValue(saved if isinstance(saved, type(value)) else value)
        form.addRow(label, widget)
        return widget

    def engine(self):
        return list(ENGINES)[self.engines.currentRow()]

    def refresh(self):
        engine = self.engine()
        vps, edge = engine == "vps", engine == "edge"
        local = local_tts.is_local(engine)
        self.engine_note.setText(ENGINES[engine][1] + ("\n" + local_tts.status(engine) if local else ""))
        self.install.setVisible(local)
        self.install.setText("Cài lại máy đọc" if local and local_tts.is_installed(engine) else "Cài máy đọc")
        for widget, visible in ((self.url, vps), (self.url_label, vps), (self.sample_row, not edge),
                                (self.sample_label, not edge), (self.edge, edge), (self.edge_label, edge),
                                (self.style, vps), (self.style_label, vps), (self.instruct, vps),
                                (self.lang, engine == "chatterbox"), (self.lang_label, engine == "chatterbox")):
            widget.setVisible(visible)
        for widget, visible in ((self.cut, not edge), (self.workers, vps or edge), (self.timeout, vps or edge)):
            widget.setVisible(visible)
            self.advanced.labelForField(widget).setVisible(visible)

    def update_script(self):
        text = self.text.toPlainText()
        is_srt = any("-->" in line and line.strip()[:1].isdigit() for line in text.splitlines())
        self.timed.setVisible(is_srt)
        self.count.setText(f"{len(text):,} ký tự" + (" · SRT" if is_srt else ""))

    def pick_srt(self):
        name, _ = W.QFileDialog.getOpenFileName(self, "Mở phụ đề", "", "SubRip (*.srt)")
        if name:
            self.load_srt(name)

    def load_srt(self, name):
        try:
            path = Path(name)
            script = read_srt(path)
            cues = parse_srt(script)
        except (OSError, ValueError) as exc:
            self.set_status(str(exc))
            return
        self.text.setPlainText(script)
        self.count.setText(f"{path.name} · {len(cues):,} mục")
        self.set_status("Đã mở SRT. Bật giữ mốc nếu cần WAV khớp phụ đề.")

    def pick_sample(self):
        dialog = LibraryDialog(self)
        dialog.exec()
        if dialog.selected:
            self.sample.setText(dialog.selected.relative_to(ROOT).as_posix())
            try:
                self.save()
            except OSError as exc:
                self.set_status(str(exc))

    def save(self):
        data = dict(engine=self.engine(), url=self.url.text().strip(), sample=self.sample.text().strip(),
                    edge=self.edge.currentText().strip(), language=self.lang.text().strip() or "en",
                    style=self.style.currentText(), instruct=self.instruct.text().strip(),
                    speed=self.speed.value(), gap=self.gap.value(), sample_seconds=self.cut.value(),
                    workers=self.workers.value(), timeout=self.timeout.value(),
                    normalize=self.normalize.isChecked(), trim=self.trim.isChecked())
        DATA.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS)
        return data

    def start(self, preview):
        try:
            engine = self.engine()
            if local_tts.is_local(engine) and not local_tts.is_installed(engine):
                raise ValueError("Bấm Cài máy đọc trước khi dùng engine này.")
            target = endpoint(self.url.text()) if engine == "vps" else self.edge.currentText().strip()
            sample = sample_path(self.sample.text())
            if engine != "edge" and not sample.is_file():
                raise ValueError("Chọn file giọng mẫu có trên máy.")
            if engine == "edge" and not target:
                raise ValueError("Chọn giọng Microsoft.")
            if engine != "edge" and not sample.resolve().is_relative_to(DATA.resolve()):
                sample = save_reference(Reference(sample.stem, "", sample))
                self.sample.setText(sample.relative_to(ROOT).as_posix())
            script = self.text.toPlainText()
            subtitles = parse_srt(script) if not self.timed.isHidden() else []
            sentences = [segment for cue in subtitles for segment in split_text(cue.text)] if subtitles else list(split_text(script))
            cues = subtitles if subtitles and self.timed.isChecked() and not preview else None
            if preview:
                fallback = "This is a short voice preview." if engine == "chatterbox" and self.lang.text() == "en" else "Xin chào, đây là giọng đọc thử."
                sentences = sentences[:1] or [fallback]
                destination = DATA / "preview.wav"
            else:
                if not sentences:
                    raise ValueError("Nhập văn bản cần đọc.")
                OUTPUT.mkdir(parents=True, exist_ok=True)
                name, selected = W.QFileDialog.getSaveFileName(
                    self, "Xuất audio", str(OUTPUT / "long-tieng.wav"), "WAV (*.wav);;MP3 (*.mp3)")
                if not name:
                    return
                destination = Path(name)
                if not destination.suffix:
                    destination = destination.with_suffix(".mp3" if selected.startswith("MP3") else ".wav")
                if not destination.resolve().is_relative_to(OUTPUT.resolve()):
                    raise ValueError(f"Chọn file trong thư mục xuất của tool: {OUTPUT}")
                if destination.suffix.lower() not in (".wav", ".mp3"):
                    raise ValueError("Đuôi file phải là WAV hoặc MP3.")
                if engine != "edge" and destination.resolve() == sample.resolve():
                    raise ValueError("Chọn tên đầu ra khác file giọng mẫu.")
            data = self.save()
            data["instruct"] = " ".join(filter(None, (STYLES[data["style"]], data["instruct"])))
            options = Options(**{key: data[key] for key in Options.__dataclass_fields__})
        except (ValueError, OSError, RuntimeError) as exc:
            self.set_status(str(exc))
            return

        self.stop = stop = threading.Event()
        self.set_busy(True)
        self.progress.setRange(0, len(sentences))
        self.progress.setValue(0)
        self.set_status(f"Đang chuẩn bị {len(sentences)} câu…")

        def work():
            try:
                render(target, sample, sentences, destination, DATA / "cache", options, stop,
                       self.signals.progress.emit, preview, cues)
            except Cancelled:
                self.signals.failure.emit("Đã dừng.")
            except Exception as exc:
                self.signals.failure.emit(str(exc))
            else:
                self.signals.success.emit((destination, preview))

        threading.Thread(target=work, daemon=True).start()

    def install_engine(self):
        engine = self.engine()
        self.installing = True
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.set_status(f"Đang cài {ENGINES[engine][0]}…")

        def work():
            try:
                summary = local_tts.install(engine, log=self.signals.log.emit)
            except Exception as exc:
                self.signals.failure.emit(str(exc))
            else:
                self.signals.success.emit(summary)

        threading.Thread(target=work, daemon=True).start()

    def check_update(self):
        self.update_task("check", updater.latest)

    def update_task(self, action, task):
        self.updating = True
        self.set_busy(True)
        self.cancel.setEnabled(False)
        self.progress.setRange(0, 0)
        self.set_status("Đang kiểm tra bản mới…" if action == "check" else "Đang tải và kiểm tra bản cập nhật…")

        def work():
            try:
                result = task()
            except Exception as exc:
                self.signals.update_result.emit("error", str(exc))
            else:
                self.signals.update_result.emit(action, result)

        threading.Thread(target=work, daemon=True).start()

    def update_finished(self, action, result):
        self.updating = False
        self.set_busy(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if action == "error":
            self.set_status(result)
        elif action == "check":
            if result is None:
                self.set_status("Chưa có Release mới hơn.")
                return
            if (ROOT / ".git").exists():
                self.set_status(f"Đã có Release {result}. Dùng Ctrl+T trong PyCharm để cập nhật bản clone.")
                G.QDesktopServices.openUrl(C.QUrl(f"https://github.com/{updater.REPO}/releases/tag/{result}"))
                return
            choice = W.QMessageBox.question(
                self, "Cập nhật", f"Đã có bản {result}. Tải về và khởi động lại sau khi cài?")
            if choice == W.QMessageBox.StandardButton.Yes:
                self.update_task("apply", lambda: updater.download(result))
            else:
                self.set_status(f"Đã có bản {result}.")
        else:
            draft = DATA / "updates" / "draft.txt"
            try:
                self.save()
                draft.write_text(self.text.toPlainText(), encoding="utf-8")
                updater.launch_helper(result)
            except OSError as exc:
                self.set_status(str(exc))
                return
            self.close()

    def set_busy(self, busy):
        for widget in (self.preview, self.export, self.engines, self.install, self.open_srt, self.text, self.timed, self.update_button):
            widget.setEnabled(not busy)
        self.cancel.setEnabled(busy and not self.installing)
        self.play.setEnabled(self.output is not None)

    def set_status(self, text):
        self.status.setText(text)

    def progress_update(self, done, total):
        self.progress.setValue(done)
        if self.stop and not self.stop.is_set():
            self.set_status(f"Đã xử lý {done}/{total} câu.")

    def reset(self):
        if self.installing:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        self.stop = None
        self.installing = False
        self.set_busy(False)
        self.refresh()

    def finished(self, result):
        self.reset()
        if isinstance(result, str):
            self.set_status(f"Cài xong · {result}")
            return
        self.output, preview = result
        self.play.setEnabled(True)
        self.set_status("Đã tạo audio nghe thử." if preview else f"Đã xuất {self.output}")
        if preview:
            self.open_audio()

    def failed(self, message):
        self.reset()
        self.set_status(message)

    def cancel_job(self):
        if self.stop:
            self.stop.set()
            self.cancel.setEnabled(False)
            self.set_status("Đang dừng. Chờ các yêu cầu đã gửi hoàn tất.")

    def open_audio(self):
        if self.output and not G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(self.output))):
            self.set_status(f"Không mở được audio. File nằm ở {self.output}")

    def closeEvent(self, event):
        if self.updating:
            event.ignore()
            return
        if self.stop or self.installing:
            self.cancel_job()
            event.ignore()
            return
        try:
            self.save()
        except OSError:
            pass
        event.accept()


def run():
    window = App()
    window.show()
    return application.exec()


if __name__ == "__main__":
    application = W.QApplication(sys.argv)
    application.setStyle("Fusion")
    application.setStyleSheet(THEME)
    if sys.platform == "win32" and not (ROOT / ".git").exists():
        try:
            with updater.portable_lock(ROOT):
                exit_code = run()
        except (OSError, RuntimeError) as exc:
            W.QMessageBox.critical(None, "Tool Lồng Tiếng", str(exc))
            exit_code = 1
    else:
        exit_code = run()
    sys.exit(exit_code)
