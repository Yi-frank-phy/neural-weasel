from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SUPPORTED_QWENIME_VERSION = "0.5.0.7"


@dataclass(frozen=True, slots=True)
class ExpectedBinary:
    relative_path: str
    size: int
    sha256: str


EXPECTED_BINARIES: tuple[ExpectedBinary, ...] = (
    ExpectedBinary(
        "qianwenime.dll",
        720_944,
        "8126422cd48b1a3b4ebdb431f39755aff219c2f25bb73445c0aba014c9d1e33b",
    ),
    ExpectedBinary(
        "QianwenIMEServer.exe",
        3_946_032,
        "16557c8d7015b610371bb3e4d7e93dc491bbfc755180d71154fcfcb1cfe3a4fb",
    ),
    ExpectedBinary(
        "QianwenIMEUiClient.exe",
        5_279_280,
        "eeae9c7d70117b7d03cc50eeb34a1f0fd3205629ec04ff739aff53eea8722a27",
    ),
    ExpectedBinary(
        "qime.dll",
        5_358_128,
        "44350c646f2673bef4a48f5a151adc844652737830a37fa938536f6c2238a8c1",
    ),
)


@dataclass(frozen=True, slots=True)
class VerificationMismatch:
    relative_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    version: str
    root: Path
    mismatches: tuple[VerificationMismatch, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_vendor_install(
    root: str | Path,
    *,
    expected: Iterable[ExpectedBinary] = EXPECTED_BINARIES,
) -> VerificationReport:
    """Verify a local official QwenIME installation without executing it."""

    install_root = Path(root)
    mismatches: list[VerificationMismatch] = []
    for item in expected:
        path = install_root / item.relative_path
        if not path.is_file():
            mismatches.append(VerificationMismatch(item.relative_path, "missing"))
            continue
        if path.stat().st_size != item.size:
            mismatches.append(VerificationMismatch(item.relative_path, "size_mismatch"))
            continue
        if _sha256(path) != item.sha256:
            mismatches.append(VerificationMismatch(item.relative_path, "sha256_mismatch"))
    return VerificationReport(
        version=SUPPORTED_QWENIME_VERSION,
        root=install_root,
        mismatches=tuple(mismatches),
    )
