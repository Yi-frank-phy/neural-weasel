# Handoff: Neural Weasel Q4 runtime / TSF no-candidates debug state

## TL;DR for the next agent
神经小狼毫（实验）输入法已正常激活，按键到达 rime 引擎，自定义组件已注册，但候选为空。
最后断点已收窄：ContextUpdateBridge 没有发布 AcceptedEditorContext。
门探针值：valid=0 epoch=0 rev=0 caplen=0；同时 broker 已收到真实上下文帧（kind=1）。

## Already proven
1. Q4 local-verified model service works; GPU guards pass.
2. Server must pass traits.modules to librime initialize (not setup); manual fix in deps tree works: components ai_translator + bilingual_key_processor register successfully.
3. TSF context frames reach ContextCaptureBroker (broker-debug.log: FORWARD kind=1 rev=4..9 frames about 4480 bytes).
4. Model service ack is instant; context snapshot becomes ready in about 369 ms for a paragraph (measured).
5. Bridge readiness_timeout raised 200 ms -> 3000 ms in native/context/context_update_bridge.h.

## Current exact break
- AiTranslator::Query runs on every key (gate-debug.log input_len 1..n) but always sees valid=0 epoch=0 rev=0 caplen=0.
- Schema has only ai_translator + punct_translator, so no candidates appear.
- Broker forwards kind=1 frames and Python epoch can advance, but the bridge either never submits, never parses the ack, or never publishes. Latest observation: python pipe health epoch stayed at 18 despite new kind=1 frames.

## Next experiment (narrow, falsifiable)
1. Instrument native/context/context_update_bridge.cc: log Submit, after BuildContextRequest, transport TryQuery result, ParseContextUpdateAcknowledgement assigned epoch, each health-loop ready_epoch, and every early exit/SetResult.
2. Rebuild server (scripts below), deploy exe, restart + activate, user types nihao in fresh notepad.
3. Read C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\Logs\bridge-debug.log and compare.

## Key locations
- repo root: C:\Users\zhaoy\Downloads\neural-weasel-repo
- build deps: C:\Users\zhaoy\Downloads\neural-weasel-build-deps
- patched weasel source tree: C:\Users\zhaoy\Downloads\neural-weasel-build-deps\weasel (pinned 9cc96e20dc71b80876b12f689bb5863c76c2a7ed)
- installed runtime dir: C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile
- dist bundle: C:\Users\zhaoy\Downloads\neural-weasel-repo\dist\neural-weasel-experimental
- logs: C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\Logs
  - neural-server.stderr.log
  - gate-debug.log
  - broker-debug.log
  - rime.neural_weasel_experimental INFO/WARNING/ERROR logs
- Q4 gguf model: C:\Users\zhaoy\AppData\Local\NeuralWeasel\gguf-poc\models\Qwen3.5-4B-Q4_K_M.gguf (SHA 00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4)
- xmake artifacts: C:\Users\zhaoy\Downloads\neural-weasel-build-deps\weasel\build\windows\x64\release\WeaselServer\NeuralWeaselServer.exe and ...\WeaselTSF\NeuralWeaselExperimentalTSF.dll

## Build / deploy commands used so far
Env for xmake: NEURAL_WEASEL_ROOT=C:\Users\zhaoy\Downloads\neural-weasel-repo ; BOOST_ROOT=C:\Users\zhaoy\Downloads\neural-weasel-build-deps\boost_1_78_0 ; VCPKG_ROOT=C:\Users\zhaoy\Downloads\neural-weasel-build-deps\vcpkg ; VERSION_MAJOR=0 VERSION_MINOR=17 VERSION_PATCH=4 FILE_VERSION=0,17,4,0 PRODUCT_VERSION=0.17.4.0 ; also call vcvars64.bat first.
- build: cmd file C:\Users\zhaoy\Downloads\neural-weasel-repo\build\run-xmake-rebuild.cmd (runs xmake -y)
- deploy server: copy artifact to C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile\NeuralWeaselServer.exe; then C:\Users\zhaoy\Downloads\neural-weasel-repo\build\restart-server-logged.ps1
- activate tip: C:\Users\zhaoy\Downloads\neural-weasel-repo\build\dedupe-activate.ps1
- full bundle reinstall needs UAC: C:\Users\zhaoy\Downloads\neural-weasel-repo\build\elevated-q4-install.ps1

## Modified files to clean / promote later
Repo (README to reconcile):
- native/context/context_update_bridge.h: readiness_timeout 3000 ms (real fix; needs test+commit)
- native/rime/ai_translator.cc: debug file probes (remove before merge)
- native/context/context_capture_broker.cc: debug file probes (remove before merge)
- scripts/prepare-weasel-overlay.ps1: already committed BOM + encoding-aware Read/Write fixes
- tests/test_install_safety_v02.py: overlay rebuild regression test present

Deps weasel tree (NOT in repo):
- RimeWithWeasel/RimeWithWeasel.cpp real fix: Initialize builds RimeTraits with modules {default, ai_translator} plus shared/user dirs plus app_name/log_dir and calls initialize(&traits) instead of initialize(NULL). This must be promoted into prepare-weasel-overlay.ps1 as a Replace-RegexOnce seam.
- Several temporary debug edits (WeaselServer.cpp, WeaselIPCServer/WeaselServerImpl.cpp, RimeWithWeasel.cpp, context files) not yet cleaned.

## Environment notes for the next agent
- Windows 11, GPU RTX 4060 Laptop 8188 MiB, user zhaoy.
- WSL bash at repo root; Windows PowerShell via /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe.
- Tool transport traps: literal dollar identifiers are stripped from command text; backtick and some backslash sequences are mangled. Always generate .ps1/.cmd/.cpp snippets through a local python file-writer rather than inline heredoc text.
- Long-running Bash->PowerShell calls exceeding ~120 s can kill child process trees; use detached Start-Process and short poll calls.
- Input method list shows 神经小狼毫（实验） CLSID 8AA66261-ED5F-46B0-895D-339B42C3AE1B. The name 安全版 does not appear in that list; both refer to the same project.
