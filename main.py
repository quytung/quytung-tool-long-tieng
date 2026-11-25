"""Qt desktop entry point."""
import json
import sys
import threading
from pathlib import Path

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

import local_tts
from voice import Cancelled, Options, endpoint, render, split_text

DATA = local_tts.RUNTIME.parent
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


class Signals(C.QObject):
    progress = C.Signal(int, int)
    log = C.Signal(str)
    success = C.Signal(object)
    failure = C.Signal(str)


class App(W.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tool Lồng Tiếng")
        self.resize(1190, 780)
        self.setMinimumSize(900, 690)
        self.stop = None
        self.installing = False
        self.output = None
        self.settings = self.load_settings()
        self.signals = Signals(self)
        self.signals.progress.connect(self.progress_update)
        self.signals.log.connect(self.set_status)
        self.signals.success.connect(self.finished)
        self.signals.failure.connect(self.failed)

        page = W.QVBoxLayout(self)
        page.setContentsMargins(24, 18, 24, 18)
        page.setSpacing(12)
        self.label(page, "Lồng tiếng", "title")
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
        self.label(editor, "Văn bản cần đọc", "section")
        self.text = W.QPlainTextEdit()
        self.text.setPlaceholderText("Dán lời thoại hoặc kịch bản vào đây…")
        editor.addWidget(self.text, 1)
        self.count = self.label(editor, "0 ký tự", muted=True)
        self.text.textChanged.connect(lambda: self.count.setText(f"{len(self.text.toPlainText()):,} ký tự"))

        controls = self.card(grid, 2)
        self.label(controls, "Giọng và cách đọc", "section")
        self.url = W.QLineEdit(self.settings.get("url", ""))
        self.url.setPlaceholderText("Tên miền hoặc IP:cổng")
        self.url.setClearButtonEnabled(True)
        self.url_label = self.label(controls, "Địa chỉ VPS")
        controls.addWidget(self.url)
        self.sample = W.QLineEdit(self.settings.get("sample", ""))
        self.sample.setPlaceholderText("WAV, MP3, FLAC…")
        self.sample_label = self.label(controls, "Giọng mẫu")
        self.sample_row = W.QWidget()
        sample_layout = W.QHBoxLayout(self.sample_row)
        sample_layout.setContentsMargins(0, 0, 0, 0)
        sample_layout.addWidget(self.sample, 1)
        sample_layout.addWidget(self.button("Chọn…", self.pick_sample))
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

    @staticmethod
    def load_settings():
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
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

    def pick_sample(self):
        name, _ = W.QFileDialog.getOpenFileName(self, "Chọn giọng mẫu", "", "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac)")
        if name:
            self.sample.setText(name)

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
            sample = Path(self.sample.text().strip().strip('"'))
            if engine != "edge" and not sample.is_file():
                raise ValueError("Chọn file giọng mẫu có trên máy.")
            if engine == "edge" and not target:
                raise ValueError("Chọn giọng Microsoft.")
            sentences = list(split_text(self.text.toPlainText()))
            if preview:
                fallback = "This is a short voice preview." if engine == "chatterbox" and self.lang.text() == "en" else "Xin chào, đây là giọng đọc thử."
                sentences = sentences[:1] or [fallback]
                destination = DATA / "preview.wav"
            else:
                if not sentences:
                    raise ValueError("Nhập văn bản cần đọc.")
                name, selected = W.QFileDialog.getSaveFileName(self, "Xuất audio", "long-tieng.wav", "WAV (*.wav);;MP3 (*.mp3)")
                if not name:
                    return
                destination = Path(name)
                if not destination.suffix:
                    destination = destination.with_suffix(".mp3" if selected.startswith("MP3") else ".wav")
                if destination.suffix.lower() not in (".wav", ".mp3"):
                    raise ValueError("Đuôi file phải là WAV hoặc MP3.")
                if engine != "edge" and destination.resolve() == sample.resolve():
                    raise ValueError("Chọn tên đầu ra khác file giọng mẫu.")
            data = self.save()
            data["instruct"] = " ".join(filter(None, (STYLES[data["style"]], data["instruct"])))
            options = Options(**{key: data[key] for key in Options.__dataclass_fields__})
        except (ValueError, OSError) as exc:
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
                       self.signals.progress.emit, preview)
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

    def set_busy(self, busy):
        for widget in (self.preview, self.export, self.engines, self.install):
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
        if self.stop or self.installing:
            self.cancel_job()
            event.ignore()
            return
        try:
            self.save()
        except OSError:
            pass
        event.accept()


if __name__ == "__main__":
    application = W.QApplication(sys.argv)
    application.setStyle("Fusion")
    application.setStyleSheet(THEME)
    window = App()
    window.show()
    sys.exit(application.exec())
