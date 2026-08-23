# Neural Weasel agent baseline

This file is the authoritative repository-level instruction for coding agents working on this checkout. If an older chat, handoff, or scratch note conflicts with this file or current `main`, follow this file and current `main`.

## Current installation baseline

- **Q8 is allowed. There is no project rule banning Q8.**
- The current production GGUF artifact in `main` is `Qwen3.5-4B-Base.Q8_0.gguf`.
- Q4, Q6, and Q8 are runtime/quantization choices. Functional contracts must remain quantization-independent unless a model artifact itself imposes a measured hardware limit.
- Do not route work away from the Neural TSF path merely because an old conversation said "Q8 is disabled" or "only Q6 may be used". Those constraints are obsolete.
- The current preferred route for **real editor surrounding context** is the repository's Neural experimental TSF pipeline (`native/tsf/*` + `native/context/*`). The official Weasel shell plus self-commit history is not an equivalent replacement.
- Do not invent an external UIA/file context bridge as a workaround for the obsolete Q8 ban unless the user explicitly requests that architecture.

## Immediate priority

The immediate goal is to get the Neural experimental input method installed and working on the target Windows machine, then perform the checked-in manual smoke tests. Do not postpone installation in order to implement Engram, English candidate expansion, fuzzy pinyin, or typo correction.

When working locally:

1. Start from current `main` and preserve the merged editor-context security contract.
2. Build/install the Neural experimental TSF bundle.
3. Validate registration, `Win+Space` visibility, normal editor typing, real surrounding context, protected/password fields, server failure behavior, uninstall, and latency.
4. Fix only failures demonstrated on the target machine, using RED -> root cause -> minimal fix -> regression test.

## Security boundary that must not regress

- Password/PIN/protected fields: zero surrounding-text capture.
- PRIVATE context may be used ephemerally for prediction but must not persist.
- Raw editor context must not enter logs, databases, telemetry, caches, crash artifacts, or Engram.
- TSF must remain capture/send only: no model invocation, Python IPC ownership, blocking backend wait, or model worker in the TSF DLL.
- Context transport remains bounded, one-way, nonblocking, identity-checked, and latest-revision wins.
- Stale focus/session/revision state must never publish candidates into a newer editor context.

## Deferred feature direction

After target-machine installation is healthy:

- Chinese correction order is a hard tier order: exact pinyin > QWERTY-neighbor typo correction > explicitly supported fuzzy-pinyin mappings. Model or personalization scores may rank only within a tier; they may not cross the tier boundary.
- English mode is an Apple-style neural candidate bar, not Tab completion: five Latin-only candidates may include ordinary completions, corrections, long-word candidates, and phrase candidates.
- English mode must never emit Han candidates. Chinese mode may emit Latin candidates with a strong default disadvantage when context/model evidence supports them.
- Engram is deferred. When implemented, it is a compressed preference/reranking layer and must not persist raw editor context or sentence history.
