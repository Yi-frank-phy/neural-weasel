# Implementation status

This file separates repository/CI evidence from interactive Windows evidence.
The experimental slice is not production ready.

## Implemented in this branch

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
- An identity-locked per-user COM/TSF profile tool. It refuses identifiers
  outside the reserved pair and verifies TSF identity exports.
- Hash-verified, idempotent install/uninstall scripts with staging, dry runs,
  no default-profile activation, and no identifier override.
- A Base-only model launcher with `full` as the correctness default and
  explicit `sparse` failure; it never silently changes backend or checkpoint.
- Static neural translator and bilingual key processor forced into the pinned
  `RimeWithWeasel` module list.
- A pinned TSF `TextEditSink` hook that schedules read-only surrounding-text
  capture. Password/PIN, unknown policy, blacklisted system processes, and
  non-input desktops are denied. Pipe work runs later on a latest-wins worker.
- Shared Python/C++ key vectors for English Space/Tab/Escape/Enter, Chinese
  Space/Escape, Backspace, numbered selection, no candidate, stale candidate,
  and service failure.
- A Windows CI job that builds pinned dependencies and all native artifacts,
  runs CTest and disposable dry-run safety tests, scans binary/resource
  identities, and uploads `neural-weasel-experimental-x64`.

## Local evidence

- All non-Windows Python tests pass; Windows-only tests use platform skips.
- Ruff lint and formatting checks pass.
- The pure C++ key-semantics test builds and passes against the shared TSV.
- Protocol tests cover no snapshot, one latest snapshot, newer snapshot in
  flight, and latest plus retained snapshots.

Exact test counts and CI links belong in the PR/final report because they
change while the branch is being repaired.

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

The live English catalog remains the single-token baseline. Multi-token causal
rescoring, fuzzy/double/abbreviated pinyin, typo correction, automatic
Microsoft Pinyin fallback, GUI, and production hardening remain out of scope.
