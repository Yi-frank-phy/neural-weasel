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

Replace-Literal -Path $CandidateListSource -Old @'
#include "tsf/candidate_ui_diagnostics.h"
#include <KeyEvent.h>
'@ -New @'
#include "tsf/candidate_ui_diagnostics.h"
#include "tsf/candidate_style_diagnostics.h"
#include <KeyEvent.h>
'@

# Log both the response-owned style (_style) and the style currently copied
# into the native UI. This distinguishes "new config never reached TSF" from
# "TSF received it but the native UI retained stale paint inputs".
Replace-Literal -Path $CandidateListSource -Old @'
    neural_weasel::tsf::WriteCandidateUiDiagnostic(
        "update-ui-visibility-change", _beginUiHr, _pbShow, _uiStarted,
        _uiCreateAttempted, _uiCreateSuccess, uiShown,
        _ui->NativeWindowForDiagnostics());
    _uiTraceHasShownState = true;
'@ -New @'
    neural_weasel::tsf::WriteCandidateUiDiagnostic(
        "update-ui-visibility-change", _beginUiHr, _pbShow, _uiStarted,
        _uiCreateAttempted, _uiCreateSuccess, uiShown,
        _ui->NativeWindowForDiagnostics());
    neural_weasel::tsf::WriteCandidateStyleDiagnostic(
        "update-ui-source-style", _style.text_color, _style.back_color,
        _style.candidate_text_color, _style.candidate_back_color,
        _style.border_color, _style.hilited_candidate_text_color,
        _style.hilited_candidate_back_color);
    const auto& nativeStyle = _ui->style();
    neural_weasel::tsf::WriteCandidateStyleDiagnostic(
        "update-ui-native-style", nativeStyle.text_color, nativeStyle.back_color,
        nativeStyle.candidate_text_color, nativeStyle.candidate_back_color,
        nativeStyle.border_color, nativeStyle.hilited_candidate_text_color,
        nativeStyle.hilited_candidate_back_color);
    _uiTraceHasShownState = true;
'@

Replace-Literal -Path $CandidateListSource -Old @'
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "start-ui", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown(), _ui->NativeWindowForDiagnostics());
'@ -New @'
  neural_weasel::tsf::WriteCandidateUiDiagnostic(
      "start-ui", _beginUiHr, _pbShow, _uiStarted, _uiCreateAttempted,
      _uiCreateSuccess, _ui->IsShown(), _ui->NativeWindowForDiagnostics());
  neural_weasel::tsf::WriteCandidateStyleDiagnostic(
      "start-ui-source-style", _style.text_color, _style.back_color,
      _style.candidate_text_color, _style.candidate_back_color,
      _style.border_color, _style.hilited_candidate_text_color,
      _style.hilited_candidate_back_color);
  const auto& nativeStyle = _ui->style();
  neural_weasel::tsf::WriteCandidateStyleDiagnostic(
      "start-ui-native-style", nativeStyle.text_color, nativeStyle.back_color,
      nativeStyle.candidate_text_color, nativeStyle.candidate_back_color,
      nativeStyle.border_color, nativeStyle.hilited_candidate_text_color,
      nativeStyle.hilited_candidate_back_color);
'@

Write-Host 'Applied text-free runtime candidate style-alpha diagnostics.'
