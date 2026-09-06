[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot,
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$BaseOverlay = Join-Path $PSScriptRoot 'prepare-weasel-overlay.ps1'
$PresentationOverlay = Join-Path $PSScriptRoot 'prepare-weasel-presentation-overlay.ps1'

. $BaseOverlay -WeaselRoot $WeaselRoot -RepositoryRoot $RepositoryRoot
. $PresentationOverlay
