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

function Read-SourceFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required upstream file is missing: $Path"
    }
    return [IO.File]::ReadAllText($Path)
}

function Write-SourceFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
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

$TsfXmake = Join-Path $ResolvedWeaselRoot 'WeaselTSF/xmake.lua'
Replace-Literal -Path $TsfXmake -Old '  set_kind("shared")' -New @'
  set_kind("shared")
  local neural_root = os.getenv("NEURAL_WEASEL_ROOT")
  add_files(
    neural_root .. "/native/pipe/named_pipe_client.cc",
    neural_root .. "/native/context/context_update_bridge.cc",
    neural_root .. "/native/rime/editor_context_epoch.cc",
    neural_root .. "/native/tsf/surrounding_text_edit_session.cc",
    neural_root .. "/native/tsf/weasel_context_adapter.cc"
  )
  add_includedirs(neural_root .. "/native")
  add_defines("WINVER=0x0A00", "_WIN32_WINNT=0x0A00",
              "WIN32_LEAN_AND_MEAN", "NOMINMAX")
'@
Replace-Literal -Path $TsfXmake -Old 'set_filename(fname)' `
    -New 'set_filename("NeuralWeaselExperimentalTSF.dll")'
Replace-Literal -Path $TsfXmake -Old (
    'os.cp(path.join(target:targetdir(), "weasel*.dll"), "$(projectdir)/output")'
) -New (
    'os.cp(path.join(target:targetdir(), "NeuralWeaselExperimentalTSF.dll"), "$(projectdir)/output")'
)
Replace-Literal -Path $TsfXmake -Old (
    'os.cp(path.join(target:targetdir(), "weasel*.pdb"), "$(projectdir)/output")'
) -New (
    'os.cp(path.join(target:targetdir(), "NeuralWeaselExperimentalTSF.pdb"), "$(projectdir)/output")'
)

$TextEditSink = Join-Path $ResolvedWeaselRoot 'WeaselTSF/TextEditSink.cpp'
Replace-Literal -Path $TextEditSink -Old '#include "WeaselTSF.h"' -New @'
#include "WeaselTSF.h"
#include "tsf/weasel_context_adapter.h"
'@
Replace-RegexOnce -Path $TextEditSink `
    -Pattern '(\s*)return S_OK;\s*}\s*STDAPI WeaselTSF::OnLayoutChange' `
    -Replacement @'
  neural_weasel::tsf::CaptureWeaselContext(pContext, _tfClientId);
  return S_OK;
}

STDAPI WeaselTSF::OnLayoutChange
'@

$WeaselTsfSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.cpp'
Replace-Literal -Path $WeaselTsfSource -Old '#include "ResponseParser.h"' -New @'
#include "ResponseParser.h"
#include "tsf/weasel_context_adapter.h"
'@
Replace-RegexOnce -Path $WeaselTsfSource `
    -Pattern 'STDMETHODIMP WeaselTSF::OnKillThreadFocus\(\) \{\s+_AbortComposition\(\);' `
    -Replacement @'
STDMETHODIMP WeaselTSF::OnKillThreadFocus() {
  neural_weasel::tsf::ClearWeaselContext();
  _AbortComposition();
'@
Replace-RegexOnce -Path $WeaselTsfSource `
    -Pattern 'STDAPI WeaselTSF::Deactivate\(\) \{\s+m_client\.EndSession\(\);' `
    -Replacement @'
STDAPI WeaselTSF::Deactivate() {
  neural_weasel::tsf::StopWeaselContext();
  m_client.EndSession();
'@
Replace-RegexOnce -Path $WeaselTsfSource `
    -Pattern 'STDAPI WeaselTSF::ActivateEx\(ITfThreadMgr\* pThreadMgr,\s+TfClientId tfClientId,\s+DWORD dwFlags\) \{\s+com_ptr<ITfDocumentMgr> pDocMgrFocus;' `
    -Replacement @'
STDAPI WeaselTSF::ActivateEx(ITfThreadMgr* pThreadMgr,
                             TfClientId tfClientId,
                             DWORD dwFlags) {
  neural_weasel::tsf::StartWeaselContext();
  com_ptr<ITfDocumentMgr> pDocMgrFocus;
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

void RimeWithWeaselHandler::_Setup() {
  rime_require_module_ai_translator();
  RIME_STRUCT(RimeTraits, weasel_traits);
  RIME_MODULE_LIST(neural_weasel_modules, "default", "ai_translator");
  weasel_traits.modules = neural_weasel_modules;
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
    [pscustomobject]@{ Old = 'WeaselTSF Button'; New = 'Neural Weasel Experimental TSF' }
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
$UtilityContent = $UtilityContent.Replace('L"小狼毫"', 'L"神经小狼毫（实验）"')
$UtilityContent = $UtilityContent.Replace('L"Weasel"', 'L"Neural Weasel Experimental"')
Write-SourceFile -Path $UtilityHeader -Content $UtilityContent

foreach ($ResourcePath in @(
    (Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.rc'),
    (Join-Path $ResolvedWeaselRoot 'WeaselServer/WeaselServer.rc')
)) {
    $Resource = Read-SourceFile -Path $ResourcePath
    $Resource = $Resource.Replace('weaselx64.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('weaselARM64.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('weaselARM.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('weasel.dll', 'NeuralWeaselExperimentalTSF.dll')
    $Resource = $Resource.Replace('"WeaselServer"', '"NeuralWeaselServer"')
    $Resource = $Resource.Replace('"Weasel TSF"', '"Neural Weasel Experimental TSF"')
    $Resource = $Resource.Replace('"Weasel"', '"Neural Weasel Experimental"')
    $Resource = $Resource.Replace('"小狼毫TSF"', '"神经小狼毫（实验）TSF"')
    $Resource = $Resource.Replace('"小狼毫"', '"神经小狼毫（实验）"')
    Write-SourceFile -Path $ResourcePath -Content $Resource
}

Write-Host "Prepared isolated Neural Weasel overlay on Weasel $ActualRevision"
