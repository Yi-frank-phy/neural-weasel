# Implementation status

This file distinguishes implemented and tested behavior from planned integration work.

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
- 103 Python tests pass, including deterministic worker-exit races, secure
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

## Failed or pending runtime gates

- The first 0.8B context forward took 1,489 ms. In a separate six-update warm
  run the measured latencies were 929, 419, 398, 348, 351, and 371 ms. This does
  **not** pass the planned 0.8B p95 target of 100 ms.
- Transformers reported that the Qwen3.5 Gated DeltaNet fast path was
  unavailable because optional `flash-linear-attention` / `causal-conv1d`
  components were not installed, so it used the plain PyTorch fallback.
- Do not migrate to the 4B checkpoint or claim production readiness until the
  background context-refresh bottleneck has a simple, reproducible solution.
- Repeat the 10,000-key stress test through the compiled C++ translator.

## Native integration status

The `native/` tree is an experimental source scaffold. It is not installed into the
system and does not replace the existing Weasel profile. Building it requires the
Visual Studio C++ workload, CMake, the exact Weasel 0.17.4 source tree, and librime
headers/libraries. Those build tools were not present at initial bootstrap.

Do not register the experimental TSF profile until the Python core and named-pipe
latency gates pass.
