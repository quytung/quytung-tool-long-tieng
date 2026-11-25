"""Build the ZIP and checksum used by GitHub Releases."""
import hashlib
import zipfile
from pathlib import Path

from updater import FILES, PACKAGE, ROOT


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    package = output / PACKAGE
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (output / (PACKAGE + ".sha256")).write_text(f"{digest}  {PACKAGE}\n", encoding="ascii")


if __name__ == "__main__":
    build(Path("dist"))
