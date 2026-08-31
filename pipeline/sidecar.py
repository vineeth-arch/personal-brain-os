"""Shared sidecar-file handling for image captures (Pass 13). A sidecar
`<stem>.meta.json` carries the owner's typed thought and any on-device OCR
text alongside an image capture in the inbox. Whenever the image moves
(archive, quarantine, retry-to-inbox), its sidecar must move with it — this
is the one place that knows the naming convention, so archive.py, errors.py
and the retry route never each reinvent it."""
from __future__ import annotations

import shutil
from pathlib import Path


def sidecar_path(image_path: Path) -> Path:
    return image_path.parent / f"{image_path.stem}.meta.json"


def move_with_sidecar(src: Path, dest: Path) -> None:
    """After src's PRIMARY file has already been moved/renamed to dest, move
    its sidecar (if any) to sit beside dest — honoring whatever collision
    rename dest itself went through, so the pair never drifts apart. Best
    effort: a sidecar-move failure must never fail the caller (the primary
    file move already succeeded and is what matters)."""
    src_sidecar = sidecar_path(src)
    if not src_sidecar.exists():
        return
    try:
        dest_sidecar = sidecar_path(dest)
        if dest_sidecar.exists():
            dest_sidecar = dest.parent / f"{dest.stem}-{src_sidecar.stat().st_mtime_ns}.meta.json"
        shutil.move(str(src_sidecar), str(dest_sidecar))
    except OSError:
        pass
