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
- Cached candidate ranking with no arbitrary pre-ranking top-k truncation.
- Asynchronous context updates and revision-addressable immutable snapshots.
- Per-user Windows named-pipe protocol and client/server implementation.
- Context redaction helpers and log-safe metadata.

## Pending runtime gates

- Download and benchmark `Qwen/Qwen3.5-0.8B-Base`.
- Verify the 0.8B BF16 peak stays below 3 GiB.
- Build the official tokenizer index and validate 3,500 common characters.
- Run the 10,000-key stress test.

## Native integration status

The `native/` tree is an experimental source scaffold. It is not installed into the
system and does not replace the existing Weasel profile. Building it requires the
Visual Studio C++ workload, CMake, the exact Weasel 0.17.4 source tree, and librime
headers/libraries. Those build tools were not present at initial bootstrap.

Do not register the experimental TSF profile until the Python core and named-pipe
latency gates pass.

