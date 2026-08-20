[CmdletBinding()]
param(
    [string]$BuildDirectory = $PSScriptRoot,
    [string]$InstallRoot = (
        Join-Path $env:LOCALAPPDATA (
            'NeuralWeasel\Experimental\experimental-profile'
        )
    ),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$ExperimentalInputMethodTip = "0804:$ExperimentalClsid$ExperimentalProfileGuid"
$ExpectedWeaselRevision = '9cc96e20dc71b80876b12f689bb5863c76c2a7ed'
$ProfileToolName = 'NeuralWeaselProfileTool.exe'
$ActivatorName = 'NeuralWeaselSessionActivator.exe'
$TsfDllName = 'NeuralWeaselExperimentalTSF.dll'
$ServerName = 'NeuralWeaselServer.exe'
$MachineComRoot = (
    "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\$ExperimentalClsid"
)
$MachineComInproc = Join-Path $MachineComRoot 'InprocServer32'

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Assert-Elevated {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    if (-not $Principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) {
        throw 'Installing the experimental TSF profile requires an elevated PowerShell session.'
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

function Set-MachineComRegistration {
    param([Parameter(Mandatory)][string]$ExpectedDll)
    $State = Get-MachineComState -ExpectedDll $ExpectedDll
    if ($State.Exists -and -not $State.Exact) {
        throw 'The reserved experimental machine COM identity is in conflict.'
    }
    if ($State.Exact) {
        return $false
    }
    New-Item -Path $MachineComInproc -Force | Out-Null
    Set-Item -LiteralPath $MachineComInproc -Value $ExpectedDll
    New-ItemProperty -LiteralPath $MachineComInproc `
        -Name 'ThreadingModel' `
        -Value 'Apartment' `
        -PropertyType String `
        -Force | Out-Null
    return $true
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

function Get-ZhHansUserLanguage {
    param([Parameter(Mandatory)][object]$LanguageList)
    $Matches = @(
        $LanguageList | Where-Object { $_.LanguageTag -eq 'zh-Hans-CN' }
    )
    if ($Matches.Count -ne 1) {
        throw "Expected exactly one zh-Hans-CN user language, found $($Matches.Count)."
    }
    return $Matches[0]
}

function Test-ExperimentalInputMethodTip {
    $LanguageList = Get-WinUserLanguageList
    $ZhHans = Get-ZhHansUserLanguage -LanguageList $LanguageList
    return @($ZhHans.InputMethodTips) -contains $ExperimentalInputMethodTip
}

function Add-ExperimentalInputMethodTip {
    $LanguageList = Get-WinUserLanguageList
    $ZhHans = Get-ZhHansUserLanguage -LanguageList $LanguageList
    if (@($ZhHans.InputMethodTips) -contains $ExperimentalInputMethodTip) {
        return $false
    }
    $ZhHans.InputMethodTips.Add($ExperimentalInputMethodTip)
    Set-WinUserLanguageList -LanguageList $LanguageList -Force
    return $true
}

function Remove-ExperimentalInputMethodTip {
    $LanguageList = Get-WinUserLanguageList
    $ZhHans = Get-ZhHansUserLanguage -LanguageList $LanguageList
    if (@($ZhHans.InputMethodTips) -notcontains $ExperimentalInputMethodTip) {
        return
    }
    $ZhHans.InputMethodTips.Remove($ExperimentalInputMethodTip)
    Set-WinUserLanguageList -LanguageList $LanguageList -Force
}

function Get-ProfileStatus {
    param(
        [Parameter(Mandatory)][string]$Tool,
        [Parameter(Mandatory)][string]$ExpectedDll
    )
    $StatusJson = & $Tool status `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --json
    Assert-LastExitCode -Operation 'Experimental profile status check'
    $ProfileStatus = $StatusJson | ConvertFrom-Json
    if ($ProfileStatus.identity_conflict) {
        throw 'The reserved experimental profile identity is in conflict.'
    }
    if ($ProfileStatus.registered) {
        if (-not $ProfileStatus.com_path) {
            throw 'The registered experimental profile has no COM path.'
        }
        $ActualDll = [IO.Path]::GetFullPath([string]$ProfileStatus.com_path)
        if (-not $ActualDll.Equals(
            [IO.Path]::GetFullPath($ExpectedDll),
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw 'The registered experimental COM path does not match this installation.'
        }
    }
    return $ProfileStatus
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The experimental profile supports only 64-bit Windows.'
}
if (-not $env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is required.'
}

$BuildDirectory = (Resolve-Path -LiteralPath $BuildDirectory).Path
$ExpectedInstallRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA (
        'NeuralWeasel\Experimental\experimental-profile'
    ))
)
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($InstallRoot -ne $ExpectedInstallRoot) {
    throw 'Refusing install directory outside the reserved experimental boundary.'
}

$ManifestPath = Join-Path $BuildDirectory 'build-manifest.json'
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    throw "Missing build manifest: $ManifestPath"
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if (
    $Manifest.experimental_clsid -ne $ExperimentalClsid -or
    $Manifest.experimental_profile_guid -ne $ExperimentalProfileGuid -or
    $Manifest.upstream_weasel_revision -ne $ExpectedWeaselRevision -or
    $Manifest.architecture -ne 'x64'
) {
    throw 'Build manifest does not describe the reserved experimental bundle.'
}

$LocalizedLauncherName = -join @(
    [char]0x542F,
    [char]0x52A8,
    [char]0x795E,
    [char]0x7ECF,
    [char]0x5C0F,
    [char]0x72FC,
    [char]0x6BEB
) + '.cmd'

$Required = @(
    $TsfDllName,
    $ProfileToolName,
    $ActivatorName,
    $ServerName,
    'NeuralWeaselRimeModule.lib',
    'build-manifest.json',
    'install-dev-profile.ps1',
    'uninstall-dev-profile.ps1',
    'diagnose.ps1',
    'start-model-service.ps1',
    'launch-neural-weasel.ps1',
    'Start-Neural-Weasel.cmd',
    $LocalizedLauncherName,
    'tools\uv.exe',
    'README-INSTALL-TEST.md',
    'data\neural_weasel.schema.yaml',
    'rime-user\default.custom.yaml',
    'python-service\pyproject.toml',
    'python-service\uv.lock'
)
foreach ($RelativePath in $Required) {
    $Path = Join-Path $BuildDirectory $RelativePath
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing required bundle artifact: $RelativePath"
    }
    if ($RelativePath -ne 'build-manifest.json') {
        $ManifestName = $RelativePath.Replace('\', '/')
        if ($ManifestName -notin $Manifest.artifacts.PSObject.Properties.Name) {
            throw "Required bundle artifact is not hashed: $RelativePath"
        }
    }
}

foreach ($Property in $Manifest.artifacts.PSObject.Properties) {
    $RelativePath = $Property.Name.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if (
        [IO.Path]::IsPathRooted($RelativePath) -or
        '..' -in $RelativePath.Split(
            [IO.Path]::DirectorySeparatorChar,
            [StringSplitOptions]::RemoveEmptyEntries
        )
    ) {
        throw "Manifest artifact escapes the bundle: $($Property.Name)"
    }
    $Path = [IO.Path]::GetFullPath((Join-Path $BuildDirectory $RelativePath))
    if (-not $Path.StartsWith(
        $BuildDirectory + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Manifest artifact escapes the bundle: $($Property.Name)"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Manifest artifact is missing: $($Property.Name)"
    }
    $ActualHash = (
        Get-FileHash -LiteralPath $Path -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($ActualHash -ne [string]$Property.Value) {
        throw "Artifact hash mismatch: $($Property.Name)"
    }
}

$SourceTool = Join-Path $BuildDirectory $ProfileToolName
$SourceDll = Join-Path $BuildDirectory $TsfDllName
& $SourceTool verify `
    --clsid $ExperimentalClsid `
    --profile-guid $ExperimentalProfileGuid `
    --dll $SourceDll
Assert-LastExitCode -Operation 'Experimental identity verification'

if ($DryRun) {
    Write-Host 'Dry-run succeeded; no files, registry keys, profiles, or defaults were changed.'
    exit 0
}

$ExistingManifestPath = Join-Path $InstallRoot 'build-manifest.json'
if (Test-Path -LiteralPath $ExistingManifestPath -PathType Leaf) {
    $ExistingMatches = (
        (Get-FileHash -LiteralPath $ExistingManifestPath -Algorithm SHA256).Hash -eq
        (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash
    )
    if ($ExistingMatches) {
        foreach ($Property in $Manifest.artifacts.PSObject.Properties) {
            $RelativePath = $Property.Name.Replace(
                '/',
                [IO.Path]::DirectorySeparatorChar
            )
            $ExistingPath = Join-Path $InstallRoot $RelativePath
            if (-not (Test-Path -LiteralPath $ExistingPath -PathType Leaf)) {
                $ExistingMatches = $false
                break
            }
            $ExistingHash = (
                Get-FileHash -LiteralPath $ExistingPath -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            if ($ExistingHash -ne [string]$Property.Value) {
                $ExistingMatches = $false
                break
            }
        }
    }
    if ($ExistingMatches) {
        $InstalledTool = Join-Path $InstallRoot $ProfileToolName
        $InstalledDll = Join-Path $InstallRoot $TsfDllName
        $ProfileStatus = Get-ProfileStatus `
            -Tool $InstalledTool `
            -ExpectedDll $InstalledDll
        $MachineComState = Get-MachineComState -ExpectedDll $InstalledDll
        if ($ProfileStatus.registered -and $MachineComState.Exact) {
            if (Test-ExperimentalInputMethodTip) {
                Write-Host 'The identical bundle is already installed and registered; registration was not repeated.'
            } else {
                Add-ExperimentalInputMethodTip | Out-Null
                Write-Host 'The identical bundle was already installed; its missing input-method list entry was repaired.'
            }
            exit 0
        }
        Assert-Elevated
        $MachineComCreated = $false
        $ProfileRegistrationRepaired = $false
        $InputMethodTipAdded = $false
        try {
            $MachineComCreated = Set-MachineComRegistration `
                -ExpectedDll $InstalledDll
            if (-not $ProfileStatus.registered) {
                & $InstalledTool register `
                    --clsid $ExperimentalClsid `
                    --profile-guid $ExperimentalProfileGuid `
                    --dll $InstalledDll
                Assert-LastExitCode -Operation 'Missing experimental profile registration repair'
                $ProfileRegistrationRepaired = $true
                Write-Host 'The identical bundle was already installed; its missing registration was repaired.'
            } elseif ($MachineComCreated) {
                Write-Host 'The identical bundle was already installed; its missing machine COM registration was repaired.'
            }
            $InputMethodTipAdded = Add-ExperimentalInputMethodTip
        } catch {
            if ($InputMethodTipAdded) {
                Remove-ExperimentalInputMethodTip
            }
            if ($ProfileRegistrationRepaired) {
                & $InstalledTool unregister `
                    --clsid $ExperimentalClsid `
                    --profile-guid $ExperimentalProfileGuid `
                    --dll $InstalledDll | Out-Null
            }
            if ($MachineComCreated) {
                Remove-MachineComRegistration -ExpectedDll $InstalledDll
            }
            throw
        }
        exit 0
    }
}

Assert-Elevated

$InstallParent = Split-Path -Parent $InstallRoot
New-Item -ItemType Directory -Path $InstallParent -Force | Out-Null
$StagingRoot = "$InstallRoot.staging-$([guid]::NewGuid().ToString('N'))"
$BackupRoot = "$InstallRoot.backup-$([guid]::NewGuid().ToString('N'))"
$HadPreviousInstall = Test-Path -LiteralPath $InstallRoot -PathType Container
$InstallSwapped = $false
$MachineComCreated = $false
$InputMethodTipAdded = $false

try {
    New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $BuildDirectory -Force |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $StagingRoot `
                -Recurse -Force
        }

    $StagedTool = Join-Path $StagingRoot $ProfileToolName
    $StagedDll = Join-Path $StagingRoot $TsfDllName
    & $StagedTool verify `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dll $StagedDll
    Assert-LastExitCode -Operation 'Staged experimental identity verification'

    Get-Process -Name 'NeuralWeaselServer' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction Stop

    if ($HadPreviousInstall) {
        Move-Item -LiteralPath $InstallRoot -Destination $BackupRoot
    }
    Move-Item -LiteralPath $StagingRoot -Destination $InstallRoot
    $InstallSwapped = $true

    $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental'
    $RimeUserRoot = Join-Path $RuntimeRoot 'RimeUser'
    New-Item -ItemType Directory -Path $RimeUserRoot -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $InstallRoot 'rime-user') -File |
        ForEach-Object {
            $Destination = Join-Path $RimeUserRoot $_.Name
            if (-not (Test-Path -LiteralPath $Destination)) {
                Copy-Item -LiteralPath $_.FullName -Destination $Destination
            }
        }

    $InstalledTool = Join-Path $InstallRoot $ProfileToolName
    $InstalledDll = Join-Path $InstallRoot $TsfDllName
    $MachineComCreated = Set-MachineComRegistration -ExpectedDll $InstalledDll
    & $InstalledTool register `
        --clsid $ExperimentalClsid `
        --profile-guid $ExperimentalProfileGuid `
        --dll $InstalledDll
    Assert-LastExitCode -Operation 'Experimental profile registration'
    $InputMethodTipAdded = Add-ExperimentalInputMethodTip

    if (Test-Path -LiteralPath $BackupRoot) {
        try {
            Remove-Item -LiteralPath $BackupRoot -Recurse -Force
        } catch {
            Write-Warning "Profile is installed, but the previous isolated backup could not be removed: $BackupRoot"
        }
    }
} catch {
    $InstallFailure = $_
    if ($InputMethodTipAdded) {
        Remove-ExperimentalInputMethodTip
    }
    if (
        $InstallSwapped -and
        (Test-Path -LiteralPath $InstallRoot -PathType Container)
    ) {
        Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $BackupRoot -PathType Container) {
        Move-Item -LiteralPath $BackupRoot -Destination $InstallRoot
        $RollbackTool = Join-Path $InstallRoot $ProfileToolName
        $RollbackDll = Join-Path $InstallRoot $TsfDllName
        & $RollbackTool register `
            --clsid $ExperimentalClsid `
            --profile-guid $ExperimentalProfileGuid `
            --dll $RollbackDll
        if ($LASTEXITCODE -ne 0) {
            throw 'Install failed and previous profile registration could not be restored.'
        }
    } elseif ($MachineComCreated) {
        Remove-MachineComRegistration `
            -ExpectedDll (Join-Path $InstallRoot $TsfDllName)
    }
    if (Test-Path -LiteralPath $StagingRoot -PathType Container) {
        Remove-Item -LiteralPath $StagingRoot -Recurse -Force
    }
    throw $InstallFailure
}

Write-Host 'Installed 神经小狼毫（实验） without changing the default input method.'
Write-Host 'Run 启动神经小狼毫.cmd to start the model service and activate it for this desktop session.'
