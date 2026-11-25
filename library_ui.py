"""Qt dialogs for choosing and preparing voice samples."""
from pathlib import Path
from uuid import uuid4

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

from storage import VOICES, WORK
from voice_library import FILTER, Reference, audio_duration, find_ld, prepare, save_reference


class CropDialog(W.QDialog):
    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source
        self.created = None
        self.setWindowTitle("Cắt mẫu giọng")
        self.resize(430, 230)
        self.total = audio_duration(source)
        if self.total < 0.4:
            raise ValueError("File nguồn quá ngắn.")
        form = W.QFormLayout(self)
        self.name = W.QLineEdit(source.stem)
        self.start = self.time_field(0)
        self.end = self.time_field(min(8, self.total))
        self.denoise = W.QCheckBox("Giảm nhiễu nền")
        self.info = W.QLabel(f"File nguồn dài {self.total:.2f} giây. Chọn đoạn nói rõ, ít nhạc và ít khoảng lặng.")
        self.info.setWordWrap(True)
        form.addRow(self.info)
        form.addRow("Tên giọng", self.name)
        form.addRow("Từ", self.start)
        form.addRow("Đến", self.end)
        form.addRow(self.denoise)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Cancel)
        preview = buttons.addButton("Nghe đoạn cắt", W.QDialogButtonBox.ButtonRole.ActionRole)
        save = buttons.addButton("Lưu giọng", W.QDialogButtonBox.ButtonRole.AcceptRole)
        preview.clicked.connect(self.preview)
        save.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def time_field(self, value):
        widget = W.QDoubleSpinBox()
        widget.setDecimals(3)
        widget.setRange(0, self.total)
        widget.setValue(value)
        widget.setSingleStep(0.25)
        widget.setSuffix(" s")
        return widget

    def reference(self):
        return Reference(self.name.text(), "", self.source, self.start.value(),
                         self.end.value() - self.start.value())

    def preview(self):
        try:
            ref = self.reference()
            target = WORK / "sample-preview" / f"{uuid4().hex}.wav"
            prepare(ref.source, target, ref.start, ref.duration, self.denoise.isChecked())
            if not G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(target))):
                raise ValueError(f"Không mở được trình nghe. File thử ở {target}")
        except (ValueError, OSError, RuntimeError) as exc:
            self.info.setText(str(exc))

    def save(self):
        try:
            self.created = save_reference(self.reference(), self.denoise.isChecked())
        except (ValueError, OSError, RuntimeError) as exc:
            self.info.setText(str(exc))
        else:
            self.accept()


class LibraryDialog(W.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Kho giọng")
        self.resize(550, 420)
        self.selected = None
        layout = W.QVBoxLayout(self)
        actions = W.QHBoxLayout()
        layout.addLayout(actions)
        add = W.QPushButton("Cắt mẫu từ file…")
        add.clicked.connect(self.crop)
        actions.addWidget(add)
        ld = W.QPushButton("Chép giọng từ LD…")
        ld.clicked.connect(self.import_ld)
        actions.addWidget(ld)
        self.entries = W.QListWidget()
        layout.addWidget(self.entries)
        self.status = W.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Close)
        use = buttons.addButton("Dùng giọng đã chọn", W.QDialogButtonBox.ButtonRole.AcceptRole)
        use.clicked.connect(self.choose)
        buttons.rejected.connect(self.accept)
        self.entries.itemDoubleClicked.connect(self.choose)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self, focus=None):
        self.entries.clear()
        for path in sorted(VOICES.glob("*.wav"), key=lambda p: p.name.casefold()):
            item = W.QListWidgetItem(path.stem)
            item.setData(C.Qt.ItemDataRole.UserRole, str(path))
            self.entries.addItem(item)
            if path == focus:
                self.entries.setCurrentItem(item)
        if not self.entries.count():
            self.status.setText("Kho chưa có giọng. Cắt mẫu từ file hoặc chép từ LD.")

    def choose(self, *_):
        item = self.entries.currentItem()
        if item:
            self.selected = Path(item.data(C.Qt.ItemDataRole.UserRole))
            self.accept()

    def crop(self):
        name, _ = W.QFileDialog.getOpenFileName(self, "Chọn file nguồn", "", FILTER)
        if not name:
            return
        try:
            dialog = CropDialog(Path(name), self)
            if dialog.exec() and dialog.created:
                self.refresh(dialog.created)
                self.status.setText(f"Đã tạo giọng {dialog.created.stem}.")
        except Exception as exc:
            self.status.setText(str(exc))

    def import_ld(self):
        root = W.QFileDialog.getExistingDirectory(self, "Chọn thư mục LD hoặc thư mục voices")
        if not root:
            return
        try:
            references = find_ld(Path(root))
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        if not references:
            self.status.setText("Không tìm thấy mẫu đọc được. Thử chọn trực tiếp thư mục voices có library.json.")
            return

        picker = W.QDialog(self)
        picker.setWindowTitle("Chọn giọng LD muốn chép")
        picker.resize(500, 370)
        layout = W.QVBoxLayout(picker)
        items = W.QListWidget()
        items.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
        for ref in references:
            item = W.QListWidgetItem(f"{ref.name} · {ref.language}" if ref.language else ref.name)
            item.setToolTip(str(ref.source))
            items.addItem(item)
        layout.addWidget(W.QLabel("Giữ Ctrl hoặc Shift để chọn nhiều giọng."))
        layout.addWidget(items)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Cancel)
        copy = buttons.addButton("Chép giọng đã chọn", W.QDialogButtonBox.ButtonRole.AcceptRole)
        copy.setEnabled(False)
        items.itemSelectionChanged.connect(lambda: copy.setEnabled(bool(items.selectedItems())))
        buttons.accepted.connect(picker.accept)
        buttons.rejected.connect(picker.reject)
        layout.addWidget(buttons)
        if not picker.exec():
            return

        imported = []
        errors = []
        W.QApplication.setOverrideCursor(C.Qt.CursorShape.WaitCursor)
        try:
            for selection in items.selectedIndexes():
                ref = references[selection.row()]
                try:
                    imported.append(save_reference(ref))
                except Exception as exc:
                    errors.append(f"{ref.name}: {exc}")
        finally:
            W.QApplication.restoreOverrideCursor()
        self.refresh(imported[0] if imported else None)
        self.status.setText(f"Đã chép {len(imported)} giọng." + (" " + "\n".join(errors) if errors else ""))
