# Neural Weasel

Neural Weasel is an experimental Windows pinyin IME whose candidate probability comes
from a local **Qwen Base** causal language model. Pinyin is a hard legality constraint;
the model never receives a chat prompt and the per-key path never runs a model forward.

The repository currently contains the independently testable core:

- strict RTX 4060 Laptop GPU launcher and runtime guard;
- Hugging Face text-only `Qwen3_5ForCausalLM` backend;
- token-to-pinyin index with heteronym support;
- continuous full-pinyin prefix matching and single-character coverage;
- immutable context-logit snapshots and ranked candidate queries;
- bounded multi-token constrained-beam core for background expansion;
- length-prefixed JSON protocol and Windows named-pipe service;
- CLI commands for index building, prediction, serving, and benchmarking.

The repository also contains a source-level Weasel/librime/TSF integration scaffold.
It is deliberately not registered or installed until the Python core passes its
correctness, GPU, and latency gates.

The repository is licensed under GPL-3.0-or-later because the eventual native build
links into GPLv3-licensed Weasel. Qwen model weights keep their own upstream license and
are downloaded separately; they are never committed here.

## Safety and privacy

- The launcher refuses to start unless it finds exactly one
  `NVIDIA GeForce RTX 4060 Laptop GPU`.
- It sets `CUDA_VISIBLE_DEVICES` to that GPU UUID before importing PyTorch.
- `device_map="auto"`, CPU/disk offload, chat templates, Instruct checkpoints, and
  silent CPU fallback are rejected.
- Generated indexes, model caches, private context, and logs live under
  `%LOCALAPPDATA%\NeuralWeasel`, outside the repository.
- Context text is never written to normal logs.

## Bootstrap

```powershell
uv python install 3.12
uv sync --extra dev
uv run neural-weasel gpu-info
uv run neural-weasel build-index --model Qwen/Qwen3.5-0.8B-Base
uv run neural-weasel predict `
  --model Qwen/Qwen3.5-0.8B-Base `
  --before "该协议所消耗的" `
  --pinyin "jiuchan"
```

The first model command downloads the official Base checkpoint into the Hugging Face
cache beneath `%LOCALAPPDATA%\NeuralWeasel`.

## Scope of v0.1

Supported:

- toneless continuous full pinyin;
- apostrophe separators;
- multiple token pronunciation paths;
- incomplete trailing syllables;
- deletion/retyping (query is stateless in raw keys);
- direct model-token candidates and last-resort single-character coverage.

Not yet supported:

- double pinyin, abbreviation, fuzzy pinyin, tones, or typo correction;
- wiring multi-token beam results into the background snapshot pipeline;
- a compiled and registered experimental Weasel TSF profile;
- an activated automatic Microsoft Pinyin fallback.
