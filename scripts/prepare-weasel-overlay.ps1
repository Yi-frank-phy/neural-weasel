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

# Keep the pinned Weasel baseline while applying later upstream CandidateList
# fixes that are narrowly relevant to TSF UI update and lifecycle handling.
$CandidateListSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/CandidateList.cpp'
Replace-Literal -Path $CandidateListSource -Old @'
  _ui->Update(ctx, status);
  if (_pbShow == FALSE)
    _UpdateUIElement();
'@ -New @'
  _ui->Update(ctx, status);
  _UpdateUIElement();
'@

Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::StartUI() {
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@ -New @'
void CCandidateList::StartUI() {
  if (_uiStarted)
    return;

  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@

Replace-Literal -Path $CandidateListSource -Old @'
  pUIElementMgr->BeginUIElement(this, &_pbShow, &uiid);
  // pUIElementMgr->UpdateUIElement(uiid);
'@ -New @'
  if (FAILED(pUIElementMgr->BeginUIElement(this, &_pbShow, &uiid)))
    return;
  _uiStarted = true;
  // pUIElementMgr->UpdateUIElement(uiid);
'@

Replace-Literal -Path $CandidateListSource -Old @'
void CCandidateList::EndUI() {
  com_ptr<ITfThreadMgr> pThreadMgr = _tsf->_GetThreadMgr();
'@ -New @'
void CCandidateList::EndUI() {
  if (!_uiStarted)
    return;

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
}
'@

$CandidateListHeader = Join-Path $ResolvedWeaselRoot 'WeaselTSF/CandidateList.h'
Replace-Literal -Path $CandidateListHeader -Old @'
  BOOL _pbShow;
  weasel::UIStyle _style;
'@ -New @'
  BOOL _pbShow;
  bool _uiStarted = false;
  weasel::UIStyle _style;
'@

Write-Host 'Applied upstream CandidateList UI update/lifecycle fixes.'
