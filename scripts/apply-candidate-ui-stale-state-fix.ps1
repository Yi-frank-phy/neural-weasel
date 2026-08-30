[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot
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

$ResolvedWeaselRoot = (Resolve-Path -LiteralPath $WeaselRoot).Path
$CandidateListSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/CandidateList.cpp'

# The partial upstream idempotence backport can otherwise leave a live
# UIElement session after the native HWND is destroyed. Destroy paths must
# either end that session or at minimum restore the local invariant before a
# later StartUI() can run again.
Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::Destroy() {
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "destroy", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown(), _ui->NativeWindowForDiagnostics());
  // EndUI();
  Show(FALSE);
  _DisposeUIWindow();
}

void CCandidateList::DestroyAll() {
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "destroy-all", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown(), _ui->NativeWindowForDiagnostics());
  // EndUI();
  Show(FALSE);
  _DisposeUIWindowAll();
}
'@ -New @'
void CCandidateList::Destroy() {
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "destroy", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown(), _ui->NativeWindowForDiagnostics());
  if (_uiStarted) {
    EndUI();
  } else {
    Show(FALSE);
    _DisposeUIWindow();
  }
}

void CCandidateList::DestroyAll() {
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "destroy-all", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown(), _ui->NativeWindowForDiagnostics());
  if (_uiStarted) {
    EndUI();
  } else {
    Show(FALSE);
  }
  _uiStarted = false;
  _DisposeUIWindowAll();
}
'@

# Cleanup is local-state critical. Failure to reacquire ITfUIElementMgr must
# not leave _uiStarted=true or preserve a dead HWND forever.
Replace-Literal -Path $CandidateListSource -Old @'
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
  if (pThreadMgr) {
    com_ptr<ITfUIElementMgr> emgr;
    auto hr = pThreadMgr->QueryInterface(&emgr);
    if (FAILED(hr))
      return;
    if (emgr != NULL)
      emgr->EndUIElement(uiid);
  }
  _uiStarted = false;
'@ -New @'
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
  if (pThreadMgr) {
    com_ptr<ITfUIElementMgr> emgr;
    auto hr = pThreadMgr->QueryInterface(&emgr);
    if (SUCCEEDED(hr) && emgr != NULL) {
      emgr->EndUIElement(uiid);
    } else {
      neural_weasel::tsf::WriteCandidateUiDiagnostic(
          "end-ui-element-manager-unavailable", hr, _pbShow, _uiStarted,
          _uiCreateAttempted, _uiCreateSuccess, _ui->IsShown(),
          _ui->NativeWindowForDiagnostics());
    }
  }
  _uiStarted = false;
'@

Write-Host 'Applied stale candidate UI-session invariant fix.'
