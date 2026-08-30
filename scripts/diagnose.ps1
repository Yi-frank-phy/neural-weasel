[CmdletBinding()]
param(
    [string]$InstallRoot = (
        Join-Path $env:LOCALAPPDATA (
            'NeuralWeasel\Experimental\experimental-profile'
        )
    )
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$ProfileTool = Join-Path $InstallRoot 'NeuralWeaselProfileTool.exe'
$ServerPath = Join-Path $InstallRoot 'NeuralWeaselServer.exe'
$ManifestPath = Join-Path $InstallRoot 'build-manifest.json'
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
    $ManifestPath = Join-Path $PSScriptRoot 'build-manifest.json'
}
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental'
$StatePath = Join-Path $RuntimeRoot 'model-service.json'
$LogRoot = Join-Path $RuntimeRoot 'Logs'
$CandidateUiTracePath = Join-Path $LogRoot 'candidate-ui-events.log'
$RuntimeWeaselConfigPath = Join-Path $RuntimeRoot 'RimeUser\weasel.yaml'
$DeployedWeaselConfigPath = Join-Path $RuntimeRoot 'RimeUser\build\weasel.yaml'
$SharedWeaselConfigPath = Join-Path $InstallRoot 'data\weasel.yaml'

function Get-OptionalProperty {
    param(
        [AllowNull()][object]$InputObject,
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][object]$Default = $null
    )
    if ($null -eq $InputObject) {
        return $Default
    }
    $Property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $Property) {
        return $Default
    }
    return $Property.Value
}

$RequiredFiles = @(
    'NeuralWeaselExperimentalTSF.dll',
    'NeuralWeaselProfileTool.exe',
    'NeuralWeaselServer.exe',
    'NeuralWeaselRimeModule.lib',
    'data\neural_weasel.schema.yaml'
)
$Missing = @(
    foreach ($Relative in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot $Relative))) {
            $Relative
        }
    }
)

$Registration = $null
if (Test-Path -LiteralPath $ProfileTool -PathType Leaf) {
    try {
        $Registration = & $ProfileTool status `
            --clsid $ExperimentalClsid `
            --profile-guid $ExperimentalProfileGuid `
            --json | ConvertFrom-Json
    } catch {
        $Registration = [ordered]@{
            registered = $false
            error = $_.Exception.GetType().Name
        }
    }
}

$ModelState = $null
if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
    try {
        $ModelState = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    } catch {
        $ModelState = [ordered]@{ state = 'malformed-state-file' }
    }
}

$Manifest = $null
if (Test-Path -LiteralPath $ManifestPath -PathType Leaf) {
    try {
        $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    } catch {
        $Manifest = [ordered]@{ error = 'malformed-build-manifest' }
    }
}

$PipePresent = $false
$WeaselPipePresent = $false
try {
    $Pipes = @(Get-ChildItem -LiteralPath '\\.\pipe\' -ErrorAction Stop)
    $PipePresent = [bool](
        $Pipes |
            Where-Object { $_.Name -like '*NeuralWeasel-v1-*' } |
            Select-Object -First 1
    )
    $WeaselPipePresent = [bool](
        $Pipes |
            Where-Object {
                $_.Name -like '*NeuralWeaselExperimentalIPC'
            } |
            Select-Object -First 1
    )
} catch {
    $PipeProbeError = $_.Exception.GetType().Name
}

$CandidateUiTraceTail = @()
if (Test-Path -LiteralPath $CandidateUiTracePath -PathType Leaf) {
    try {
        $CandidateUiTraceTail = @(
            Get-Content -LiteralPath $CandidateUiTracePath -Tail 64
        )
    } catch {
        $CandidateUiTraceReadError = $_.Exception.GetType().Name
    }
}

$ComPathConflict = $false
$Registered = [bool](Get-OptionalProperty $Registration 'registered' $false)
$ComRegistered = [bool](
    Get-OptionalProperty $Registration 'com_registered' $false
)
$ProfileRegistered = [bool](
    Get-OptionalProperty $Registration 'profile_registered' $false
)
$IdentityConflict = [bool](
    Get-OptionalProperty $Registration 'identity_conflict' $false
)
$RegisteredComPath = Get-OptionalProperty $Registration 'com_path'
if ($ComRegistered -and $RegisteredComPath) {
    $RegisteredPath = [IO.Path]::GetFullPath([string]$RegisteredComPath)
    $ComPathConflict = -not $RegisteredPath.StartsWith(
        $InstallRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )
}

$Report = [ordered]@{
    experimental_profile_registered = $Registered
    experimental_com_registered = $ComRegistered
    experimental_language_profile_registered = $ProfileRegistered
    experimental_identity_conflict = $IdentityConflict
    experimental_clsid = $ExperimentalClsid
    experimental_profile_guid = $ExperimentalProfileGuid
    com_server_path = $RegisteredComPath
    server_executable_path = $ServerPath
    server_executable_present = Test-Path -LiteralPath $ServerPath -PathType Leaf
    server_process_running = [bool](
        Get-Process -Name 'NeuralWeaselServer' -ErrorAction SilentlyContinue
    )
    named_pipe_present = $PipePresent
    weasel_ipc_pipe_present = $WeaselPipePresent
    model_service_state = Get-OptionalProperty $ModelState 'state' 'not-started'
    backend = Get-OptionalProperty $ModelState 'backend'
    build_version = Get-OptionalProperty $Manifest 'repository_commit_sha'
    upstream_weasel_revision = Get-OptionalProperty `
        $Manifest 'upstream_weasel_revision'
    official_weasel_path_conflict = $ComPathConflict
    missing_required_files = $Missing
    recent_safe_log_location = $LogRoot
    candidate_ui_trace_present = Test-Path `
        -LiteralPath $CandidateUiTracePath -PathType Leaf
    candidate_ui_trace_path = $CandidateUiTracePath
    candidate_ui_trace_tail = $CandidateUiTraceTail
    rime_runtime_weasel_config_present = Test-Path `
        -LiteralPath $RuntimeWeaselConfigPath -PathType Leaf
    rime_deployed_weasel_config_present = Test-Path `
        -LiteralPath $DeployedWeaselConfigPath -PathType Leaf
    rime_shared_weasel_config_present = Test-Path `
        -LiteralPath $SharedWeaselConfigPath -PathType Leaf
    rime_deployed_weasel_precedes_shared = Test-Path `
        -LiteralPath $DeployedWeaselConfigPath -PathType Leaf
}
if (Get-Variable PipeProbeError -ErrorAction SilentlyContinue) {
    $Report.named_pipe_probe_error = $PipeProbeError
}
if (Get-Variable CandidateUiTraceReadError -ErrorAction SilentlyContinue) {
    $Report.candidate_ui_trace_read_error = $CandidateUiTraceReadError
}
$Report | ConvertTo-Json -Depth 5
