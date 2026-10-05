# Implementation status

This file separates repository/CI evidence from interactive Windows evidence.
The experimental slice is not production ready.

Reviewed against `main` on 2026-10-05. This is a source-level overview, not a
fresh measurement of the installed runtime. See [the documentation index](README.md)
for current procedures and historical evidence.

## Implemented in main

- Unified `context_epoch = 0` behavior: use the latest available snapshot, or
  literal fallback when none exists. This applies to `query_candidates`,
  legacy `query_pinyin`, and the native translator.
- Fixed configurable Latin prior penalty in Chinese context. Explicit Latin
  shapes may cancel it; there is no cross-candidate model-margin rule.
- A pinned Weasel `0.17.4` overlay at
  `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`.
- Real Windows outputs: `NeuralWeaselExperimentalTSF.dll`,
  `NeuralWeaselServer.exe`, `NeuralWeaselProfileTool.exe`, and the linked
  `RimeWithWeasel` static neural module evidence.
- Independent CLSID, profile GUID, display name, process name, Weasel IPC
  identity, model pipe, registry root, install root, Rime user directory, and
  log directory.
- An identity-locked machine-wide COM/per-user TSF profile tool. It refuses identifiers
  outside the reserved pair and verifies TSF identity exports.
- Hash-verified, idempotent install/uninstall scripts with staging, dry runs,
  no default-profile activation, and no identifier override.
- A Base-only GGUF/llama.cpp CUDA launcher, defaulting to Q8 with explicit
  `Q4_K_M` selection. Legacy Torch full/sparse comparison is a development tool.
- Bounded shorthand-pinyin traversal, progressive candidate paging and
  background multi-token Chinese/Latin conditional scoring.
- Opt-in FIM scoring with both caret sides; continuation remains the default.
  FIM changes the prompt, not the Windows text-acquisition interface.
- Static neural translator and bilingual key processor forced into the pinned
  `RimeWithWeasel` module list.
- A pinned TSF `TextEditSink` hook that schedules read-only surrounding-text
  capture. Password/PIN, unknown policy, blacklisted system processes, and
  non-input desktops are denied. A bounded capture sender forwards snapshots
  to the experimental server; model-service IPC and readiness waits run there.
- Shared Python/C++ key vectors for default-literal and explicitly selected
  English Space, Tab/Escape/Enter, Chinese Space/Escape, Backspace, numbered
  selection, no candidate, stale candidate, and service failure.
- A Windows CI job that builds pinned dependencies and all native artifacts,
  runs CTest and disposable dry-run safety tests, scans binary/resource
  identities, and uploads `neural-weasel-experimental-x64`.

## Automated evidence and its limits

- Dated experiment reports record test results for their named source revisions;
  they do not establish a passing result for every later commit.
- The pure C++ key-semantics test uses the shared TSV.
- Protocol tests cover no snapshot, one latest snapshot, newer snapshot in
  flight, and latest plus retained snapshots.

Exact test counts, source revisions and CI links belong in the dated reports
and current CI run. See [context-engine testing](experiments/context-engine-full-test-20261002.md)
and [disconnect recovery](experiments/context-update-recovery-20261002.md).

## Windows CI evidence

After the branch workflow completes it verifies MSVC compilation, CTest, the
pinned Weasel/librime build, all required PE/static-library outputs, bundle
hashes, identity scanning, and repeated dry-run safety cases.

CI intentionally does **not** register a global TSF profile on a shared runner.

## Manual evidence still required

The procedure in `docs/manual/windows-install-smoke-test.md` must still be run
in Windows Sandbox, a disposable VM, or a dedicated test user for:

- actual COM/TSF registration and `Win+Space` visibility;
- Chinese/English typing and editor Enter behavior;
- secure-field behavior;
- model-service failure and server restart in real editors;
- full unregister/removal;
- confirmation that official Weasel and Microsoft Pinyin are unaffected.

Fuzzy/double pinyin, typo correction, expanded English UX acceptance, automatic
Microsoft Pinyin fallback and production hardening remain deferred.
Direct Windows UIA capture is tracked separately in
[issue #43](https://github.com/Yi-frank-phy/neural-weasel/issues/43).
The [target-machine handoff](handoff/codex-neural-weasel-debug.md) records partial
installation and typing observations; the full manual acceptance matrix remains open.
