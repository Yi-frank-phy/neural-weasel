from __future__ import annotations

from pathlib import Path


_VENDOR_SUFFIXES = {".dll", ".exe", ".pdb", ".msi"}
_IGNORED_DIRECTORY_NAMES = {".git", ".venv", "build", "dist", "external"}


def test_repository_never_embeds_qwenime_vendor_binaries() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _VENDOR_SUFFIXES
        and not _IGNORED_DIRECTORY_NAMES.intersection(path.parts)
    ]

    assert forbidden == []
