from __future__ import annotations

from neural_weasel.context import EditorContext, is_secure_capture


def test_secure_context_never_keeps_text() -> None:
    context = EditorContext.secure_context("password-manager.exe")

    assert context.before == ""
    assert context.after == ""
    assert context.secure
    assert "before" not in context.metadata()
    assert "after" not in context.metadata()


def test_utf16_clipping_does_not_split_non_bmp_character() -> None:
    context = EditorContext(
        before="a" * 8191 + "😀",
        after="😀" + "b" * 4096,
        app_id="editor.exe",
        partial=False,
        complete_region=True,
        secure=False,
    )

    clipped = context.clipped_fast()

    assert clipped.before == "a" * 8190 + "😀"
    assert len(clipped.before.encode("utf-16-le")) // 2 == 8192
    assert clipped.after.startswith("😀")
    assert len(clipped.after.encode("utf-16-le")) // 2 == 4096


def test_capture_policy_is_fail_closed_on_each_security_signal() -> None:
    base = {
        "input_scopes": set(),
        "app_id": "editor.exe",
        "blacklisted_apps": set(),
        "secure_desktop": False,
        "protected_field": False,
    }
    assert not is_secure_capture(**base)
    assert is_secure_capture(**(base | {"input_scopes": {"IS_PASSWORD"}}))
    assert is_secure_capture(**(base | {"app_id": "VAULT.EXE", "blacklisted_apps": {"vault.exe"}}))
    assert is_secure_capture(**(base | {"secure_desktop": True}))
    assert is_secure_capture(**(base | {"protected_field": True}))

