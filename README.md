# Neural Weasel

Neural Weasel is an experimental Windows bilingual IME whose candidate probability
comes from a local **Qwen Base** causal language model. Pinyin and Latin prefixes are
hard legality constraints; the model never receives a chat prompt and the per-key path
never runs a model forward.

## Authoritative runtime and installation baseline

**Q8 is allowed.** There is no current project rule banning Q8 or restricting future
work to Q4/Q6. Q4, Q6, and Q8 are runtime/quantization choices; editor-context,
candidate-generation, ranking, and privacy contracts are quantization-independent.
The production artifact currently pinned by `main` is
`Qwen3.5-4B-Base.Q8_0.gguf`.

For real editor surrounding context, the preferred route is the repository's Neural
experimental TSF pipeline (`native/tsf/*` plus `native/context/*`). An official Weasel
shell that only remembers text committed by the IME is not equivalent to TSF
surrounding-text capture. Do not fall back to an external UIA/file bridge merely
because an older discussion said Q8 was disabled; that constraint is obsolete.

The immediate target-machine priority is to install and smoke-test the Neural
experimental profile. Engram, expanded English prediction, fuzzy pinyin, and typo
correction are deferred until the profile installs and types correctly on the target
Windows machine. See `AGENTS.md` for the repository-level agent baseline.

The repository currently contains the independently testable core:

- strict RTX 4060 Laptop GPU launcher and runtime guard;
- production Qwen3.5-4B Base GGUF/CUDA runtime plus independently testable model backends;
- token-to-pinyin index with heteronym support;
- continuous full-pinyin prefix matching and single-character coverage;
- replaceable full-logits and sparse lm-head projection backends;
- immutable context snapshots and non-blocking epoch-consistent queries;
- one unified Chinese/English candidate type, script policy, ranking, and protocol;
- English-context Han hard exclusion and Chinese-context Latin allowance;
- literal-safe English `Space`, explicit-completion `Tab`, and `Escape` semantics;
- bounded multi-token constrained-beam core for background expansion;
- length-prefixed JSON protocol and Windows named-pipe service;
- bounded, authenticated Neural TSF surrounding-context capture;
- CLI commands for index building, prediction, serving, backend comparison, and replay.

Windows CI builds the isolated Neural experimental Weasel/librime/TSF profile and
server bundle. The bundle is buildable and installable by the checked-in development
scripts; what remains unproven until target-machine work is real Windows registration,
`Win+Space` visibility, editor compatibility, latency, secure-field behavior, restart,
and complete removal. Do not confuse that required local smoke test with an inability
to build the profile.

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
- Password/PIN/protected fields must never send surrounding plaintext.
- Raw editor context must not be persisted into Engram, logs, caches, telemetry, or
  crash artifacts.

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

These commands start the separate Named Pipe model service. Installation is
performed only from a verified CI bundle with `install-dev-profile.ps1`.

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

Windows CI applies the repository isolation overlay to pinned Weasel `0.17.4`
and uploads `neural-weasel-experimental-x64`. The bundle contains the real
experimental TSF DLL, independent server, profile tool, static neural module
evidence, runtime data, hash manifest, and safety scripts. Installation accepts
only the reserved experimental identities and never sets the default input
method.

This is an experimental test bundle, not a production release. Global TSF
registration, `Win+Space` visibility, real editor typing, secure-field
behavior, server restart, and complete removal still require the
[manual Windows smoke test](docs/manual/windows-install-smoke-test.md).

## Scope of the tested core

Supported:

- toneless continuous full pinyin;
- apostrophe separators;
- multiple token pronunciation paths;
- incomplete trailing syllables;
- deletion/retyping (query is stateless in raw keys);
- direct model-token candidates and last-resort single-character coverage;
- one-token Latin completions discovered directly from the Base tokenizer;
- a shared bounded multi-token candidate representation and tested score normalization;
- hard Han exclusion in decisive English context;
- modest, overridable Latin penalty in Chinese context;
- old immutable snapshots during background refresh;
- real bounded TSF surrounding-context capture in the Neural experimental profile.

Not yet supported or not yet target-machine-validated:

- double pinyin, abbreviation, fuzzy pinyin, tones, or typo correction;
- real conditional Base-model scoring for cross-token English completions in the live
  service (the current live tokenizer catalog is one-token);
- completed target-machine registration and smoke validation of the independent Neural
  experimental TSF profile;
- an activated automatic Microsoft Pinyin fallback.
