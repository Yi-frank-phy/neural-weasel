from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import (
    EXPECTED_BINARIES,
    ExpectedBinary,
    verify_vendor_install,
)

SERVER_RELATIVE_PATH = "QianwenIMEServer.exe"
PRESERVED_VENDOR_RELATIVE_PATHS = (
    "qianwenime.dll",
    "QianwenIMEUiClient.exe",
    "qime.dll",
)


class QwenImeSwapPlanError(RuntimeError):
    """Raised when a safe server-only swap plan cannot be constructed."""


@dataclass(frozen=True, slots=True)
class FileOperation:
    kind: str
    source: Path
    destination: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "source": str(self.source),
            "destination": str(self.destination),
        }


@dataclass(frozen=True, slots=True)
class ServerSwapPlan:
    version: str
    install_root: Path
    target_server: Path
    replacement_server: Path
    backup_server: Path
    preserved_vendor_paths: tuple[Path, ...]
    file_operations: tuple[FileOperation, ...]
    registry_changes: tuple[str, ...] = ()
    tsf_registration_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "install_root": str(self.install_root),
            "target_server": str(self.target_server),
            "replacement_server": str(self.replacement_server),
            "backup_server": str(self.backup_server),
            "preserved_vendor_paths": [str(path) for path in self.preserved_vendor_paths],
            "file_operations": [operation.to_dict() for operation in self.file_operations],
            "registry_changes": list(self.registry_changes),
            "tsf_registration_required": self.tsf_registration_required,
            "mutates_files": False,
        }


def _server_expectation(expected: tuple[ExpectedBinary, ...]) -> ExpectedBinary:
    for item in expected:
        if item.relative_path == SERVER_RELATIVE_PATH:
            return item
    raise QwenImeSwapPlanError("server expectation is missing")


def build_server_swap_plan(
    install_root: str | Path,
    replacement_server: str | Path,
    *,
    expected: Iterable[ExpectedBinary] = EXPECTED_BINARIES,
) -> ServerSwapPlan:
    """Build a version-pinned, non-mutating QwenIME server swap plan.

    The official TSF front end remains registered and untouched. This function performs
    only static verification and returns intended file operations; it never writes files,
    changes the registry, registers COM classes, or starts vendor code.
    """

    root = Path(install_root)
    replacement = Path(replacement_server)
    expected_tuple = tuple(expected)

    report = verify_vendor_install(root, expected=expected_tuple)
    if not report.ok:
        raise QwenImeSwapPlanError("unsupported QwenIME installation")
    if not replacement.is_file():
        raise QwenImeSwapPlanError("replacement server is missing")

    resolved_root = root.resolve()
    resolved_replacement = replacement.resolve()
    if resolved_replacement.is_relative_to(resolved_root):
        raise QwenImeSwapPlanError("replacement must be external to the QwenIME installation")

    server_expectation = _server_expectation(expected_tuple)
    target_server = root / SERVER_RELATIVE_PATH
    backup_server = (
        root
        / ".neural-weasel-backup"
        / f"{SERVER_RELATIVE_PATH}.{server_expectation.sha256[:16]}.bak"
    )
    preserved_vendor_paths = tuple(root / path for path in PRESERVED_VENDOR_RELATIVE_PATHS)
    operations = (
        FileOperation("backup", target_server, backup_server),
        FileOperation("activate", replacement, target_server),
    )
    return ServerSwapPlan(
        version=report.version,
        install_root=root,
        target_server=target_server,
        replacement_server=replacement,
        backup_server=backup_server,
        preserved_vendor_paths=preserved_vendor_paths,
        file_operations=operations,
    )
