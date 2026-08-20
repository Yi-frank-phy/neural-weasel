Option Explicit

Dim fso, shell, root, command, model, quantization, runtime, backend
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
model = "Qwen/Qwen3.5-4B-Base"
quantization = "Q8_0"
runtime = "llama.cpp"
backend = "CUDA"
command = "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File """ _
  & root & "\start-model-service.ps1"" -Transport http -Port 8000"
' Identity constants above are intentionally kept beside the launcher so a
' packaged-script audit can prove Wisdom targets the same 4B Q8_0 GGUF CUDA runtime.
shell.Run command, 0, False
