"""Qt desktop entry point."""
import json
import os
import subprocess
import re
import shutil
import tempfile
from datetime import datetime
from collections import deque
from dataclasses import asdict, replace
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
from subtitles import Cue, parse_srt, read_srt
from voice import Cancelled, Options, endpoint, render, split_text, group_sentences

SETTINGS = DATA / "settings.json"
QUEUE_FILE = DATA / "queue.json"
ENGINES = {
    "vps": ("OmniVoice VPS", "GPU thuê · Dùng URL máy chủ và giọng mẫu"),
    "omni_local": ("OmniVoice Local", "Nhân giọng trên máy · Ưu tiên NVIDIA"),
    "chatterbox": ("Chatterbox Multilingual", "Nhân giọng trên máy · Chọn mã ngôn ngữ"),
    "vieneu": ("VieNeu-TTS", "Giọng tiếng Việt · Chạy trên CPU"),
    "pocket": ("PocketTTS Local", "Tiếng Anh · Nhân giọng từ mẫu · CPU"),
    "cosyvoice": ("CosyVoice 3 Local", "Nhân giọng từ mẫu · Lời thoại mẫu tùy chọn"),
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
            if path.suffix.lower() in (".srt", ".txt"):
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
    authorize_pocket = C.Signal(str, str)
    hardware = C.Signal(str, str)


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
        self.jobs = []
        self.log_entries = deque(maxlen=800)
        self.active_job = None
        self.priority_job = None
        self.devices = {}
        self.reading_engine = None
        self.gpu_status = "Đang dò GPU"
        self.polling_gpu = False
        self.settings = self.load_settings()
        self.signals = Signals(self)
        self.signals.progress.connect(self.progress_update)
        self.signals.log.connect(self.append_log)
        self.signals.success.connect(self.finished)
        self.signals.failure.connect(self.failed)
        self.signals.update_result.connect(self.update_finished)
        self.signals.authorize_pocket.connect(self.open_pocket_login)
        self.signals.hardware.connect(self.hardware_result)

        page = W.QVBoxLayout(self)
        page.setContentsMargins(24, 18, 24, 18)
        page.setSpacing(12)
        header = W.QHBoxLayout()
        page.addLayout(header)
        self.label(header, "Lồng tiếng", "title")
        header.addStretch()
        self.hardware = self.label(header, "", muted=True)
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
        engine_row = W.QHBoxLayout()
        sidebar.addLayout(engine_row)
        self.engines = W.QComboBox()
        self.engines.setSizeAdjustPolicy(W.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.engines.setMinimumContentsLength(8)
        self.engines.addItems([label for label, _ in ENGINES.values()])
        engine_row.addWidget(self.engines, 1)
        self.install = self.button("Cài", self.install_engine)
        engine_row.addWidget(self.install)
        self.label(sidebar, "Hàng đợi", "section")
        self.queue = W.QListWidget()
        self.queue.setHorizontalScrollBarPolicy(C.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue.setTextElideMode(C.Qt.TextElideMode.ElideRight)
        sidebar.addWidget(self.queue, 1)
        self.queue.currentRowChanged.connect(self.queue_selection)
        self.pause_queue = W.QCheckBox("Tạm dừng hàng đợi")
        self.pause_queue.toggled.connect(lambda paused: None if paused else self.next_job())
        sidebar.addWidget(self.pause_queue)
        commands = W.QHBoxLayout()
        self.up_job = self.button("Lên", self.move_job)
        self.retry_job = self.button("Chạy lại", self.retry_selected)
        commands.addWidget(self.up_job)
        commands.addWidget(self.retry_job)
        sidebar.addLayout(commands)

        editor = self.card(grid, 3)
        heading = W.QHBoxLayout()
        editor.addLayout(heading)
        self.label(heading, "Kịch bản dự án", "section")
        heading.addStretch()
        self.open_srt = self.button("Mở TXT / SRT…", self.pick_srt)
        heading.addWidget(self.open_srt)
        project = W.QHBoxLayout()
        editor.addLayout(project)
        project.addWidget(self.button("Bài mới", self.new_project))
        self.project_name = W.QLineEdit()
        self.project_name.setPlaceholderText("Tên dự án (tự lấy dòng đầu nếu bỏ trống)")
        project.addWidget(self.project_name, 1)
        self.text = ScriptEditor()
        self.text.setPlaceholderText("Dán văn bản, nội dung SRT hoặc thả file TXT / SRT…")
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
        self.device_label = self.label(controls, "Thiết bị OmniVoice")
        self.device = W.QComboBox()
        for label, value in (("Tự động", "auto"), ("CPU", "cpu"), ("GPU NVIDIA (CUDA)", "cuda")):
            self.device.addItem(label, value)
        self.device.setCurrentIndex(max(0, self.device.findData(self.settings.get("device", "auto"))))
        controls.addWidget(self.device)
        self.transcripts = self.settings.get("transcripts", {})
        self.transcript_label = self.label(controls, "Lời thoại trong giọng mẫu (tùy chọn)")
        self.transcript = W.QLineEdit(self.transcripts.get(self.sample.text(), ""))
        self.transcript.setPlaceholderText("Có thể để trống nếu chưa biết lời mẫu")
        controls.addWidget(self.transcript)
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
        self.continuous = W.QCheckBox("Đọc liền đoạn")
        self.continuous.setToolTip("Đọc nhiều câu trong một lượt. Giữ mốc SRT vẫn đọc theo từng mục phụ đề.")
        self.continuous.setChecked(self.settings.get("continuous", False))
        controls.addWidget(self.continuous)
        self.save_raw = W.QCheckBox("Lưu WAV thô để chẩn đoán")
        self.save_raw.setChecked(self.settings.get("save_raw", False))
        controls.addWidget(self.save_raw)
        log_header = W.QHBoxLayout()
        self.label(log_header, "Nhật ký trực tiếp", "section")
        log_header.addStretch()
        self.log_filter = W.QComboBox()
        self.log_filter.addItems(["Tất cả", "Cảnh báo", "Lỗi"])
        self.log_filter.currentIndexChanged.connect(self.refresh_log)
        log_header.addWidget(self.log_filter)
        log_header.addWidget(self.button("Chép", self.copy_log))
        controls.addLayout(log_header)
        self.log_view = W.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(80)
        self.log_view.setMaximumBlockCount(800)
        self.log_view.setStyleSheet("font: 11px Consolas; color: #9de4d5;")
        controls.addWidget(self.log_view, 1)

        actions = W.QHBoxLayout()
        page.addLayout(actions)
        self.preview = self.button("Nghe thử", lambda: self.start(True))
        self.export = self.button("Thêm vào hàng chờ", lambda: self.start(False))
        self.run_now = self.button("Chạy", lambda: self.start(False, immediate=True))
        self.run_now.setObjectName("primary")
        self.cancel = self.button("Dừng", self.cancel_job)
        self.play = self.button("Mở thư mục", self.open_folder)
        self.release_link = self.button("Releases", lambda: G.QDesktopServices.openUrl(
            C.QUrl(f"https://github.com/{updater.REPO}/releases")))
        actions.addWidget(self.release_link)
        for button in (self.preview, self.export, self.run_now, self.cancel, self.play):
            actions.addWidget(button)
        actions.addStretch()
        self.progress = W.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        page.addWidget(self.progress)
        self.status = self.label(page, "", muted=True)

        self.engines.currentIndexChanged.connect(self.refresh)
        saved = self.settings.get("engine", "vps")
        self.engines.setCurrentIndex(list(ENGINES).index(saved) if saved in ENGINES else 0)
        self.refresh()
        self.set_busy(False)
        self.restore_queue()
        self.set_status("Chọn máy đọc và nhập văn bản.")
        self.gpu_timer = C.QTimer(self)
        self.gpu_timer.timeout.connect(self.poll_hardware)
        self.gpu_timer.start(5000)
        self.poll_hardware()
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
        return list(ENGINES)[self.engines.currentIndex()]

    def refresh(self):
        engine = self.engine()
        vps, edge = engine == "vps", engine == "edge"
        local = local_tts.is_local(engine)
        self.engines.setToolTip(ENGINES[engine][1] + ("\n" + local_tts.status(engine) if local else ""))
        self.refresh_hardware()
        self.install.setText("Thiết lập PocketTTS" if engine == "pocket" else "Cài")
        self.install.setVisible(local)
        self.install.setToolTip("Cài lại máy đọc" if local and local_tts.is_installed(engine) else "Cài máy đọc")
        for widget, visible in ((self.url, vps), (self.url_label, vps), (self.sample_row, not edge),
                                (self.sample_label, not edge), (self.edge, edge), (self.edge_label, edge),
                                (self.style, vps), (self.style_label, vps), (self.instruct, vps),
                                (self.continuous, engine == "omni_local"), (self.transcript_label, engine == "cosyvoice"), (self.transcript, engine == "cosyvoice"),
                                (self.save_raw, engine == "omni_local"), (self.device, engine == "omni_local"), (self.device_label, engine == "omni_local"),
                                (self.lang, engine == "chatterbox"), (self.lang_label, engine == "chatterbox")):
            widget.setVisible(visible)
        for widget, visible in ((self.cut, not edge and engine != "cosyvoice"), (self.workers, vps or edge), (self.timeout, vps or edge)):
            widget.setVisible(visible)
            self.advanced.labelForField(widget).setVisible(visible)

    def update_script(self):
        text = self.text.toPlainText()
        is_srt = any("-->" in line and line.strip()[:1].isdigit() for line in text.splitlines())
        self.timed.setVisible(is_srt)
        self.count.setText(f"{len(text):,} ký tự" + (" · SRT" if is_srt else ""))

    def pick_srt(self):
        name, _ = W.QFileDialog.getOpenFileName(self, "Mở kịch bản", "", "TXT / SRT (*.txt *.srt)")
        if name:
            self.load_srt(name)

    def load_srt(self, name):
        try:
            path = Path(name)
            script = read_srt(path)
            cues = parse_srt(script) if path.suffix.lower() == ".srt" else []
        except (OSError, ValueError) as exc:
            self.set_status(str(exc))
            return
        self.project_name.setText(path.stem)
        self.text.setPlainText(script)
        self.count.setText(f"{path.name} · {len(cues):,} mục" if cues else path.name)
        self.set_status(f"Đã mở {path.name}.")

    def pick_sample(self):
        dialog = LibraryDialog(self)
        dialog.exec()
        if dialog.selected:
            self.transcripts[self.sample.text()] = self.transcript.text().strip()
            self.sample.setText(dialog.selected.relative_to(ROOT).as_posix())
            self.transcript.setText(self.transcripts.get(self.sample.text(), ""))
            try:
                self.save()
            except OSError as exc:
                self.set_status(str(exc))

    def save(self):
        self.transcripts[self.sample.text()] = self.transcript.text().strip()
        data = dict(transcripts=self.transcripts, ref_text=self.transcript.text().strip(), device=self.device.currentData(), engine=self.engine(), url=self.url.text().strip(), sample=self.sample.text().strip(),
                    edge=self.edge.currentText().strip(), language=self.lang.text().strip() or "en",
                    style=self.style.currentText(), instruct=self.instruct.text().strip(),
                    speed=self.speed.value(), gap=self.gap.value(), sample_seconds=self.cut.value(),
                    workers=self.workers.value(), timeout=self.timeout.value(),
                    continuous=self.continuous.isChecked(), save_raw=self.save_raw.isChecked(), normalize=self.normalize.isChecked(), trim=self.trim.isChecked())
        DATA.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SETTINGS)
        return data

    def new_project(self):
        self.project_name.clear()
        self.text.clear()
        self.timed.setChecked(False)

    def start(self, preview, immediate=False):
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
            is_srt = any("-->" in line and line.strip()[:1].isdigit() for line in script.splitlines())
            combined_cues = parse_srt(script) if is_srt else []
            timed = bool(combined_cues and self.timed.isChecked() and not preview)
            combined = ([line for cue in combined_cues for line in split_text(cue.text)]
                        if combined_cues else list(split_text(script)))
            if engine == "omni_local" and self.continuous.isChecked() and not timed:
                combined = list(group_sentences(combined))
            if preview:
                fallback = "This is a short voice preview." if engine in ("cosyvoice", "pocket") or (engine == "chatterbox" and self.lang.text() == "en") else "Xin chào, đây là giọng đọc thử."
                combined = combined[:1] or [fallback]
            elif not combined:
                raise ValueError("Nhập văn bản cần đọc.")
            data = self.save()
            data["instruct"] = " ".join(filter(None, (STYLES[data["style"]], data["instruct"])))
            if engine == "cosyvoice":
                data["sample_seconds"] = 0
            else:
                data["ref_text"] = ""
            options = Options(**{key: data[key] for key in Options.__dataclass_fields__})
        except (ValueError, OSError, RuntimeError) as exc:
            self.report_error(str(exc))
            return

        name = self.project_name.text().strip() or (combined[0][:45] if not preview else "Nghe thử")
        if preview:
            self.run_audio(dict(name=name, target=target, sample=sample, sentences=combined, options=options,
                                destination=DATA / "preview.wav", cues=None, sections=[]), True)
            return
        try:
            OUTPUT.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w .-]", "_", name).strip(" .")[:50].rstrip(" .") or "Du-an"
            folder = Path(tempfile.mkdtemp(prefix=f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{safe}_", dir=OUTPUT))
        except OSError as exc:
            self.report_error(str(exc))
            return
        job = dict(name=name, target=target, sample=sample, sentences=combined, options=options,
                   destination=folder / "giong-hoan-chinh.wav",
                   cues=sorted(combined_cues, key=lambda cue: cue.start_ms) if timed else None,
                   sections=[], state="Chờ")
        self.jobs.append(job)
        if not self.persist_queue():
            self.jobs.pop()
            folder.rmdir()
            return
        self.refresh_queue(len(self.jobs) - 1)
        self.append_log(f"Đã thêm dự án · {len(combined)} câu")
        if immediate:
            self.priority_job = job
            if self.stop:
                self.cancel_job()
                self.set_status("Đang dừng bài hiện tại để chuyển sang bài mới…")
            else:
                self.next_job()
        else:
            self.set_status("Đã thêm vào hàng chờ.")

    def persist_queue(self):
        docs = []
        for job in self.jobs:
            entry = job.copy()
            entry["destination"] = job["destination"].relative_to(OUTPUT).as_posix()
            entry["sample"] = job["sample"].relative_to(ROOT).as_posix() if job["sample"].is_relative_to(ROOT) else str(job["sample"])
            entry["options"] = asdict(job["options"])
            entry["cues"] = [asdict(cue) for cue in job["cues"]] if job["cues"] else None
            entry["sections"] = [(lines, [asdict(cue) for cue in cues]) for lines, cues in job["sections"]]
            docs.append(entry)
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            temporary = QUEUE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
            temporary.replace(QUEUE_FILE)
            return True
        except OSError as exc:
            self.report_error(str(exc))
            return False

    def restore_queue(self):
        try:
            documents = json.loads(QUEUE_FILE.read_text(encoding="utf-8")) if QUEUE_FILE.exists() else []
            restored = []
            for job in documents:
                job["destination"] = (OUTPUT / job["destination"]).resolve()
                if not job["destination"].is_relative_to(OUTPUT.resolve()) or not job["destination"].parent.is_dir():
                    continue
                job["sample"] = ROOT / job["sample"]
                job["options"] = Options(**job["options"])
                job["cues"] = [Cue(**cue) for cue in job["cues"]] if job["cues"] else None
                job["sections"] = [(lines, [Cue(**cue) for cue in cues]) for lines, cues in job["sections"]]
                if job["state"] == "Đang chạy":
                    job["state"] = "Đã dừng"
                restored.append(job)
            self.jobs = restored
            self.pause_queue.setChecked(any(job["state"] == "Chờ" for job in self.jobs))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.report_error(f"Không mở lại được hàng đợi: {exc}")
        self.refresh_queue(0)

    def refresh_queue(self, selected=None):
        if selected is None:
            selected = self.queue.currentRow()
        with C.QSignalBlocker(self.queue):
            self.queue.clear()
            for job in self.jobs:
                entry = W.QListWidgetItem(f"{job['state']} · {job['name']}")
                entry.setToolTip(entry.text())
                self.queue.addItem(entry)
            self.queue.setCurrentRow(selected)
        self.queue_selection()

    def queue_selection(self, *_):
        row = self.queue.currentRow()
        job = self.jobs[row] if row >= 0 else None
        self.play.setEnabled(bool(job and job["state"] in ("Xong", "Lỗi", "Đã dừng")))
        self.retry_job.setEnabled(bool(job and job["state"] in ("Lỗi", "Đã dừng")))
        self.up_job.setEnabled(bool(job and row > 0 and job["state"] == "Chờ" and self.jobs[row - 1]["state"] == "Chờ"))

    def move_job(self):
        row = self.queue.currentRow()
        self.jobs[row - 1], self.jobs[row] = self.jobs[row], self.jobs[row - 1]
        self.persist_queue()
        self.refresh_queue(row - 1)

    def retry_selected(self):
        self.jobs[self.queue.currentRow()]["state"] = "Chờ"
        self.persist_queue()
        self.refresh_queue()
        self.next_job()

    def next_job(self):
        if self.stop or self.installing or self.updating:
            return
        job = self.priority_job
        self.priority_job = None
        if job is None and not self.pause_queue.isChecked():
            job = next((item for item in self.jobs if item["state"] == "Chờ"), None)
        if job:
            self.active_job = job
            job["state"] = "Đang chạy"
            self.persist_queue()
            self.refresh_queue()
            self.run_audio(job)

    def run_audio(self, job, preview=False):
        self.gpu_timer.setInterval(1000)
        self.poll_hardware()
        self.reading_engine = job["options"].engine
        self.refresh_hardware()
        self.stop = stop = threading.Event()
        self.set_busy(True)
        self.progress.setRange(0, len(job["sentences"]))
        self.progress.setValue(0)
        self.set_status(f"Đang đọc «{job['name']}»…")
        self.append_log(f"Bắt đầu · {job['options'].engine} · {len(job['sentences'])} câu")

        def work():
            destination = job["destination"]
            cache = DATA / "cache"
            resume = destination.parent / ".resume"
            def make(sentences, path, cues):
                render(job["target"], job["sample"], sentences, path, cache, job["options"], stop,
                       self.signals.progress.emit, preview, cues, artifacts=not preview, log=self.signals.log.emit, resume=resume)
            try:
                if job["options"].engine == "omni_local":
                    card, cuda = local_tts.nvidia()
                    driver = f"{cuda[0]}.{cuda[1]}" if cuda != (0, 0) else "không rõ"
                    self.signals.log.emit(f"NVIDIA: {card or 'không dò thấy'} · CUDA driver: {driver}")
                if not destination.exists() or preview:
                    make(job["sentences"], destination, job["cues"])
                for index, (sentences, cues) in enumerate(job["sections"], 1):
                    folder = destination.parent / f"phan-{index:02d}"
                    folder.mkdir(exist_ok=True)
                    path = folder / destination.name
                    if not path.exists():
                        self.signals.log.emit(f"Đang xuất phần {index}")
                        if job["cues"]:
                            offset = cues[0].start_ms
                            cues = [replace(cue, start_ms=cue.start_ms - offset, end_ms=cue.end_ms - offset) for cue in cues]
                        make(sentences, path, cues if job["cues"] else None)
                if not preview:
                    shutil.rmtree(resume, ignore_errors=True)
            except Cancelled:
                self.signals.failure.emit("Đã dừng.")
            except Exception as exc:
                self.signals.failure.emit(str(exc))
            else:
                self.signals.success.emit((destination, preview))

        threading.Thread(target=work, daemon=True).start()

    def open_pocket_login(self, url, code):
        self.set_status(f"Đăng nhập Hugging Face · Mã xác nhận: {code}")
        link = C.QUrl(url)
        if link.scheme() == "https" and link.host() == "huggingface.co":
            if not G.QDesktopServices.openUrl(link):
                W.QMessageBox.information(self, "Đăng nhập Hugging Face",
                                          f"Mở {url} trong trình duyệt. Mã xác nhận: {code}")

    def install_engine(self):
        engine = self.engine()
        if engine == "pocket":
            dialog = W.QDialog(self)
            dialog.setWindowTitle("Thiết lập PocketTTS")
            layout = W.QVBoxLayout(dialog)
            guide = W.QLabel(
                '1. <a href="https://huggingface.co/kyutai/pocket-tts">Mở trang PocketTTS</a>, '
                'đăng nhập và chấp nhận điều khoản nhân giọng.<br><br>'
                '2. Bấm Tiếp tục. Nếu trình duyệt yêu cầu, xác nhận cho phép tool truy cập.<br>'
                'Tool sẽ kiểm tra quyền rồi tự cài máy đọc.')
            guide.setOpenExternalLinks(True)
            layout.addWidget(guide)
            actions = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Cancel)
            actions.addButton("Tiếp tục", W.QDialogButtonBox.ButtonRole.AcceptRole)
            actions.accepted.connect(dialog.accept)
            actions.rejected.connect(dialog.reject)
            layout.addWidget(actions)
            if not dialog.exec():
                return
        self.devices.pop(engine, None)
        self.reading_engine = engine
        self.refresh_hardware()
        self.installing = True
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.set_status(f"Đang cài {ENGINES[engine][0]}…")

        def work():
            try:
                if engine == "pocket":
                    local_tts.prepare_pocket_access(self.signals.log.emit, self.signals.authorize_pocket.emit)
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
            self.report_error(result)
        elif action == "check":
            if result is None:
                self.set_status("Chưa có Release mới hơn.")
                return
            if (ROOT / ".git").exists():
                self.set_status(f"Đã có Release {result}. Dùng Ctrl+T để cập nhật bản clone.")
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
        for widget in (self.preview, self.export, self.run_now, self.engines, self.install, self.open_srt, self.text, self.timed, self.update_button):
            widget.setEnabled(not busy)
        self.cancel.setEnabled(busy and self.stop is not None and not self.installing)
        if self.active_job:
            for widget in (self.export, self.run_now, self.engines, self.open_srt, self.text, self.timed):
                widget.setEnabled(True)
        self.queue_selection()

    def set_status(self, text):
        self.status.setText(text)

    def poll_hardware(self):
        if self.polling_gpu:
            return
        self.polling_gpu = True

        def work():
            executable = shutil.which("nvidia-smi")
            if not executable and sys.platform == "win32":
                path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
                executable = str(path) if path.is_file() else None
            if executable:
                try:
                    result = subprocess.run(
                        [executable, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=4, check=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    name, use, used, total = (field.strip() for field in result.stdout.splitlines()[0].split(","))
                    self.signals.hardware.emit(f"GPU {use}% · VRAM {int(used)/1024:.1f}/{int(total)/1024:.1f} GB", name)
                    return
                except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                    pass
            self.signals.hardware.emit("GPU —", "Không đọc được số liệu NVIDIA")

        threading.Thread(target=work, daemon=True).start()

    def hardware_result(self, status, name):
        self.polling_gpu = False
        self.gpu_status = status
        self.hardware.setToolTip(f"{name} · tải và bộ nhớ GPU của toàn máy")
        self.refresh_hardware()

    def refresh_hardware(self):
        engine = self.reading_engine or self.engine()
        device = {"vps": "VPS", "edge": "Qua mạng", "vieneu": "CPU", "pocket": "CPU"}.get(engine)
        if device is None:
            device = self.devices.get(engine, "Chưa xác nhận")
        self.hardware.setText(f"{self.gpu_status}  |  Máy đọc: {device}")

    def append_log(self, text, level="INFO"):
        device = re.search(r"chạy trên .*\((cuda|cpu|mps)\)\.", text, re.I)
        if device and self.reading_engine:
            self.devices[self.reading_engine] = device[1].upper()
            self.refresh_hardware()
        clean = re.sub(r"https?://[^\s<>]+", "[URL]", str(text), flags=re.I)
        row = f"{datetime.now():%H:%M:%S}  {level}  {clean}"
        if level == "INFO" and ("CẢNH BÁO" in clean or "⚠" in clean):
            level = "CẢNH BÁO"
            row = f"{datetime.now():%H:%M:%S}  {level}  {clean}"
        self.log_entries.append((level, row))
        if self.log_visible(level):
            self.log_view.appendPlainText(row)
        try:
            logs = DATA / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / f"{datetime.now():%Y-%m-%d}.log").open("a", encoding="utf-8") as file:
                file.write(row + "\n")
        except OSError:
            pass

    def report_error(self, text):
        self.append_log(text, "LỖI")
        self.set_status(re.sub(r"https?://[^\s<>]+", "[URL]", str(text), flags=re.I))

    def log_visible(self, level):
        return self.log_filter.currentIndex() == 0 or (self.log_filter.currentIndex() == 1 and level == "CẢNH BÁO") or (self.log_filter.currentIndex() == 2 and level == "LỖI")

    def refresh_log(self, *_):
        self.log_view.setPlainText("\n".join(row for level, row in self.log_entries if self.log_visible(level)))
        self.log_view.moveCursor(G.QTextCursor.MoveOperation.End)

    def copy_log(self):
        W.QApplication.clipboard().setText("\n".join(row for _, row in self.log_entries))

    def progress_update(self, done, total):
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        if self.stop and not self.stop.is_set():
            self.set_status(f"Đã xử lý {done}/{total} câu.")

    def reset(self):
        if self.installing:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        self.stop = None
        self.installing = False
        self.reading_engine = None
        self.gpu_timer.setInterval(5000)
        self.set_busy(False)
        self.refresh()

    def finished(self, result):
        if self.active_job:
            self.active_job["state"] = "Xong"
            self.active_job = None
            self.persist_queue()
            self.refresh_queue()
        self.reset()
        if isinstance(result, str):
            self.set_status(f"Cài xong · {result}")
        else:
            self.output, preview = result
            self.set_status("Đã tạo audio nghe thử." if preview else f"Đã xuất {self.output}")
            if preview:
                self.open_audio()
        self.append_log("Hoàn thành." if not isinstance(result, str) else self.status.text())
        C.QTimer.singleShot(0, self.next_job)

    def failed(self, message):
        if self.active_job:
            self.active_job["state"] = "Đã dừng" if self.stop.is_set() and message == "Đã dừng." else "Lỗi"
            self.active_job = None
            self.persist_queue()
            self.refresh_queue()
        self.reset()
        self.report_error(message)
        C.QTimer.singleShot(0, self.next_job)

    def open_folder(self):
        row = self.queue.currentRow()
        if row >= 0:
            folder = self.jobs[row]["destination"].parent
            G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(folder)))

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
