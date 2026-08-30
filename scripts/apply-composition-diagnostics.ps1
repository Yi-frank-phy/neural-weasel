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
$StaleUiFix = Join-Path $PSScriptRoot 'apply-candidate-ui-stale-state-fix.ps1'
& $StaleUiFix -WeaselRoot $ResolvedWeaselRoot
$StyleDiagnostics = Join-Path $PSScriptRoot 'apply-candidate-style-diagnostics.ps1'
& $StyleDiagnostics -WeaselRoot $ResolvedWeaselRoot

$CompositionSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/Composition.cpp'

Replace-Literal -Path $CompositionSource -Old @'
#include "CandidateList.h"
'@ -New @'
#include "CandidateList.h"
#include "tsf/candidate_ui_diagnostics.h"
'@

Replace-Literal -Path $CompositionSource -Old @'
  _cand->StartUI();
'@ -New @'
  neural_weasel::tsf::WriteCompositionDiagnostic(
      "start-request", _pComposition != nullptr, _status.composing,
      _pComposition.p, nullptr);
  _cand->StartUI();
'@

Replace-Literal -Path $CompositionSource -Old @'
STDAPI WeaselTSF::OnCompositionTerminated(TfEditCookie ecWrite,
                                          ITfComposition* pComposition) {
'@ -New @'
STDAPI WeaselTSF::OnCompositionTerminated(TfEditCookie ecWrite,
                                          ITfComposition* pComposition) {
  neural_weasel::tsf::WriteCompositionDiagnostic(
      "terminated", _pComposition != nullptr, _status.composing,
      _pComposition.p, pComposition);
'@

Replace-Literal -Path $CompositionSource -Old @'
void WeaselTSF::_AbortComposition(bool clear) {
  m_client.ClearComposition();
'@ -New @'
void WeaselTSF::_AbortComposition(bool clear) {
  neural_weasel::tsf::WriteCompositionDiagnostic(
      "abort", _pComposition != nullptr, _status.composing,
      _pComposition.p, nullptr);
  m_client.ClearComposition();
'@

Replace-Literal -Path $CompositionSource -Old @'
void WeaselTSF::_FinalizeComposition() {
  _pComposition = nullptr;
}

void WeaselTSF::_SetComposition(com_ptr<ITfComposition> pComposition) {
  _pComposition = pComposition;
}
'@ -New @'
void WeaselTSF::_FinalizeComposition() {
  neural_weasel::tsf::WriteCompositionDiagnostic(
      "finalize", _pComposition != nullptr, _status.composing,
      _pComposition.p, nullptr);
  _pComposition = nullptr;
}

void WeaselTSF::_SetComposition(com_ptr<ITfComposition> pComposition) {
  _pComposition = pComposition;
  neural_weasel::tsf::WriteCompositionDiagnostic(
      "set", _pComposition != nullptr, _status.composing,
      _pComposition.p, nullptr);
}
'@

Write-Host 'Applied stale UI-state repair plus text-free style and composition diagnostics.'
