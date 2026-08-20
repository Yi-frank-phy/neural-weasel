[CmdletBinding()]
param(
    [string]$InstallRoot = (
        Join-Path $env:LOCALAPPDATA (
            'NeuralWeasel\Experimental\experimental-profile'
        )
    ),
    [switch]$RemoveModelCache,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$ExperimentalInputMethodTip = "0804:$ExperimentalClsid$ExperimentalProfileGuid"
$MachineComRoot = (
    "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$ExperimentalClsid"
)
$MachineComInproc = Join-Path $MachineComRoot 'InprocServer32'
$ExperimentalRuntimeRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental')
)
$ExpectedInstallRoot = Join-Path $ExperimentalRuntimeRoot 'experimental-profile'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($InstallRoot -ne $ExpectedInstallRoot) {
    throw 'Refusing to remove a directory outside the reserved experimental-profile boundary.'
}

function Assert-Elevated {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    if (-not $Principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw 'Uninstalling the experimental TSF profile requires an elevated PowerShell session.'
    }
}

function Get-MachineComState {
    param([Parameter(Mandatory)][string]$ExpectedDll)
    if (-not (Test-Path -LiteralPath $MachineComRoot)) {
        return [pscustomobject]@{ Exists = $false; Exact = $false }
    }
    if (-not (Test-Path -LiteralPath $MachineComInproc)) {
        return [pscustomobject]@{ Exists = $true; Exact = $false }
    }
    $Key = Get-Item -LiteralPath $MachineComInproc
    $RegisteredDll = [string]$Key.GetValue('')
    $ThreadingModel = [string]$Key.GetValue('ThreadingModel')
    $Exact = (
        $RegisteredDll -and
        [IO.Path]::GetFullPath($RegisteredDll).Equals(
            [IO.Path]::GetFullPath($ExpectedDll),
            [StringComparison]::OrdinalIgnoreCase
        ) -and
        $ThreadingModel.Equals(
            'Apartment',
            [StringComparison]::OrdinalIgnoreCase
        )
    )
    return [pscustomobject]@{ Exists = $true; Exact = $Exact }
}

function Remove-MachineComRegistration {
    param([Parameter(Mandatory)][string]$ExpectedDll)
    $State = Get-MachineComState -ExpectedDll $ExpectedDll
    if ($State.Exists -and -not $State.Exact) {
        throw 'Refusing to remove a conflicting machine COM identity.'
    }
    if ($State.Exact) {
        Remove-Item -LiteralPath $MachineComRoot -Recurse -Force
    }
}

function Remove-ExperimentalInputMethodTip {
    $LanguageList = Get-WinUserLanguageList
    $ZhHansMatches = @(
        $LanguageList | Where-Object { $_.LanguageTag -eq 'zh-Hans-CN' }
    )
    if ($ZhHansMatches.Count -ne 1) {
        throw "Expected exactly one zh-Hans-CN user language, found $($ZhHansMatches.Count)."
    }
    $ZhHans = $ZhHansMatches[0]
    if (@($ZhHans.InputMethodTips) -notcontains $ExperimentalInputMethodTip) {
        return
    }
    $ZhHans.InputMethodTips.Remove($ExperimentalInputMethodTip)
    Set-WinUserLanguageList -LanguageList $LanguageList -Force
}

$InstalledTool = Join-Path $InstallRoot 'NeuralWeaselProfileTool.exe'
$InstalledDll = Join-Path $InstallRoot 'NeuralWeaselExperimentalTSF.dll'
if ($DryRun) {
    $MachineComState = Get-MachineComState -ExpectedDll $InstalledDll
    if ($MachineComState.Exists -and -not $MachineComState.Exact) {
        throw 'The reserved experimental machine COM identity is in conflict.'
    }
    if (Test-Path -LiteralPath $InstalledTool -PathType Leaf) {
        & $InstalledTool unregister `
            --clsid $ExperimentalClsid `
            --profile-guid $ExperimentalProfileGuid `
            --dll $InstalledDll `
            --dry-run
        if ($LASTEXITCODE -ne 0) {
            throw "Experimental unregister dry-run failed with exit code $LASTEXITCODE."
        }
    }
    Write-Host 'Dry-run succeeded; no process, profile, registry key, or file was changed.'
    exit 0
}

Assert-Elevated

Get-Process -Name 'NeuralWeaselServer' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction Stop

if (Test-Path -LiteralPath $InstalledTool -PathType Leaf) {
    & $InstalledTool unregister `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dll $InstalledDll
    if ($LASTEXITCODE -ne 0) {
        throw "Experimental profile unregistration failed with exit code $LASTEXITCODE."
    }
} elseif (
    (Test-Path -LiteralPath $InstallRoot -PathType Container) -or
    (Test-Path -LiteralPath $MachineComRoot)
) {
    throw 'Refusing partial uninstall: the identity-locked profile tool is missing.'
}

Remove-MachineComRegistration -ExpectedDll $InstalledDll
Remove-ExperimentalInputMethodTip

if (Test-Path -LiteralPath $ExperimentalRuntimeRoot -PathType Container) {
    Remove-Item -LiteralPath $ExperimentalRuntimeRoot -Recurse -Force
}

if ($RemoveModelCache) {
    $ModelCache = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\huggingface'
    if (Test-Path -LiteralPath $ModelCache -PathType Container) {
        Remove-Item -LiteralPath $ModelCache -Recurse -Force
    }
}

Write-Host 'Removed only the 神经小狼毫（实验） profile and isolated runtime data.'
if (-not $RemoveModelCache) {
    Write-Host 'Model weights were preserved. Use -RemoveModelCache to remove them explicitly.'
}
