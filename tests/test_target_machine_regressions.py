from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_wisdom_and_experimental_model_services_use_disjoint_state() -> None:
    service = _read("scripts/start-model-service.ps1")
    wisdom = _read("scripts/start-wisdom-service.vbs")

    assert "ServiceProfile" in service
    assert "WisdomIntegration" in service
    assert "-ServiceProfile wisdom" in wisdom
    assert "model-service.json" in service


def test_static_rime_module_has_explicit_registration_before_setup() -> None:
    module = _read("native/rime/ai_translator_module.cc")
    overlay = _read("scripts/prepare-weasel-overlay.ps1")

    assert "rime_register_module_ai_translator_explicit" in module
    assert "RimeFindModule(\"ai_translator\")" in module
    assert "rime_register_module_ai_translator_explicit();" in overlay

    setup_marker = "void RimeWithWeaselHandler::_Setup() {"
    call_marker = "rime_register_module_ai_translator_explicit();"
    traits_marker = "RIME_STRUCT(RimeTraits, weasel_traits);"
    setup = overlay.index(setup_marker)
    call = overlay.index(call_marker, setup)
    traits = overlay.index(traits_marker, setup)
    assert setup < call < traits


def test_profile_tool_uses_machine_wide_com_and_cleans_legacy_user_key() -> None:
    profile_tool = _read("native/profile_tool/profile_tool.cpp")

    assert "HKEY_LOCAL_MACHINE" in profile_tool
    assert "HKEY_CURRENT_USER" in profile_tool
    assert "RegisterComServer" in profile_tool
    assert "DeleteComRegistration" in profile_tool
    assert "ReadRegisteredDll(HKEY_LOCAL_MACHINE" in profile_tool
    assert "DeleteComRegistration(HKEY_CURRENT_USER" in profile_tool
