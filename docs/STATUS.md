# Implementation status

This file distinguishes implemented and tested behavior from planned integration work.

## v0.2 implemented and tested

- Specification-first contract in:
  - `docs/specs/v0.2-testable-bilingual-ime.md`;
  - `docs/specs/v0.2-acceptance-tests.md`;
  - `docs/architecture/unified-constraint-engine.md`.
- Every v0.2 feature group has a separate failing-test commit before its feature
  commit.
- Minimal `ModelBackend` protocol with immutable state, diagnostics, private-state
  invalidation, full-logits CPU indexing, and sparse selected-row lm-head projection.
- Qwen runtime adapters for both paths. The sparse adapter calls the base transformer
  (`model.model`) and does not produce a complete vocabulary logit vector.
- Full/sparse deterministic top-1, top-k set, and score-consistency tests.
- Unified Candidate fields, one ranking/de-duplication path, pinyin constraint, Latin
  prefix constraint, and asymmetric context script policy.
- English-context Han candidates are hard rejected even when assigned a higher model
  score.
- Chinese context permits Latin candidates; no proper-name whitelist is used.
- Literal-safe key reducers in Python and native C++:
  - English `Space` commits literal plus a space;
  - `Tab` explicitly accepts completion;
  - `Escape` dismisses completion while preserving literal text;
  - Chinese `Space` remains Rime-default candidate acceptance.
- Retained immutable snapshot epochs and a background coordinator. Candidate query
  never calls context refresh/model forward and can use the old epoch while a new
  epoch is in flight.
- Unified `query_candidates` pipe protocol while retaining the v0.1
  `query_pinyin` handler for regression compatibility.
- Fail-closed experimental profile manifest and four required PowerShell entry
  scripts.
- Measured backend comparison and bilingual replay harnesses, exposed as CLI commands.
- Last complete non-Windows run before the CI-portability follow-ups:
  **136 passed, 21 Windows-only tests skipped**. The follow-ups add contract tests
  and do not change runtime code.
- Current Linux native check: the pure bilingual key-semantics C++ test compiles and
  runs successfully.
- GitHub Actions CI run 21 on the branch is green:
  - Python 3.12: **149 passed, 3 Torch-bound modules skipped** in the intentionally
    Torch-free job; lint and format checks passed.
  - MSVC: native boundaries, librime translator, and bilingual processor compiled
    against fixed librime `1.15.0`; all native CTest state-machine tests passed.

## v0.2 implemented but not validated on target hardware

- Real Qwen sparse hidden-state publication and selected-row projection.
- Real full/sparse latency, GPU memory, publication latency, and numerical tolerance
  comparison.
- Real bilingual replay using either backend.

The required commands exist, but this environment does not expose the user's RTX 4060
Laptop GPU or cached model. No v0.2 performance numbers are claimed.

## v0.2 partial/scaffold only

- `scripts/install-dev-profile.ps1` and
  `scripts/uninstall-dev-profile.ps1` validate and target only the reserved
  experimental identity and directory.
- The scripts require `NeuralWeaselExperimentalTSF.dll` and
  `NeuralWeaselProfileTool.exe`, neither of which is produced by this branch.
- Existing context capture, pipe client, Rime translator, fallback state machine, and
  profile planning remain source-level integration boundaries.

## v0.2 not implemented

- A consistently isolated Weasel 0.17.4 fork producing the experimental TSF DLL,
  WeaselServer, RimeWithWeasel module, renamed IPC endpoint, and profile tool.
- Installation, manual profile switching, Chinese/English editor smoke tests, and
  uninstall verification in a Windows test user or VM.
- Live conditional Base-model sequence scoring for cross-token English words. The
  shared representation and tests support multi-token paths, but the live tokenizer
  catalog currently emits only one-token completions and uses the selected next-token
  score.
- Candidate refresh notification from a completed asynchronous multi-token English
  expansion.

These omissions mean the branch does **not** meet the requested “user can install and
start typing” completion condition and must not be described as such.

## Implemented in the repository

- Python 3.12 project and reproducible `uv.lock`.
- Strict pre-import RTX 4060 Laptop GPU isolation.
- Base-only checkpoint allowlist for Qwen3.5 0.8B and 4B.
- Text-only Hugging Face forward path with immutable full-vocabulary logit snapshots.
- Continuous full-pinyin token index with heteronyms, explicit apostrophe boundaries,
  incomplete prefixes, and single-character coverage.
- Model-revision, tokenizer, pypinyin-data, and schema cache invalidation.
- Precompiled pinyin query plans and NumPy exact top-k over the complete legal
  token set, with no arbitrary pre-ranking truncation.
- Asynchronous context updates and revision-addressable immutable snapshots.
- Per-user Windows named-pipe protocol and client/server implementation.
- Context redaction helpers and log-safe metadata.
- Pipe first-instance protection, server-user identity checks, and listener
  recovery after all persistent instances are occupied.
- Secure-focus cleanup that invalidates pending/in-flight work and removes all
  addressable model snapshots before acknowledgement.
- Bounded multi-token constrained beam core with constraint-before-top-k,
  canonical token paths, and cache-safe serial replay.
- Source-level TSF profile discovery and latched Microsoft Pinyin fallback
  state machine; neither is registered or activated.

## Verified runtime-independent gates

- Official `Qwen/Qwen3.5-0.8B-Base` tokenizer index built:
  102,368 model-token pronunciation rows and 43,026 coverage rows.
- All 3,755 GB2312 level-1 Han characters are inputtable.
- On the real 0.8B index with a 248,320-element cached logit vector, 500-query
  microbenchmarks measured:
  - `j`: p50 0.191 ms, p95 0.240 ms, p99 0.291 ms;
  - `jiuc`: p50 0.045 ms, p95 0.084 ms, p99 0.139 ms;
  - `jiuchan`: p50 0.114 ms, p95 0.151 ms, p99 0.253 ms.
- A 10,000-request persistent Named Pipe stress run at maximum client speed
  completed with zero missing, reordered, or wrong-epoch responses:
  p50 0.287 ms, p95 0.496 ms, p99 0.593 ms, maximum 21.120 ms.
- 109 Python tests pass, including deterministic worker-exit races, secure
  context invalidation, pipe squatting, capacity recovery, and 1,000 full-pinyin
  syllable combinations.

## Verified GPU/runtime gates

- CUDA PyTorch 2.11.0+cu128 is installed in the project environment.
- The launcher isolated the process to
  `NVIDIA GeForce RTX 4060 Laptop GPU`
  (`GPU-bf42efb8-87be-5177-685f-03af505a60c0`) before importing PyTorch.
- The official `Qwen/Qwen3.5-0.8B-Base` weight blob was verified against its
  1,746,942,600-byte size and SHA-256 digest before being admitted to the local
  Hugging Face cache.
- A real BF16 text-only forward produced `纠缠` as the first constrained
  candidate for context `该协议所消耗的` and pinyin `jiuchan`.
- Peak CUDA allocation/reservation was 1,471/1,480 MiB, below the 3 GiB gate;
  5,596 MiB remained free on the 8 GiB GPU.
- Safe append-only cache reuse is implemented only when the old token IDs are
  an exact prefix of the new token IDs. Equal token sequences reuse logits;
  tokenizer resegmentation and left-window movement force a full recomputation.
  No mixed Gated DeltaNet/KV cache is cropped, reset, or deep-copied.
- A real cached-suffix result matched the full-forward argmax. BF16 operation
  ordering produced a maximum logit difference of 0.125 and mean difference of
  approximately 0.02455.

## Historical v0.1 runtime observations

- The first 0.8B context forward took 1,489 ms. In a separate six-update warm
  run the measured latencies were 929, 419, 398, 348, 351, and 371 ms. This does
  **not** pass the planned 0.8B p95 target of 100 ms.
- After adding safe token-prefix cache reuse, a 29-update real append-only run
  measured the full snapshot-ready path (GPU forward plus immutable CPU logit
  copy) at p50 98.031 ms, p95 135.565 ms, p99 160.248 ms, and maximum
  164.536 ms. This is a substantial improvement but still does **not** pass the
  former p95 target.
- Transformers reported that the Qwen3.5 Gated DeltaNet fast path was
  unavailable because optional `flash-linear-attention` / `causal-conv1d`
  components were not installed, so it used the plain PyTorch fallback.
- v0.2 records these refresh times but no longer treats p95 under 100 ms as a
  release gate. Candidate quality under stale snapshots is the relevant replay metric.
- Repeat the 10,000-key stress test through the compiled C++ translator.

## Native integration status

The `native/` tree does not replace the existing Weasel profile. Its standalone CMake
boundaries and librime plugin are compiled by Windows CI, but a safe independent
Weasel fork remains missing. Do not register an official Weasel binary under the
experimental identifiers.

