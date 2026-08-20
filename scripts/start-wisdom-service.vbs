Option Explicit

Dim fso, shell, root, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File """ _
  & root & "\start-model-service.ps1"" -Transport http -Port 8000 -Backend full -Precision fp8"
shell.Run command, 0, False
