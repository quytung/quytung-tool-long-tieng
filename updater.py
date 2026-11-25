"""Download and install the portable Windows release."""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

from version import VERSION

ROOT = Path(__file__).resolve().parent
UPDATES = ROOT / "data" / "updates"
REPO = "quytung/quytung-tool-long-tieng"
PACKAGE = "Tool_Long_Tieng.zip"
LIMIT = 20 * 1024 * 1024
FILES = (
    "CHAY TOOL.bat", "README.md", "requirements.txt", "main.py",
    "library_ui.py", "local_tts.py", "storage.py", "subtitles.py",
    "tts_worker.py", "voice.py", "voice_library.py", "updater.py", "version.py",
)


def version_tuple(text):
    if not re.fullmatch(r"v?\d{1,6}\.\d{1,6}\.\d{1,6}", text):
        raise ValueError("Phiên bản cập nhật không hợp lệ.")
    return tuple(map(int, text.lstrip("v").split(".")))


def latest():
    import requests

    query = f"https://api.github.com/repos/{REPO}/releases/latest"
    response = requests.get(query, timeout=(10, 20))
    if response.status_code == 404:
        return None
    response.raise_for_status()
    release = response.json()
    tag = release["tag_name"]
    if version_tuple(tag) <= version_tuple(VERSION):
        return None
    assets = {asset["name"] for asset in release["assets"]}
    if not {PACKAGE, PACKAGE + ".sha256"} <= assets:
        raise ValueError("Release mới chưa có đủ ZIP và mã SHA-256.")
    return tag


def download(tag):
    import requests

    version_tuple(tag)
    base = f"https://github.com/{REPO}/releases/download/{tag}/{PACKAGE}"
    digest = requests.get(base + ".sha256", timeout=(10, 20))
    digest.raise_for_status()
    fields = digest.text.split()
    if not fields or not re.fullmatch("[a-fA-F0-9]{64}", fields[0]):
        raise ValueError("Mã SHA-256 của Release không hợp lệ.")
    UPDATES.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="update-", dir=UPDATES))
    try:
        archive = directory / PACKAGE
        sha = hashlib.sha256()
        with requests.get(base, stream=True, timeout=(10, 30)) as response, archive.open("wb") as file:
            response.raise_for_status()
            total = 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > LIMIT:
                    raise ValueError("Gói cập nhật vượt kích thước cho phép.")
                sha.update(chunk)
                file.write(chunk)
        if sha.hexdigest() != fields[0].lower():
            raise ValueError("SHA-256 sai. Tải lại bản cập nhật.")
        staged = directory / "staged"
        unpack(archive, staged, tag)
        return directory
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def unpack(archive, staged, tag):
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or set(names) != set(FILES):
            raise ValueError("Danh sách file trong ZIP không đúng.")
        if sum(entry.file_size for entry in entries) > LIMIT or any(
            entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) for entry in entries
        ):
            raise ValueError("Nội dung gói cập nhật không hợp lệ.")
        staged.mkdir()
        for entry in entries:
            (staged / entry.filename).write_bytes(bundle.read(entry))
    expected = f'VERSION = "{tag.lstrip("v")}"'
    if (staged / "version.py").read_text(encoding="utf-8").strip() != expected:
        raise ValueError("Phiên bản trong ZIP không khớp Release.")
    for source in staged.glob("*.py"):
        compile(source.read_bytes(), source.name, "exec")


@contextmanager
def portable_lock(root, timeout=0):
    import msvcrt

    folder = root / "data"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "portable.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        deadline = time.monotonic() + timeout
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Bản ZIP đang mở. Đóng phiên đang chạy rồi thử lại.") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def apply(root, directory):
    backup = directory / "backup"
    backup.mkdir()
    staged = directory / "staged"
    pending = directory / "pending"
    shutil.copytree(staged, pending)
    for name in FILES:
        target = root / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Không thể cập nhật file {name}.")
        if target.exists():
            shutil.copy2(target, backup / name)
    changed = []
    try:
        for name in FILES:
            os.replace(pending / name, root / name)
            changed.append(name)
    except OSError as error:
        failures = []
        for name in reversed(changed):
            previous = backup / name
            try:
                if previous.exists():
                    os.replace(previous, root / name)
                else:
                    (root / name).unlink()
            except OSError:
                failures.append(name)
        if failures:
            raise RuntimeError(f"Không thể phục hồi {', '.join(failures)}. Xem bản sao tại {backup}") from error
        raise


def launch_helper(directory):
    helper = directory / "apply_update.py"
    shutil.copy2(__file__, helper)
    shutil.copy2(ROOT / "version.py", directory / "version.py")
    subprocess.Popen([sys.executable, str(helper), str(ROOT), str(directory)],
                     cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)


def apply_and_restart(root, directory):
    log = root / "data" / "updates" / "last-update.txt"
    try:
        with portable_lock(root, timeout=30):
            apply(root, directory)
            log.write_text(f"Đã cập nhật tại {time.ctime()}\nSao lưu: {directory / 'backup'}", encoding="utf-8")
    except Exception as error:
        log.write_text(str(error), encoding="utf-8")
        print(f"Cập nhật lỗi: {error}", flush=True)
        input("Nhấn Enter để đóng. Có thể mở lại bằng CHAY TOOL.bat.")
        return 1
    subprocess.Popen(["cmd.exe", "/d", "/c", "CHAY TOOL.bat"], cwd=root)
    return 0


if __name__ == "__main__":
    sys.exit(apply_and_restart(Path(sys.argv[1]), Path(sys.argv[2])))
