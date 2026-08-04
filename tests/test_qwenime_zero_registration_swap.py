from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from neural_weasel.qwenime_compat.manifest import ExpectedBinary
from neural_weasel.qwenime_compat.swap_plan import (
    QwenImeSwapPlanError,
    build_server_swap_plan,
)


def _expected_binary(path: Path, relative_path: str) -> ExpectedBinary:
    payload = path.read_bytes()
    return ExpectedBinary(
        relative_path=relative_path,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _vendor_tree(tmp_path: Path) -> tuple[Path, tuple[ExpectedBinary, ...]]:
    install_root = tmp_path / "QwenIME"
    install_root.mkdir()
    payloads = {
        "qianwenime.dll": b"official-tsf-front-end",
        "QianwenIMEServer.exe": b"official-server",
        "QianwenIMEUiClient.exe": b"official-ui-client",
        "qime.dll": b"official-engine",
    }
    for relative_path, payload in payloads.items():
        (install_root / relative_path).write_bytes(payload)
    expected = tuple(
        _expected_binary(install_root / relative_path, relative_path)
        for relative_path in payloads
    )
    return install_root, expected


def test_swap_plan_changes_only_the_server_and_never_registers_tsf(tmp_path: Path) -> None:
    install_root, expected = _vendor_tree(tmp_path)
    replacement = tmp_path / "NeuralWeaselQwenServer.exe"
    replacement.write_bytes(b"replacement-server")

    plan = build_server_swap_plan(
        install_root,
        replacement,
        expected=expected,
    )

    assert plan.tsf_registration_required is False
    assert plan.registry_changes == ()
    assert plan.target_server == install_root / "QianwenIMEServer.exe"
    assert plan.replacement_server == replacement
    assert plan.preserved_vendor_paths == (
        install_root / "qianwenime.dll",
        install_root / "QianwenIMEUiClient.exe",
        install_root / "qime.dll",
    )
    assert [operation.kind for operation in plan.file_operations] == ["backup", "activate"]
    assert plan.file_operations[0].source == plan.target_server
    assert plan.file_operations[1].destination == plan.target_server


def test_swap_plan_is_non_mutating(tmp_path: Path) -> None:
    install_root, expected = _vendor_tree(tmp_path)
    replacement = tmp_path / "NeuralWeaselQwenServer.exe"
    replacement.write_bytes(b"replacement-server")
    original_server = (install_root / "QianwenIMEServer.exe").read_bytes()

    plan = build_server_swap_plan(install_root, replacement, expected=expected)

    assert (install_root / "QianwenIMEServer.exe").read_bytes() == original_server
    assert not plan.backup_server.exists()


def test_swap_plan_fails_closed_for_an_unverified_install(tmp_path: Path) -> None:
    install_root, expected = _vendor_tree(tmp_path)
    replacement = tmp_path / "NeuralWeaselQwenServer.exe"
    replacement.write_bytes(b"replacement-server")
    (install_root / "qianwenime.dll").write_bytes(b"modified-front-end")

    with pytest.raises(QwenImeSwapPlanError, match="unsupported QwenIME installation"):
        build_server_swap_plan(install_root, replacement, expected=expected)


def test_swap_plan_rejects_replacing_the_front_end(tmp_path: Path) -> None:
    install_root, expected = _vendor_tree(tmp_path)

    with pytest.raises(QwenImeSwapPlanError, match="replacement must be external"):
        build_server_swap_plan(
            install_root,
            install_root / "qianwenime.dll",
            expected=expected,
        )
