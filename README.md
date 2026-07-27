# Neural Weasel

Neural Weasel is an experimental Windows bilingual IME whose candidate probability
comes from a local **Qwen Base** causal language model. Pinyin and Latin prefixes are
hard legality constraints; the model never receives a chat prompt and the per-key path
never runs a model forward.

The repository currently contains the independently testable core:

- strict RTX 4060 Laptop GPU launcher and runtime guard;
- Hugging Face text-only `Qwen3_5ForCausalLM` backend;
- token-to-pinyin index with heteronym support;
- continuous full-pinyin prefix matching and single-character coverage;
- replaceable full-logits and sparse lm-head projection backends;
- immutable context snapshots and non-blocking epoch-consistent queries;
- one unified Chinese/English candidate type, script policy, ranking, and protocol;
- English-context Han hard exclusion and Chinese-context Latin allowance;
- literal-safe English `Space`, explicit-completion `Tab`, and `Escape` semantics;
- bounded multi-token constrained-beam core for background expansion;
- length-prefixed JSON protocol and Windows named-pipe service;
- CLI commands for index building, prediction, serving, backend comparison, and replay.

The repository also contains a source-level Weasel/librime/TSF integration boundary.
It is **not yet a buildable independent Weasel profile**. The development install
scripts fail closed unless an experimental TSF DLL and profile tool are supplied; this
branch does not produce those binaries. Do not treat it as installable or production
ready.

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

## Run the v0.2 model service

The full-logits backend is the correctness baseline:

```powershell
.\scripts\start-model-service.ps1 -Backend full
```

The sparse backend avoids the full-vocabulary projection and CPU logits copy:

```powershell
.\scripts\start-model-service.ps1 -Backend sparse
```

These commands start the Named Pipe model service. They do not install a Windows input
method profile.

## Measure on the target GPU

Compare both backends on identical legal Latin token sets:

```powershell
uv run neural-weasel benchmark-backends `
  --before "The receiver-centred placement is operationally" `
  --allowed-counts 32 128 512
```

Run the checked-in bilingual replay:

```powershell
uv run neural-weasel replay `
  --fixture benchmarks/replay_v02.jsonl `
  --backend full
```

The output includes candidate quality, wrong-script count, snapshot age, query
percentiles, refresh percentiles, and stale-snapshot errors. Snapshot refresh above
100 ms is reported but is not a failure by itself.

## Windows profile status

There is currently no safe end-user installation sequence. In particular:

- `scripts/install-dev-profile.ps1` requires
  `NeuralWeaselExperimentalTSF.dll` and `NeuralWeaselProfileTool.exe`;
- this branch does not build either artifact;
- registering the official Weasel DLL under the experimental GUID would still connect
  to the official WeaselServer and could affect the existing installation;
- a safe deliverable requires a consistently renamed/forked TSF DLL, server, IPC
  endpoint, RimeWithWeasel build, and static neural module.

The install/uninstall scripts and GUID manifest are safety-tested preparation, not an
installable release.

The branch CI does compile the repository-owned static librime translator/key
processor and native state-machine tests with MSVC against librime `1.15.0`. This is
build evidence for the integration boundary, not evidence that an independent TSF
profile can be installed.

## Scope of the tested core

Supported:

- toneless continuous full pinyin;
- apostrophe separators;
- multiple token pronunciation paths;
- incomplete trailing syllables;
- deletion/retyping (query is stateless in raw keys);
- direct model-token candidates and last-resort single-character coverage.
- one-token Latin completions discovered directly from the Base tokenizer;
- a shared bounded multi-token candidate representation and tested score normalization;
- hard Han exclusion in decisive English context;
- modest, overridable Latin penalty in Chinese context;
- old immutable snapshots during background refresh.

Not yet supported:

- double pinyin, abbreviation, fuzzy pinyin, tones, or typo correction;
- real conditional Base-model scoring for cross-token English completions in the live
  service (the current live tokenizer catalog is one-token);
- a compiled, registered, and smoke-tested independent Weasel TSF profile;
- an activated automatic Microsoft Pinyin fallback.
