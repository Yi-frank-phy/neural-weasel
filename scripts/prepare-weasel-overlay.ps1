[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot,
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedWeaselRevision = '9cc96e20dc71b80876b12f689bb5863c76c2a7ed'
$ExperimentalClsidBlock = @'
// {8AA66261-ED5F-46B0-895D-339B42C3AE1B}
static const GUID c_clsidTextService = {
    0x8aa66261,
    0xed5f,
    0x46b0,
    {0x89, 0x5d, 0x33, 0x9b, 0x42, 0xc3, 0xae, 0x1b}};
'@
$ExperimentalProfileBlock = @'
// {C9B3984E-A16C-4779-80E8-ACD988C57B0D}
static const GUID c_guidProfile = {
    0xc9b3984e,
    0xa16c,
    0x4779,
    {0x80, 0xe8, 0xac, 0xd9, 0x88, 0xc5, 0x7b, 0x0d}};
'@

# Upstream resource files are UTF-16 while sources are UTF-8; remember the
# encoding of each read so Write-SourceFile can restore it byte-for-byte.
$Global:LastSourceEncoding = 'utf-8'

function Read-SourceFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required upstream file is missing: $Path"
    }
    $Bytes = [IO.File]::ReadAllBytes($Path)
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) {
        $Global:LastSourceEncoding = 'unicode'
        return [Text.Encoding]::Unicode.GetString($Bytes, 2, $Bytes.Length - 2)
    }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFE -and $Bytes[1] -eq 0xFF) {
        $Global:LastSourceEncoding = 'bigendianunicode'
        return [Text.Encoding]::BigEndianUnicode.GetString($Bytes, 2, $Bytes.Length - 2)
    }
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        $Global:LastSourceEncoding = 'utf-8'
        return [Text.UTF8Encoding]::new($false, $true).GetString($Bytes, 3, $Bytes.Length - 3)
    }
    $Global:LastSourceEncoding = 'utf-8'
    return [Text.UTF8Encoding]::new($false, $true).GetString($Bytes)
}

function Write-SourceFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    switch ($Global:LastSourceEncoding) {
        'unicode' { [IO.File]::WriteAllText($Path, $Content, [Text.Encoding]::Unicode) }
        'bigendianunicode' { [IO.File]::WriteAllText($Path, $Content, [Text.Encoding]::BigEndianUnicode) }
        default { [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false)) }
    }
}

function Replace-Literal {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Old,
        [Parameter(Mandatory)][string]$New
    )
    $Content = Read-SourceFile -Path $Path
    if (-not $Content.Contains($Old)) {
        throw "Pinned upstream seam changed in $Path; missing expected text: $Old"
    }
    Write-SourceFile -Path $Path -Content $Content.Replace($Old, $New)
}

function Replace-RegexOnce {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Pattern,
        [Parameter(Mandatory)][string]$Replacement
    )
    $Content = Read-SourceFile -Path $Path
    $Regex = [regex]::new(
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::Singleline
    )
    if ($Regex.Matches($Content).Count -ne 1) {
        throw "Pinned upstream seam changed in $Path; expected one regex match."
    }
    Write-SourceFile -Path $Path -Content $Regex.Replace($Content, $Replacement, 1)
}

$ResolvedWeaselRoot = (Resolve-Path -LiteralPath $WeaselRoot).Path
$ResolvedRepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$ActualRevision = (& git -C $ResolvedWeaselRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $ActualRevision -ne $ExpectedWeaselRevision) {
    throw "Refusing unpinned Weasel tree. Expected $ExpectedWeaselRevision, got $ActualRevision"
}

$RootXmake = Join-Path $ResolvedWeaselRoot 'xmake.lua'
Replace-Literal -Path $RootXmake `
    -Old 'add_includedirs("$(projectdir)/include")' `
    -New @'
add_includedirs("$(projectdir)/include")
add_includedirs("$(projectdir)/librime/src")
'@

$Globals = Join-Path $ResolvedWeaselRoot 'WeaselTSF/Globals.cpp'
Replace-RegexOnce -Path $Globals `
    -Pattern '// \{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A\}\s+static const GUID c_clsidTextService = \{.*?\};' `
    -Replacement $ExperimentalClsidBlock
Replace-RegexOnce -Path $Globals `
    -Pattern '// \{3D02CAB6-2B8E-4781-BA20-1C9267529467\}\s+static const GUID c_guidProfile = \{.*?\};' `
    -Replacement $ExperimentalProfileBlock

$IdentityHeaderDirectory = Join-Path $ResolvedWeaselRoot 'include/tsf'
New-Item -ItemType Directory -Path $IdentityHeaderDirectory -Force | Out-Null
Copy-Item -LiteralPath (
    Join-Path $ResolvedRepositoryRoot 'native/tsf/experimental_profile_ids.h'
) -Destination (Join-Path $IdentityHeaderDirectory 'experimental_profile_ids.h') -Force
Copy-Item -LiteralPath (
    Join-Path $ResolvedRepositoryRoot 'native/tsf/experimental_identity_exports.cpp'
) -Destination (
    Join-Path $ResolvedWeaselRoot 'WeaselTSF/NeuralWeaselIdentity.cpp'
) -Force

# Crash-containment boundary: the editor-hosted DLL gets only bounded TSF
# capture/classification, source identity, the binary frame codec, and the
# authenticated one-way sender. Model IPC, JSON, workers, and backend lifecycle
# stay out of the TSF process and live in NeuralWeaselServer.exe or later.
$TsfXmake = Join-Path $ResolvedWeaselRoot 'WeaselTSF/xmake.lua'
Replace-Literal -Path $TsfXmake -Old '  add_files("./*.cpp", "WeaselTSF.def")' -New @'
  add_files("./*.cpp", "WeaselTSF.def")
  local neural_root = os.getenv("NEURAL_WEASEL_ROOT")
  add_files(
    neural_root .. "/native/tsf/input_scope_policy.cc",
    neural_root .. "/native/tsf/surrounding_text_edit_session.cc",
    neural_root .. "/native/tsf/context_capture_client.cc",
    neural_root .. "/native/tsf/weasel_context_adapter.cc",
    neural_root .. "/native/context/context_ipc_protocol.cc",
    neural_root .. "/native/context/source_context_identity.cc"
  )
  add_includedirs(neural_root .. "/native")
  add_syslinks("advapi32", "bcrypt")
'@
Replace-Literal -Path $TsfXmake -Old 'set_filename(fname)' `
    -New 'set_basename("NeuralWeaselExperimentalTSF")'
Replace-Literal -Path $TsfXmake -Old (
    'os.cp(path.join(target:targetdir(), "weasel*.dll"), "$(projectdir)/output")'
) -New (
    'os.cp(target:targetfile(), "$(projectdir)/output/NeuralWeaselExperimentalTSF.dll")'
)
Replace-Literal -Path $TsfXmake -Old (
    'os.cp(path.join(target:targetdir(), "weasel*.pdb"), "$(projectdir)/output")'
) -New @'
local pdb = path.join(target:targetdir(), "NeuralWeaselExperimentalTSF.pdb")
    if os.isfile(pdb) then
      os.cp(pdb, "$(projectdir)/output/NeuralWeaselExperimentalTSF.pdb")
    end
'@
Replace-Literal -Path $TsfXmake `
    -Old '  add_shflags("/DEBUG /OPT:REF /OPT:ICF")' `
    -New '  add_shflags("/DEBUG /OPT:REF /OPT:ICF /LTCG")'

$WeaselUiSource = Join-Path $ResolvedWeaselRoot 'WeaselUI/WeaselUI.cpp'
$RobustCandidateWindowCreate = @'
bool UI::Create(HWND parent) {
  if (!pimpl_) {
    pimpl_ = new UIImpl(*this);
    if (!pimpl_)
      return false;
  } else if (pimpl_->panel.IsWindow()) {
    return true;
  }

  HWND created = pimpl_->panel.Create(
      parent, 0, 0, WS_POPUP,
      WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
      0U, 0);
  if (created == nullptr && parent != nullptr) {
    // Some modern editor hosts expose a transient TSF view HWND that cannot
    // own a popup. Keep the candidate panel process-local and retry unowned.
    created = pimpl_->panel.Create(
        nullptr, 0, 0, WS_POPUP,
        WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE |
            WS_EX_TRANSPARENT,
        0U, 0);
  }
  return created != nullptr;
}
'@
Replace-RegexOnce -Path $WeaselUiSource `
    -Pattern 'bool UI::Create\(HWND parent\) \{.*?\}\s+(?=void UI::Destroy)' `
    -Replacement $RobustCandidateWindowCreate

$CandidateUiSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/CandidateList.cpp'
Copy-Item -LiteralPath (
    Join-Path $ResolvedRepositoryRoot 'native/tsf/ui_lifecycle_trace.h'
) -Destination (
    Join-Path $ResolvedWeaselRoot 'WeaselTSF/UiLifecycleTrace.h'
) -Force
Replace-Literal -Path $CandidateUiSource `
    -Old '#include "CandidateList.h"' `
    -New @'
#include "CandidateList.h"
#include "UiLifecycleTrace.h"
'@
Replace-RegexOnce -Path $CandidateUiSource `
    -Pattern '(?ms)^STDMETHODIMP CCandidateList::Show\(BOOL showCandidateWindow\) \{.*?^\}' `
    -Replacement @'
STDMETHODIMP CCandidateList::Show(BOOL showCandidateWindow) {
  if (showCandidateWindow)
    _ui->Show();
  else
    _ui->Hide();
  neural_weasel::tsf::TraceUiLifecycle(
      L"event=candidate-window-visibility requested=%d actual=%d",
      showCandidateWindow ? 1 : 0, _ui->IsShown() ? 1 : 0);
  return S_OK;
}
'@
Replace-Literal -Path $CandidateUiSource `
    -Old 'void CCandidateList::UpdateUI(const Context& ctx, const Status& status) {' `
    -New @'
void CCandidateList::UpdateUI(const Context& ctx, const Status& status) {
  neural_weasel::tsf::TraceUiLifecycle(
      L"event=candidate-ui-update candidates=%llu composing=%d pbShow=%d",
      static_cast<unsigned long long>(ctx.cinfo.candies.size()),
      status.composing ? 1 : 0, _pbShow ? 1 : 0);
'@
$TracedStartUi = @'
void CCandidateList::StartUI() {
  neural_weasel::tsf::TraceUiLifecycle(L"event=candidate-ui-start begin");
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
  if (!pThreadMgr) {
    neural_weasel::tsf::TraceUiLifecycle(
        L"event=candidate-ui-start result=no-thread-manager");
    return;
  }

  com_ptr<ITfUIElementMgr> pUIElementMgr;
  auto hr = pThreadMgr->QueryInterface(&pUIElementMgr);
  if (FAILED(hr)) {
    neural_weasel::tsf::TraceUiLifecycle(
        L"event=candidate-ui-start result=query-failed hr=%ld", hr);
    return;
  }

  if (pUIElementMgr == NULL) {
    neural_weasel::tsf::TraceUiLifecycle(
        L"event=candidate-ui-start result=null-ui-manager");
    return;
  }

  if (!_ui->uiCallback())
    _ui->SetUICallBack([this](size_t* const sel, size_t* const hov,
                              bool* const next, bool* const scroll_next) {
      _tsf->HandleUICallback(sel, hov, next, scroll_next);
    });
  hr = pUIElementMgr->BeginUIElement(this, &_pbShow, &uiid);
  neural_weasel::tsf::TraceUiLifecycle(
      L"event=candidate-ui-start result=registered hr=%ld pbShow=%d uiid=%lu",
      hr, _pbShow ? 1 : 0, uiid);
  // pUIElementMgr->UpdateUIElement(uiid);
  if (_pbShow) {
    _ui->style() = _style;
    _MakeUIWindow();
  }
}

'@
Replace-RegexOnce -Path $CandidateUiSource `
    -Pattern '(?ms)^void CCandidateList::StartUI\(\) \{.*?^\}\r?\n\r?\n(?=void CCandidateList::EndUI)' `
    -Replacement $TracedStartUi
Replace-Literal -Path $CandidateUiSource `
    -Old 'void CCandidateList::EndUI() {' `
    -New @'
void CCandidateList::EndUI() {
  neural_weasel::tsf::TraceUiLifecycle(
      L"event=candidate-ui-end uiid-valid=%d",
      uiid == TF_INVALID_UIELEMENTID ? 0 : 1);
'@
Replace-RegexOnce -Path $CandidateUiSource `
    -Pattern '(?ms)^void CCandidateList::_MakeUIWindow\(\) \{.*?^\}' `
    -Replacement @'
void CCandidateList::_MakeUIWindow() {
  HWND p = _GetActiveWnd();
  const bool created = _ui->Create(p);
  neural_weasel::tsf::TraceUiLifecycle(
      L"event=candidate-window-create result=%d owner=%d", created ? 1 : 0,
      p ? 1 : 0);
}
'@

$WeaselTsfHeader = Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.h'
Replace-RegexOnce -Path $WeaselTsfHeader `
    -Pattern '(?m)^(\s*weasel::Client m_client;\r?\n)(\s*DWORD _activateFlags;)' `
    -Replacement @'
$1  ULONGLONG _nextReconnectTick = 0;
$2
'@

$WeaselTsfSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.cpp'
$BoundedReconnect = @'
bool WeaselTSF::_EnsureServerConnected() {
  if (m_client.Echo()) {
    _nextReconnectTick = 0;
    return true;
  }

  // The launcher owns server lifetime. Never launch or wait from an
  // application-hosted TSF DLL; return promptly and retry at a bounded rate.
  const ULONGLONG now = GetTickCount64();
  if (now < _nextReconnectTick) {
    return false;
  }
  _nextReconnectTick = now + 1000;
  _Reconnect();
  if (!m_client.Echo()) {
    return false;
  }
  _nextReconnectTick = 0;
  return true;
}
'@
# WeaselTSF.cpp bounded reconnect rewrite count is enforced once against the
# pinned upstream revision. This removes process launch, sleeping and detached
# callbacks from every editor process that loads the experimental TSF.
Replace-RegexOnce -Path $WeaselTsfSource `
    -Pattern '(?ms)^static unsigned int retry = 0;\r?\n\r?\nbool WeaselTSF::_EnsureServerConnected\(\) \{.*?^\}\s*\z' `
    -Replacement $BoundedReconnect

Replace-Literal -Path $WeaselTsfSource -Old '#include "WeaselTSF.h"' -New @'
#include "WeaselTSF.h"
#include "tsf/weasel_context_adapter.h"
'@
Replace-Literal -Path $WeaselTsfSource -Old @'
STDAPI WeaselTSF::Deactivate() {
  m_client.EndSession();
'@ -New @'
STDAPI WeaselTSF::Deactivate() {
  neural_weasel::tsf::ClearWeaselContext();
  m_client.EndSession();
'@
Replace-Literal -Path $WeaselTsfSource -Old @'
STDMETHODIMP WeaselTSF::OnSetThreadFocus() {
  std::wstring _ToggleImeOnOpenClose{};
'@ -New @'
STDMETHODIMP WeaselTSF::OnSetThreadFocus() {
  neural_weasel::tsf::BeginWeaselContextFocus();
  std::wstring _ToggleImeOnOpenClose{};
'@
Replace-Literal -Path $WeaselTsfSource -Old @'
STDMETHODIMP WeaselTSF::OnKillThreadFocus() {
  _AbortComposition();
'@ -New @'
STDMETHODIMP WeaselTSF::OnKillThreadFocus() {
  neural_weasel::tsf::ClearWeaselContext();
  _AbortComposition();
'@

$ThreadMgrSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/ThreadMgrEventSink.cpp'
Replace-Literal -Path $ThreadMgrSource -Old '#include "WeaselTSF.h"' -New @'
#include "WeaselTSF.h"
#include "tsf/weasel_context_adapter.h"
'@
Replace-Literal -Path $ThreadMgrSource -Old @'
STDAPI WeaselTSF::OnSetFocus(ITfDocumentMgr* pDocMgrFocus,
                             ITfDocumentMgr* pDocMgrPrevFocus) {
  _InitTextEditSink(pDocMgrFocus);
'@ -New @'
STDAPI WeaselTSF::OnSetFocus(ITfDocumentMgr* pDocMgrFocus,
                             ITfDocumentMgr* pDocMgrPrevFocus) {
  if (pDocMgrFocus != pDocMgrPrevFocus) {
    neural_weasel::tsf::ClearWeaselContext();
    if (pDocMgrFocus != nullptr) {
      neural_weasel::tsf::BeginWeaselContextFocus();
    }
  }
  _InitTextEditSink(pDocMgrFocus);
'@

$TextEditSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/TextEditSink.cpp'
Replace-Literal -Path $TextEditSource -Old '#include "WeaselTSF.h"' -New @'
#include "WeaselTSF.h"
#include "tsf/weasel_context_adapter.h"
'@
Replace-Literal -Path $TextEditSource -Old @'
    pEnumTextChanges->Release();
  }
  return S_OK;
}
'@ -New @'
    pEnumTextChanges->Release();
  }
  neural_weasel::tsf::CaptureWeaselContext(pContext, _tfClientId);
  return S_OK;
}
'@

$LanguageBarSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/LanguageBar.cpp'
Replace-RegexOnce -Path $LanguageBarSource `
    -Pattern 'std::wstring WeaselTSF::_GetRootDir\(\) \{.*?return dir;\s*}' `
    -Replacement @'
std::wstring WeaselTSF::_GetRootDir() {
  WCHAR module_path[32768] = {};
  const DWORD length =
      GetModuleFileNameW(g_hInst, module_path, _countof(module_path));
  if (length == 0 || length >= _countof(module_path)) {
    return {};
  }
  return fs::path(module_path).parent_path().wstring();
}
'@

$ServerXmake = Join-Path $ResolvedWeaselRoot 'WeaselServer/xmake.lua'
Replace-Literal -Path $ServerXmake -Old '  set_kind("binary")' -New @'
  set_kind("binary")
  set_filename("__NEURAL_WEASEL_SERVER_EXE__")
'@
Replace-Literal -Path $ServerXmake -Old '  add_files("./*.cpp")' -New @'
  add_files("./*.cpp")
  local neural_root = os.getenv("NEURAL_WEASEL_ROOT")
  add_files(
    neural_root .. "/native/context/context_capture_broker.cc",
    neural_root .. "/native/context/context_ipc_protocol.cc",
    neural_root .. "/native/context/context_update_bridge.cc",
    neural_root .. "/native/pipe/named_pipe_client.cc"
  )
  add_includedirs(neural_root .. "/native")
  add_syslinks("advapi32")
'@

$ServerSource = Join-Path $ResolvedWeaselRoot 'WeaselServer/WeaselServer.cpp'
Replace-Literal -Path $ServerSource -Old '#include "WeaselService.h"' -New @'
#include "WeaselService.h"
#include "context/context_capture_broker.h"
'@
Replace-Literal -Path $ServerSource -Old @'
  try {
    WeaselServerApp app;
    RegisterApplicationRestart(NULL, 0);
    nRet = app.Run();
  } catch (...) {
'@ -New @'
  try {
    WeaselServerApp app;
    neural_weasel::context::ContextCaptureBroker context_broker;
    context_broker.Start();
    RegisterApplicationRestart(NULL, 0);
    nRet = app.Run();
    context_broker.Stop();
  } catch (...) {
'@

$RimeXmake = Join-Path $ResolvedWeaselRoot 'RimeWithWeasel/xmake.lua'
$RimeOverlay = @'
target("RimeWithWeasel")
  set_kind("static")
  add_files("./*.cpp")
  add_rules("use_weaselconstants")
  local neural_root = os.getenv("NEURAL_WEASEL_ROOT")
  local vcpkg_root = os.getenv("VCPKG_ROOT")
  add_files(
    neural_root .. "/native/pipe/named_pipe_client.cc",
    neural_root .. "/native/rime/ai_translator.cc",
    neural_root .. "/native/rime/ai_translator_module.cc",
    neural_root .. "/native/rime/bilingual_key_processor.cc",
    neural_root .. "/native/rime/bilingual_key_semantics.cc",
    neural_root .. "/native/rime/editor_context_epoch.cc"
  )
  add_includedirs(
    neural_root .. "/native",
    neural_root .. "/native/generated",
    "$(projectdir)/librime/src",
    "$(projectdir)/librime/include",
    vcpkg_root .. "/installed/x64-windows/include"
  )
  add_defines("RIME_IMPORTS")
'@
Write-SourceFile -Path $RimeXmake -Content $RimeOverlay

$RimeWithWeasel = Join-Path $ResolvedWeaselRoot 'RimeWithWeasel/RimeWithWeasel.cpp'
Replace-RegexOnce -Path $RimeWithWeasel `
    -Pattern 'void RimeWithWeaselHandler::_Setup\(\) \{\s+RIME_STRUCT\(RimeTraits, weasel_traits\);' `
    -Replacement @'
void rime_require_module_ai_translator();
void rime_register_module_ai_translator_explicit();

void RimeWithWeaselHandler::_Setup() {
  rime_require_module_ai_translator();
  rime_register_module_ai_translator_explicit();
  RIME_STRUCT(RimeTraits, weasel_traits);
  RIME_MODULE_LIST(neural_weasel_modules, "default", "ai_translator");
  weasel_traits.modules = neural_weasel_modules;
'@
Replace-RegexOnce -Path $RimeWithWeasel `
    -Pattern 'LOG\(INFO\) << "Initializing la rime\.";\s+rime_api->initialize\(NULL\);' `
    -Replacement @'
LOG(INFO) << "Initializing la rime.";
  RIME_MODULE_LIST(neural_weasel_init_modules, "default", "ai_translator");
  std::string init_shared_dir = wtou8(WeaselSharedDataPath().wstring());
  std::string init_user_dir = wtou8(WeaselUserDataPath().wstring());
  std::string init_log_dir = WeaselLogPath().u8string();
  RIME_STRUCT(RimeTraits, init_traits);
  init_traits.shared_data_dir = init_shared_dir.c_str();
  init_traits.user_data_dir = init_user_dir.c_str();
  init_traits.prebuilt_data_dir = init_shared_dir.c_str();
  init_traits.app_name = "rime.neural_weasel_experimental";
  init_traits.log_dir = init_log_dir.c_str();
  init_traits.modules = neural_weasel_init_modules;
  rime_api->initialize(&init_traits);
'@

$TextExtensions = @('.cpp', '.h', '.rc', '.lua', '.def')
$SourceDirectories = @(
    'include',
    'RimeWithWeasel',
    'WeaselIPC',
    'WeaselIPCServer',
    'WeaselServer',
    'WeaselTSF',
    'WeaselUI'
)
$Replacements = @(
    [pscustomobject]@{ Old = 'WeaselNamedPipe'; New = 'NeuralWeaselExperimentalIPC' }
    [pscustomobject]@{ Old = 'WeaselIPCWindow_1.0'; New = 'NeuralWeaselExperimentalIPCWindow_1.0' }
    [pscustomobject]@{ Old = '(WEASEL)Furandōru-Sukāretto-'; New = '(NEURAL-WEASEL-EXPERIMENTAL)-' }
    [pscustomobject]@{ Old = 'WeaselDeployerExclusiveMutex'; New = 'NeuralWeaselExperimentalDeployerMutex' }
    [pscustomobject]@{ Old = 'WeaselServer.exe'; New = '__NEURAL_WEASEL_SERVER_EXE__' }
    [pscustomobject]@{ Old = 'WeaselServer.pdb'; New = '__NEURAL_WEASEL_SERVER_PDB__' }
    [pscustomobject]@{ Old = 'WeaselDeployer.exe'; New = 'NeuralWeaselProfileTool.exe' }
    [pscustomobject]@{ Old = 'WeaselTSF Button'; New = 'Neural Weasel Safe TSF' }
    [pscustomobject]@{ Old = 'start_service.bat'; New = 'NeuralWeaselServer.exe' }
    [pscustomobject]@{ Old = 'Software\\Rime\\Weasel'; New = 'Software\\NeuralWeasel\\Experimental' }
    [pscustomobject]@{ Old = 'Software\\Rime\\weasel'; New = 'Software\\NeuralWeasel\\Experimental' }
    [pscustomobject]@{ Old = '%AppData%\\Rime'; New = '%LOCALAPPDATA%\\NeuralWeasel\\Experimental\\RimeUser' }
    [pscustomobject]@{ Old = '%TEMP%\\rime.weasel'; New = '%LOCALAPPDATA%\\NeuralWeasel\\Experimental\\Logs' }
    [pscustomobject]@{ Old = 'rime.weasel'; New = 'rime.neural_weasel_experimental' }
)
foreach ($Directory in $SourceDirectories) {
    Get-ChildItem -LiteralPath (Join-Path $ResolvedWeaselRoot $Directory) `
        -File -Recurse |
        Where-Object { $TextExtensions -contains $_.Extension } |
        ForEach-Object {
            $Content = Read-SourceFile -Path $_.FullName
            $Changed = $false
            foreach ($Entry in $Replacements) {
                if ($Content.Contains($Entry.Old)) {
                    $Content = $Content.Replace($Entry.Old, $Entry.New)
                    $Changed = $true
                }
            }
            $Content = $Content.Replace(
                '__NEURAL_WEASEL_SERVER_EXE__',
                'NeuralWeaselServer.exe'
            )
            $Content = $Content.Replace(
                '__NEURAL_WEASEL_SERVER_PDB__',
                'NeuralWeaselServer.pdb'
            )
            if ($Changed) {
                Write-SourceFile -Path $_.FullName -Content $Content
            }
        }
}

$Constants = Join-Path $ResolvedWeaselRoot 'include/WeaselConstants.h'
Replace-Literal -Path $Constants -Old '#define WEASEL_CODE_NAME "Weasel"' `
    -New '#define WEASEL_CODE_NAME "NeuralWeaselExperimental"'
Replace-Literal -Path $Constants -Old '#define RIME_REG_KEY L"Software\\Rime"' `
    -New '#define RIME_REG_KEY L"Software\\NeuralWeasel\\Experimental"'

$UtilityHeader = Join-Path $ResolvedWeaselRoot 'include/WeaselUtility.h'
$UtilityContent = Read-SourceFile -Path $UtilityHeader
$UtilityContent = $UtilityContent.Replace('L"小狼毫"', 'L"神经小狼毫（安全版）"')
$UtilityContent = $UtilityContent.Replace('L"Weasel"', 'L"Neural Weasel Safe"')
Write-SourceFile -Path $UtilityHeader -Content $UtilityContent

foreach ($ResourcePath in @(
    (Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.rc'),
    (Join-Path $ResolvedWeaselRoot 'WeaselServer/WeaselServer.rc')
)) {
    $Resource = Read-SourceFile -Path $ResourcePath
    if (-not $Resource.StartsWith('#pragma code_page(65001)')) {
        $Resource = "#pragma code_page(65001)`r`n$Resource"
    }
    $Resource = $Resource.Replace('weaselx64.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('weaselARM64.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('weaselARM.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('weasel.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('"WeaselServer"', '"NeuralWeaselServer"')
    $Resource = $Resource.Replace('"Weasel TSF"', '"Neural Weasel Safe TSF"')
    $Resource = $Resource.Replace('"Weasel"', '"Neural Weasel Safe"')
    $Resource = $Resource.Replace('"小狼毫TSF"', '"神经小狼毫（安全版）TSF"')
    $Resource = $Resource.Replace('"小狼毫"', '"神经小狼毫（安全版）"')
    Write-SourceFile -Path $ResourcePath -Content $Resource
}

Write-Host "Prepared crash-contained Neural Weasel overlay on Weasel $ActualRevision"
