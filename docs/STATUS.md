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

## Pending GPU/runtime gates

- Finish installing CUDA PyTorch and download the 0.8B Base weights.
- Verify strict CUDA UUID binding and text-only model construction.
- Measure context-forward latency and confirm 0.8B BF16 peak stays below 3 GiB.
- Repeat the 10,000-key stress test through the compiled C++ translator.

## Native integration status

The `native/` tree is an experimental source scaffold. It is not installed into the
system and does not replace the existing Weasel profile. Building it requires the
Visual Studio C++ workload, CMake, the exact Weasel 0.17.4 source tree, and librime
headers/libraries. Those build tools were not present at initial bootstrap.

Do not register the experimental TSF profile until the Python core and named-pipe
latency gates pass.
