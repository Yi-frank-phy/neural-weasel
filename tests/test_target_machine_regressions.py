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


def test_static_rime_components_are_explicitly_initialized_and_finalized() -> None:
    module = _read("native/rime/ai_translator_module.cc")
    overlay = _read("scripts/prepare-weasel-overlay.ps1")

    assert "rime_register_module_ai_translator_explicit" in module
    assert 'RimeFindModule("ai_translator")' in module
    assert "void rime_require_module_ai_translator()" in module
    assert "rime_register_module_ai_translator_explicit();" in module
    assert "void rime_initialize_module_ai_translator_explicit()" in module
    assert "void rime_finalize_module_ai_translator_explicit()" in module

    initialize_call = "rime_initialize_module_ai_translator_explicit();"
    rime_initialize = "rime_api->initialize(NULL);"
    finalize_call = "rime_finalize_module_ai_translator_explicit();"
    rime_finalize = "rime_api->finalize();"
    assert initialize_call in overlay
    assert finalize_call in overlay

    initialize_patch = overlay.index("-Old '  rime_api->initialize(NULL);'")
    initialize_call_pos = overlay.index(initialize_call, initialize_patch)
    rime_initialize_pos = overlay.index(rime_initialize, initialize_call_pos)
    assert initialize_call_pos < rime_initialize_pos

    finalize_patch = overlay.index("-Old '  rime_api->finalize();'")
    rime_finalize_pos = overlay.index(rime_finalize, finalize_patch)
    finalize_call_pos = overlay.index(finalize_call, rime_finalize_pos)
    assert rime_finalize_pos < finalize_call_pos


def test_profile_tool_uses_machine_wide_com_and_cleans_legacy_user_key() -> None:
    profile_tool = _read("native/profile_tool/profile_tool.cpp")

    assert "HKEY_LOCAL_MACHINE" in profile_tool
    assert "HKEY_CURRENT_USER" in profile_tool
    assert "RegisterComServer" in profile_tool
    assert "DeleteComRegistration" in profile_tool
    assert "ReadRegisteredDll(HKEY_LOCAL_MACHINE" in profile_tool
    assert "DeleteComRegistration(HKEY_CURRENT_USER" in profile_tool


def test_install_bundle_is_windows_powershell_51_encoding_safe() -> None:
    install = _read("scripts/install-dev-profile.ps1")

    assert "$LauncherCmdName" in install
    assert "[char]0x" in install
    assert "Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8" in install


def test_python_service_bundle_contains_declared_readme() -> None:
    bundle = _read("scripts/build-windows-bundle.ps1")
    install = _read("scripts/install-dev-profile.ps1")

    assert "Join-Path $PythonService 'README.md'" in bundle
    assert "python-service\\README.md" in install


def test_real_install_requires_admin_before_mutation_but_dry_run_does_not() -> None:
    install = _read("scripts/install-dev-profile.ps1")

    assert "function Assert-Administrator" in install
    dry_run = install.index("if ($DryRun)")
    admin_call = install.index("Assert-Administrator", dry_run)
    mutation = install.index("$ExistingManifestPath", dry_run)
    assert dry_run < admin_call < mutation


def test_profile_install_updates_user_input_method_tip_without_setting_default() -> None:
    profile_tool = _read("native/profile_tool/profile_tool.cpp")

    assert "InstallLayoutOrTip" in profile_tool
    assert "ILOT_UNINSTALL" in profile_tool
    assert "ILOT_DEFPROFILE" not in profile_tool
    assert "ILOT_CLEANINSTALL" not in profile_tool


def test_generated_weasel_resources_force_utf8_code_page() -> None:
    overlay = _read("scripts/prepare-weasel-overlay.ps1")

    assert "#pragma code_page(65001)" in overlay


def test_cuda_launcher_exposes_torch_dll_directory_only_to_service_process() -> None:
    service = _read("scripts/start-model-service.ps1")

    assert "torch\\lib" in service
    assert "$env:PATH" in service
    assert "llama_install_check" in service
    assert service.index("torch\\lib") < service.index("llama_install_check")


def test_experimental_launcher_rejects_stale_or_wrong_service_state() -> None:
    service = _read("scripts/start-model-service.ps1")
    launcher = _read("scripts/launch-neural-weasel.ps1")

    assert "process_start_utc" in service
    for marker in (
        "service_profile",
        "transport",
        "model",
        "format",
        "quantization",
        "runtime",
        "compute_backend",
        "safety_profile",
        "process_start_utc",
    ):
        assert marker in launcher
    assert "StartTime" in launcher


def test_ci_executes_install_dry_run_under_windows_powershell_51() -> None:
    driver = _read("scripts/test-install-safety.ps1")

    assert "powershell.exe" in driver
    assert "install-dev-profile.ps1" in driver
    assert "-DryRun" in driver

