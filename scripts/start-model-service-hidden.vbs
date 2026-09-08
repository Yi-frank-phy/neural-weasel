Option Explicit

If WScript.Arguments.Count <> 5 Then
  WScript.Quit 2
End If

Function Quote(value)
  Quote = Chr(34) & Replace(value, Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function

Dim shell, fileSystem, powershell, hiddenRunner, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
powershell = shell.ExpandEnvironmentStrings( _
  "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
hiddenRunner = fileSystem.BuildPath( _
  fileSystem.GetParentFolderName(WScript.ScriptFullName), _
  "start-model-service-hidden.ps1")
command = Quote(powershell) & _
  " -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden" & _
  " -ExecutionPolicy Bypass -File " & Quote(hiddenRunner) & _
  " -ServiceScript " & Quote(WScript.Arguments(0)) & _
  " -Quantization " & Quote(WScript.Arguments(1)) & _
  " -GgufPath " & Quote(WScript.Arguments(2)) & _
  " -StdOutPath " & Quote(WScript.Arguments(3)) & _
  " -StdErrPath " & Quote(WScript.Arguments(4))

' Window style 0 prevents a console window; waiting keeps Task Scheduler tied
' to the real service lifetime so its restart policy still applies.
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
