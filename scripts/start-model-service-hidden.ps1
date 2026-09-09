[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ServiceScript,
    [Parameter(Mandatory)]
    [ValidateSet('Q4_K_M', 'Q8_0')]
    [string]$Quantization,
    [Parameter(Mandatory)][string]$GgufPath,
    [Parameter(Mandatory)][string]$StdOutPath,
    [Parameter(Mandatory)][string]$StdErrPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Quote-ProcessArgument {
    param([Parameter(Mandatory)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

$PowerShellExe = Join-Path $PSHOME 'powershell.exe'
$ChildArguments = @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-WindowStyle',
    'Hidden',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    (Quote-ProcessArgument $ServiceScript),
    '-Quantization',
    (Quote-ProcessArgument $Quantization),
    '-GgufPath',
    (Quote-ProcessArgument $GgufPath)
) -join ' '

$StartInfo = [Diagnostics.ProcessStartInfo]::new()
$StartInfo.FileName = $PowerShellExe
$StartInfo.Arguments = $ChildArguments
$StartInfo.WorkingDirectory = Split-Path -Parent $ServiceScript
$StartInfo.UseShellExecute = $false
$StartInfo.CreateNoWindow = $true
$StartInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$StartInfo.RedirectStandardOutput = $true
$StartInfo.RedirectStandardError = $true
$Process = [Diagnostics.Process]::new()
$Process.StartInfo = $StartInfo
$StdOutStream = $null
$StdErrStream = $null

try {
    $StdOutStream = [IO.File]::Open(
        $StdOutPath,
        [IO.FileMode]::Append,
        [IO.FileAccess]::Write,
        [IO.FileShare]::ReadWrite
    )
    $StdErrStream = [IO.File]::Open(
        $StdErrPath,
        [IO.FileMode]::Append,
        [IO.FileAccess]::Write,
        [IO.FileShare]::ReadWrite
    )
    if (-not $Process.Start()) {
        throw 'The hidden model-service process could not be started.'
    }
    $StdOutCopy = $Process.StandardOutput.BaseStream.CopyToAsync($StdOutStream, 4096)
    $StdErrCopy = $Process.StandardError.BaseStream.CopyToAsync($StdErrStream, 4096)
    $Process.WaitForExit()
    $StdOutCopy.GetAwaiter().GetResult()
    $StdErrCopy.GetAwaiter().GetResult()
    exit $Process.ExitCode
} catch {
    [IO.File]::AppendAllText(
        $StdErrPath,
        ($_ | Out-String),
        (New-Object Text.UTF8Encoding($false))
    )
    exit 1
} finally {
    if ($StdOutStream) {
        $StdOutStream.Dispose()
    }
    if ($StdErrStream) {
        $StdErrStream.Dispose()
    }
    $Process.Dispose()
}
