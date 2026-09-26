[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ServiceScript,
    [Parameter(Mandatory)]
    [ValidateSet('Q4_K_M', 'Q8_0')]
    [string]$Quantization,
    [Parameter(Mandatory)]
    [AllowEmptyString()]
    [string]$GgufPath,
    [Parameter(Mandatory)][string]$StdOutPath,
    [Parameter(Mandatory)][string]$StdErrPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$AutomaticGgufPathToken = '__NEURAL_WEASEL_AUTOMATIC_GGUF__'
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'NeuralWeasel\Experimental'
$StatePath = Join-Path $RuntimeRoot 'model-service.json'

function Write-SupervisorLog {
    param([Parameter(Mandatory)][string]$Message)
    $timestamp = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    $line = "[$timestamp] [GameSupervisor] $Message`r`n"
    try {
        $parentDir = Split-Path -Parent $StdOutPath
        if (-not (Test-Path -LiteralPath $parentDir)) {
            New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
        }
        [IO.File]::AppendAllText(
            $StdOutPath,
            $line,
            (New-Object Text.UTF8Encoding($false))
        )
    } catch {
        # Non-terminating logging fallback
    }
}

function Quote-ProcessArgument {
    param([Parameter(Mandatory)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

# Define Win32 Fullscreen / Game Detector if not already defined
if (-not ([System.Management.Automation.PSTypeName]'NeuralWeaselGameDetector').Type) {
    Add-Type -TypeDefinition @"
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public class NeuralWeaselGameDetector {
    [DllImport("shell32.dll")]
    public static extern int SHQueryUserNotificationState(out int quns);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenDesktop(string lpszDesktop, int dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetThreadDesktop(IntPtr hDesktop);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool CloseDesktop(IntPtr hDesktop);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);

    [DllImport("user32.dll", EntryPoint = "GetWindowLong")]
    private static extern int GetWindowLong32(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr")]
    private static extern IntPtr GetWindowLongPtr64(IntPtr hWnd, int nIndex);

    public static long GetStyle(IntPtr hWnd) {
        if (IntPtr.Size == 8)
            return GetWindowLongPtr64(hWnd, -16).ToInt64();
        else
            return (long)GetWindowLong32(hWnd, -16);
    }

    public static bool HasCaption(IntPtr hWnd) {
        long s = GetStyle(hWnd);
        return (s & 0x00C00000L) == 0x00C00000L; // WS_CAPTION = WS_BORDER | WS_DLGFRAME
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct MONITORINFO {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
    }

    public class Evaluation {
        public bool IsFullscreenGame;
        public int Quns;
        public string ProcessName;
        public uint ProcessId;
        public string WindowTitle;
        public string Reason;
    }

    private static readonly string[] SystemExclusions = new string[] {
        "explorer", "textinputhost", "shellexperiencehost", "searchhost",
        "startmenuexperiencehost", "lockapp", "applicationframehost", "dwm"
    };

    public static Evaluation Check() {
        var eval = new Evaluation();
        int quns = 0;
        SHQueryUserNotificationState(out quns);
        eval.Quns = quns;

        Thread t = new Thread(() => {
            IntPtr hDesk = OpenDesktop("default", 0, false, 0x01FF);
            if (hDesk == IntPtr.Zero) {
                eval.Reason = "OpenDesktop failed";
                return;
            }
            if (!SetThreadDesktop(hDesk)) {
                eval.Reason = "SetThreadDesktop failed";
                CloseDesktop(hDesk);
                return;
            }

            IntPtr fg = GetForegroundWindow();
            if (fg == IntPtr.Zero) {
                eval.Reason = "Foreground is null";
                CloseDesktop(hDesk);
                return;
            }

            uint pid = 0;
            GetWindowThreadProcessId(fg, out pid);
            eval.ProcessId = pid;
            string name = "unknown";
            try {
                using (var p = Process.GetProcessById((int)pid)) {
                    name = p.ProcessName.ToLowerInvariant();
                }
            } catch {}
            eval.ProcessName = name;

            StringBuilder sb = new StringBuilder(256);
            GetWindowText(fg, sb, 256);
            eval.WindowTitle = sb.ToString();

            foreach (var ex in SystemExclusions) {
                if (name == ex) {
                    eval.Reason = "System process: " + name;
                    CloseDesktop(hDesk);
                    return;
                }
            }

            if (quns == 3) {
                eval.IsFullscreenGame = true;
                eval.Reason = "QUNS_RUNNING_D3D_FULL_SCREEN";
                CloseDesktop(hDesk);
                return;
            }

            RECT wr;
            if (GetWindowRect(fg, out wr)) {
                IntPtr hMon = MonitorFromWindow(fg, 2);
                MONITORINFO mi = new MONITORINFO();
                mi.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
                if (GetMonitorInfo(hMon, ref mi)) {
                    bool coversMonitor = (wr.Left <= mi.rcMonitor.Left &&
                                          wr.Top <= mi.rcMonitor.Top &&
                                          wr.Right >= mi.rcMonitor.Right &&
                                          wr.Bottom >= mi.rcMonitor.Bottom);
                    bool hasCaption = HasCaption(fg);
                    if (coversMonitor && !hasCaption) {
                        eval.IsFullscreenGame = true;
                        eval.Reason = string.Format("Borderless/Fullscreen window ({0}x{1}) without caption",
                            wr.Right - wr.Left, wr.Bottom - wr.Top);
                    } else if (coversMonitor && hasCaption) {
                        eval.Reason = "Maximized window with caption";
                    } else {
                        eval.Reason = "Normal window";
                    }
                }
            }

            CloseDesktop(hDesk);
        });

        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        return eval;
    }
}
"@
}

function Update-SuspendedState {
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try {
            $stateObj = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
            if ($stateObj) {
                $stateObj.state = 'suspended-fullscreen'
                $stateObj.pid = $null
                $stateObj.updated_utc = [DateTime]::UtcNow.ToString('o')
                $tempPath = "$StatePath.tmp-$PID"
                $encoding = New-Object Text.UTF8Encoding($false)
                $json = $stateObj | ConvertTo-Json
                [IO.File]::WriteAllText($tempPath, $json, $encoding)
                Move-Item -LiteralPath $tempPath -Destination $StatePath -Force
            }
        } catch {
            Write-SupervisorLog "Failed to update state to suspended: $_"
        }
    }
}

function Start-SupervisedChild {
    param(
        [string]$PowerShellExe,
        [string]$ChildArguments,
        [string]$WorkingDirectory
    )

    $parentDir = Split-Path -Parent $StdOutPath
    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $PowerShellExe
    $startInfo.Arguments = $ChildArguments
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo

    $outStream = [IO.File]::Open(
        $StdOutPath,
        [IO.FileMode]::Append,
        [IO.FileAccess]::Write,
        [IO.FileShare]::ReadWrite
    )
    $errStream = [IO.File]::Open(
        $StdErrPath,
        [IO.FileMode]::Append,
        [IO.FileAccess]::Write,
        [IO.FileShare]::ReadWrite
    )

    if (-not $process.Start()) {
        $outStream.Dispose()
        $errStream.Dispose()
        throw 'The supervised model-service process could not be started.'
    }

    $outCopy = $process.StandardOutput.BaseStream.CopyToAsync($outStream, 4096)
    $errCopy = $process.StandardError.BaseStream.CopyToAsync($errStream, 4096)

    Write-SupervisorLog "Model service child process launched (PID: $($process.Id))."

    return @{
        Process     = $process
        StdOutTask  = $outCopy
        StdErrTask  = $errCopy
        StdOutStream= $outStream
        StdErrStream= $errStream
    }
}

function Stop-SupervisedChild {
    param([hashtable]$Bundle)

    if ($null -eq $Bundle) { return }
    $proc = $Bundle.Process
    if ($proc -and -not $proc.HasExited) {
        $childPid = $proc.Id
        Write-SupervisorLog "Stopping child process tree (PID: $childPid)..."
        try {
            & taskkill.exe /PID $childPid /T /F *> $null
        } catch {
            Write-SupervisorLog "taskkill error: $_"
        }
        $null = $proc.WaitForExit(3000)
    }

    try { $Bundle.StdOutTask.Wait(1000) } catch {}
    try { $Bundle.StdErrTask.Wait(1000) } catch {}
    if ($Bundle.StdOutStream) { $Bundle.StdOutStream.Dispose() }
    if ($Bundle.StdErrStream) { $Bundle.StdErrStream.Dispose() }
    if ($proc) { $proc.Dispose() }

    Update-SuspendedState
    Write-SupervisorLog "Model service child process tree terminated and state suspended."
}

# Clean up any orphaned model service or python processes from previous runs
function Stop-LingeringService {
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
            if ($state.pid) {
                $lingering = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
                if ($lingering -and ($lingering.ProcessName -match 'python|powershell|uv|neural-weasel')) {
                    Write-SupervisorLog "Found lingering model process PID $($lingering.Id). Terminating tree..."
                    & taskkill.exe /PID $lingering.Id /T /F *> $null
                }
            }
        } catch {}
    }
}

# Main Supervisor Execution
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
    (Quote-ProcessArgument $Quantization)
)
if ($GgufPath -ne $AutomaticGgufPathToken) {
    $ChildArguments += @(
        '-GgufPath',
        (Quote-ProcessArgument $GgufPath)
    )
}
$ChildArgumentsString = $ChildArguments -join ' '
$WorkingDir = Split-Path -Parent $ServiceScript

Write-SupervisorLog "Starting Neural Weasel Game-Aware Supervisor (PID: $PID)..."
Stop-LingeringService

$childBundle = $null
$isGameMode = $false
$consecutiveGameCount = 0
$consecutiveDesktopCount = 0
$gameThreshold = 2
$desktopThreshold = 2
$pollIntervalSeconds = 2

# Initial evaluation
$initEval = [NeuralWeaselGameDetector]::Check()
if ($initEval.IsFullscreenGame) {
    $isGameMode = $true
    Update-SuspendedState
    Write-SupervisorLog "Initial environment is Fullscreen Game ($($initEval.ProcessName): $($initEval.Reason)). Service suspended."
} else {
    try {
        $childBundle = Start-SupervisedChild -PowerShellExe $PowerShellExe -ChildArguments $ChildArgumentsString -WorkingDirectory $WorkingDir
    } catch {
        Write-SupervisorLog "Initial child startup failed: $_"
    }
}

try {
    while ($true) {
        Start-Sleep -Seconds $pollIntervalSeconds

        $eval = [NeuralWeaselGameDetector]::Check()

        if ($eval.IsFullscreenGame) {
            $consecutiveDesktopCount = 0
            $consecutiveGameCount++

            if (-not $isGameMode -and $consecutiveGameCount -ge $gameThreshold) {
                $isGameMode = $true
                Write-SupervisorLog "Fullscreen game detected ($($eval.ProcessName), PID $($eval.ProcessId): $($eval.Reason)). Evicting model service..."
                if ($childBundle) {
                    Stop-SupervisedChild -Bundle $childBundle
                    $childBundle = $null
                } else {
                    Stop-LingeringService
                    Update-SuspendedState
                }
            }
        } else {
            $consecutiveGameCount = 0
            $consecutiveDesktopCount++

            if ($isGameMode) {
                if ($consecutiveDesktopCount -ge $desktopThreshold) {
                    $isGameMode = $false
                    Write-SupervisorLog "Exited fullscreen game. Restoring model service to desktop..."
                    try {
                        $childBundle = Start-SupervisedChild -PowerShellExe $PowerShellExe -ChildArguments $ChildArgumentsString -WorkingDirectory $WorkingDir
                    } catch {
                        Write-SupervisorLog "Child recovery failed: $_"
                    }
                }
            } else {
                # In normal desktop mode: verify child health
                if ($null -eq $childBundle -or $childBundle.Process.HasExited) {
                    if ($childBundle -and $childBundle.Process.HasExited) {
                        Write-SupervisorLog "Child process exited unexpectedly with code $($childBundle.Process.ExitCode). Restarting in 3 seconds..."
                        Stop-SupervisedChild -Bundle $childBundle
                        $childBundle = $null
                        Start-Sleep -Seconds 3
                    }
                    try {
                        $childBundle = Start-SupervisedChild -PowerShellExe $PowerShellExe -ChildArguments $ChildArgumentsString -WorkingDirectory $WorkingDir
                    } catch {
                        Write-SupervisorLog "Child restart failed: $_"
                    }
                }
            }
        }
    }
} finally {
    Write-SupervisorLog "Supervisor exiting. Cleaning up child processes..."
    if ($childBundle) {
        Stop-SupervisedChild -Bundle $childBundle
    }
}
