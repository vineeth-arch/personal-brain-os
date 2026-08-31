"""Stage 6 — archive. Move the processed source file out of the inbox to
archive_path. Never delete (CLAUDE.md source-of-truth safety)."""
from __future__ import annotations

import shutil
from pathlib import Path


def archive(src: Path, archive_path: Path) -> Path:
    archive_path = Path(archive_path)
    archive_path.mkdir(parents=True, exist_ok=True)
    dest = archive_path / src.name
    if dest.exists():
        dest = archive_path / f"{src.stem}-{src.stat().st_mtime_ns}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest
