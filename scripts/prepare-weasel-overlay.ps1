[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot,
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
    $UsesCrLf = $Content.Contains("`r`n")
    $NormalizedContent = $Content.Replace("`r`n", "`n")
    $NormalizedOld = $Old.Replace("`r`n", "`n")
    $NormalizedNew = $New.Replace("`r`n", "`n")
    if (-not $NormalizedContent.Contains($NormalizedOld)) {
        throw "Pinned upstream seam changed in $Path; missing expected text: $Old"
    }
    $Updated = $NormalizedContent.Replace($NormalizedOld, $NormalizedNew)
    if ($UsesCrLf) {
        $Updated = $Updated.Replace("`n", "`r`n")
    }
    Write-SourceFile -Path $Path -Content $Updated
}

$PinnedOverlay = Join-Path $PSScriptRoot 'prepare-weasel-overlay-pinned.ps1'
& $PinnedOverlay -WeaselRoot $WeaselRoot -RepositoryRoot $RepositoryRoot

$ResolvedWeaselRoot = (Resolve-Path -LiteralPath $WeaselRoot).Path

# Backport narrow upstream candidate-UI lifecycle fixes without moving the
# entire Weasel baseline. ITfUIElement::IsShown must report the actual HWND
# state; an internal "Show was requested" boolean is not visibility evidence.
$WeaselUiSource = Join-Path $ResolvedWeaselRoot 'WeaselUI/WeaselUI.cpp'
Replace-Literal -Path $WeaselUiSource `
    -Old '  bool IsShown() const { return shown; }' `
    -New '  bool IsShown() const { return panel.IsWindowVisible() != FALSE; }'

# Preserve normal behavior while making UI::Create() return truthful HWND
# creation status to the diagnostic caller. The pinned upstream function
# previously returned true even when panel.Create() failed.
Replace-Literal -Path $WeaselUiSource -Old @'
  pimpl_->panel.Create(
      parent, 0, 0, WS_POPUP,
      WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
      0U, 0);
  return true;
'@ -New @'
  const HWND created = pimpl_->panel.Create(
      parent, 0, 0, WS_POPUP,
      WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
      0U, 0);
  return created != NULL && pimpl_->panel.IsWindow();
'@

$CandidateListSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/CandidateList.cpp'
Replace-Literal -Path $CandidateListSource -Old @'
#include "CandidateList.h"
#include <KeyEvent.h>
'@ -New @'
#include "CandidateList.h"
#include "tsf/candidate_ui_diagnostics.h"
#include <KeyEvent.h>
'@

Replace-Literal -Path $CandidateListSource -Old @'
  _ui->Update(ctx, status);
  if (_pbShow == FALSE)
    _UpdateUIElement();

  if (status.composing)
    Show(_pbShow);
  else
    Show(FALSE);
}
'@ -New @'
  _ui->Update(ctx, status);
  _UpdateUIElement();

  if (status.composing)
    Show(_pbShow);
  else
    Show(FALSE);

  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "update-ui", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown());
}
'@

Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::Destroy() {
  // EndUI();
  Show(FALSE);
  _DisposeUIWindow();
}

void CCandidateList::DestroyAll() {
  // EndUI();
  Show(FALSE);
  _DisposeUIWindowAll();
}
'@ -New @'
void CCandidateList::Destroy() {
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "destroy", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown());
  // EndUI();
  Show(FALSE);
  _DisposeUIWindow();
}

void CCandidateList::DestroyAll() {
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "destroy-all", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown());
  // EndUI();
  Show(FALSE);
  _DisposeUIWindowAll();
}
'@

Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::StartUI() {
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@ -New @'
void CCandidateList::StartUI() {
  if (_uiStarted) {
    neural_weasel::tsf::WriteCandidateUiDiagnostic(
        "start-suppressed-already-started", _beginUiHr, _pbShow, _uiStarted,
        _uiCreateAttempted, _uiCreateSuccess, _ui->IsShown());
    return;
  }

  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@

Replace-Literal -Path $CandidateListSource -Old @'
  pUIElementMgr->BeginUIElement(this, &_pbShow, &uiid);
  // pUIElementMgr->UpdateUIElement(uiid);
  if (_pbShow) {
    _ui->style() = _style;
    _MakeUIWindow();
  }
'@ -New @'
  _beginUiHr = pUIElementMgr->BeginUIElement(this, &_pbShow, &uiid);
  if (FAILED(_beginUiHr)) {
    neural_weasel::tsf::WriteCandidateUiDiagnostic(
        "begin-ui-failed", _beginUiHr, _pbShow, _uiStarted,
        _uiCreateAttempted, _uiCreateSuccess, _ui->IsShown());
    return;
  }
  _uiStarted = true;
  _uiCreateAttempted = false;
  _uiCreateSuccess = false;
  // pUIElementMgr->UpdateUIElement(uiid);
  if (_pbShow) {
    _ui->style() = _style;
    _uiCreateAttempted = true;
    _uiCreateSuccess = _MakeUIWindow();
  }
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "start-ui", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown());
'@

Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::EndUI() {
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@ -New @'
void CCandidateList::EndUI() {
  if (!_uiStarted) {
    neural_weasel::tsf::WriteCandidateUiDiagnostic(
        "end-suppressed-not-started", _beginUiHr, _pbShow, _uiStarted,
        _uiCreateAttempted, _uiCreateSuccess, _ui->IsShown());
    return;
  }

  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@

Replace-Literal -Path $CandidateListSource -Old @'
    if (emgr != NULL)
      emgr->EndUIElement(uiid);
  }
  _DisposeUIWindow();
}
'@ -New @'
    if (emgr != NULL)
      emgr->EndUIElement(uiid);
  }
  _uiStarted = false;
  _DisposeUIWindow();
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "end-ui", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown());
}
'@

Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::_MakeUIWindow() {
  HWND p = _GetActiveWnd();
  _ui->Create(p);
}
'@ -New @'
bool CCandidateList::_MakeUIWindow() {
  HWND p = _GetActiveWnd();
  return _ui->Create(p);
}
'@

$CandidateListHeader = Join-Path $ResolvedWeaselRoot 'WeaselTSF/CandidateList.h'
Replace-Literal -Path $CandidateListHeader -Old @'
  void _DisposeUIWindowAll();
  void _MakeUIWindow();
'@ -New @'
  void _DisposeUIWindowAll();
  bool _MakeUIWindow();
'@
Replace-Literal -Path $CandidateListHeader -Old @'
  BOOL _pbShow;
  weasel::UIStyle _style;
'@ -New @'
  BOOL _pbShow;
  HRESULT _beginUiHr = E_PENDING;
  bool _uiStarted = false;
  bool _uiCreateAttempted = false;
  bool _uiCreateSuccess = false;
  weasel::UIStyle _style;
'@

Write-Host 'Applied candidate UI lifecycle backports and text-free state trace.'
