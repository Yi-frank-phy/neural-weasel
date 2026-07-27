[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$ExperimentalClsid = '{8AA66261-ED5F-46B0-895D-339B42C3AE1B}'
$ExperimentalProfileGuid = '{C9B3984E-A16C-4779-80E8-ACD988C57B0D}'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\experimental-profile'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$Report = [ordered]@{
    os = [Environment]::OSVersion.VersionString
    is_64_bit = [Environment]::Is64BitOperatingSystem
    experimental_clsid = $ExperimentalClsid
    experimental_profile_guid = $ExperimentalProfileGuid
    install_root = $InstallRoot
    install_present = Test-Path -LiteralPath $InstallRoot -PathType Container
    model_pipe_present = $false
    gpu = $null
}

try {
    $Report.model_pipe_present = [bool](
        Get-ChildItem -LiteralPath '\\.\pipe\' -ErrorAction Stop |
            Where-Object { $_.Name -like 'NeuralWeasel-v1-*' } |
            Select-Object -First 1
    )
} catch {
    $Report.pipe_probe_error = $_.Exception.Message
}

Push-Location -LiteralPath $ProjectRoot
try {
    $Report.gpu = (& uv run neural-weasel gpu-info 2>&1 | Out-String).Trim()
} catch {
    $Report.gpu_error = $_.Exception.Message
} finally {
    Pop-Location
}

$Report | ConvertTo-Json -Depth 4
