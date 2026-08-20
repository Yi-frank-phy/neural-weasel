# Qwen3.5-4B-Base Q8_0 GGUF CUDA Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the production Transformers/bitsandbytes runtime with a pinned Qwen3.5-4B-Base Q8_0 GGUF runtime that fails closed unless llama.cpp executes all model layers through CUDA on the isolated RTX 4060.

**Architecture:** Introduce a focused llama.cpp adapter that exposes the tokenizer/vocabulary and immutable next-token logits expected by the existing ranking layer. Keep keypress scoring snapshot-only; all model forwards remain background context work. Bind the SQLite pinyin index to the GGUF vocabulary fingerprint and make launch/health checks enforce artifact, quantization, and CUDA identity.

**Tech Stack:** Python 3.12, llama-cpp-python/llama.cpp, CUDA, GGUF, SQLite, PowerShell, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-20-qwen35-4b-q8-gguf-cuda-design.md`

## Global Constraints

- Production checkpoint is exactly `Qwen/Qwen3.5-4B-Base`.
- Production quantization is exactly `Q8_0` GGUF.
- Model-layer CPU fallback is forbidden; CUDA full-layer offload is required.
- Target device is exactly `NVIDIA GeForce RTX 4060 Laptop GPU`.
- Pinyin token IDs must come from the GGUF/llama.cpp vocabulary, never a separate HF tokenizer.
- Keypress path never runs a model forward.
- The model artifact is downloaded to a per-user cache and verified by SHA-256; it is never committed to GitHub.

---

### Task 1: Freeze the production runtime contract

**Files:**
- Modify: `tests/test_health_contract.py`
- Modify: `tests/test_one_click_launcher.py`
- Modify: `tests/test_wisdom_integration.py`
- Create: `tests/test_gguf_runtime_contract.py`

**Interfaces:**
- Produces canonical constants for model id, format, quantization, runtime and CUDA backend expectations.
- Produces failing tests that reject `0.8B`, `int8`, `nf4`, CPU fallback, and partial GPU offload.

- [ ] Replace current 0.8B/int8 expectations with `Qwen/Qwen3.5-4B-Base`, `gguf`, `Q8_0`, `llama.cpp`, `CUDA`, `gpu_layers=all`.
- [ ] Add a Torch-free fake-runtime test that proves service startup rejects a runtime/index vocabulary mismatch.
- [ ] Add launcher text-contract assertions that require the 4B model and GGUF artifact and forbid `--precision int8`/`nf4` production flags.
- [ ] Run the focused tests and record the intended RED failures in PR #14.

### Task 2: Add pinned GGUF artifact identity and acquisition

**Files:**
- Create: `src/neural_weasel/gguf_artifact.py`
- Create: `src/neural_weasel/acquire_model.py`
- Modify: `src/neural_weasel/paths.py`
- Test: `tests/test_gguf_artifact.py`

**Interfaces:**
- Produces `ProductionGgufArtifact` with `model_id`, `repo_id`, `filename`, `revision`, `sha256`, `quantization`.
- Produces `ensure_production_gguf() -> Path` that downloads only the pinned file and verifies SHA-256 before publication.

- [ ] Add tests for deterministic cache path, SHA-256 acceptance, wrong-hash rejection, and atomic publication.
- [ ] Implement immutable artifact constants for the chosen Base Q8_0 file.
- [ ] Implement download through `huggingface_hub.hf_hub_download` into the per-user model cache and verify the final file hash.
- [ ] Run tests and commit.

### Task 3: Introduce llama.cpp vocabulary adapter

**Files:**
- Create: `src/neural_weasel/llama_vocab.py`
- Modify: `src/neural_weasel/index.py`
- Modify: `src/neural_weasel/resolve_index.py`
- Test: `tests/test_llama_vocab.py`
- Test: `tests/test_index_identity.py`

**Interfaces:**
- Produces `LlamaVocabAdapter` implementing `__len__`, `decode`, `encode`, `get_vocab`, and `all_special_ids` for existing index/ranking code.
- Produces `vocab_fingerprint` derived from GGUF token ids and decoded bytes/text.
- Index metadata contains `gguf_sha256` and `vocab_fingerprint` instead of HF revision/tokenizer hash identity.

- [ ] Write a fake llama.cpp vocabulary test covering byte-safe decode and token-id stability.
- [ ] Refactor index builder to consume the adapter without importing Transformers.
- [ ] Bump index schema because persisted identity semantics change.
- [ ] Make canonical index paths include GGUF/vocabulary identity.
- [ ] Run index/coverage/unit tests and commit.

### Task 4: Implement the CUDA-only GGUF runtime adapter

**Files:**
- Create: `src/neural_weasel/llama_runtime.py`
- Modify: `src/neural_weasel/gpu.py`
- Modify: `src/neural_weasel/service_factory.py`
- Modify: `src/neural_weasel/runtime_identity.py`
- Test: `tests/test_llama_runtime.py`
- Test: `tests/test_runtime_identity.py`

**Interfaces:**
- Produces `LlamaCppBackend(model_path: Path, artifact: ProductionGgufArtifact, ...)`.
- Exposes `tokenizer`, `create_snapshot(before, after)`, `full_logits`, `diagnostics`, and private cache invalidation compatible with existing full-logits backend seams.
- Diagnostics include `format`, `quantization`, `runtime`, `backend`, `gpu_layers`, `gguf_sha256`, `vocab_fingerprint`, `gpu_name`, and `gpu_uuid`.

- [ ] Mock `llama_cpp.Llama` and assert construction uses `n_gpu_layers=-1`, one isolated GPU and logits access.
- [ ] Add fail-closed checks for missing CUDA backend evidence, wrong GPU identity and partial layer offload evidence.
- [ ] Implement context evaluation with `reset()/eval()` and immutable last-token full-vocabulary score copy; preserve the existing async snapshot publication boundary.
- [ ] Implement tokenizer adapter over the same loaded llama.cpp model.
- [ ] Validate runtime/index identity before serving.
- [ ] Run focused runtime/backend tests and commit.

### Task 5: Switch CLI and launchers to the GGUF runtime

**Files:**
- Modify: `src/neural_weasel/internal_cli.py`
- Modify: `src/neural_weasel/launcher.py`
- Modify: `scripts/start-model-service.ps1`
- Modify: `scripts/start-neural-weasel-integration.ps1`
- Modify: `scripts/install-wisdom-integration.ps1`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_one_click_launcher.py`
- Test: `tests/test_wisdom_integration.py`

**Interfaces:**
- `neural-weasel acquire-model` downloads/verifies the pinned GGUF.
- `neural-weasel gpu-info` reports llama.cpp CUDA readiness in addition to physical GPU identity.
- `serve`, `serve-http`, `predict`, `simulate`, `benchmark`, `replay` all use the GGUF runtime and no longer expose production precision selection.

- [ ] Change defaults/contracts to 4B Base Q8_0 GGUF.
- [ ] Remove production bitsandbytes precision flags and Torch-model construction from service commands.
- [ ] Add explicit CUDA-enabled llama-cpp-python installation/wiring for Windows; CPU-only installation must fail the runtime smoke gate.
- [ ] Make startup acquire/verify the GGUF before building its vocabulary-bound index.
- [ ] Update Wisdom health acceptance to exact GGUF/CUDA identity.
- [ ] Run launcher and PowerShell 5.1 tests and commit.

### Task 6: Add target-machine CUDA smoke/benchmark command

**Files:**
- Create: `src/neural_weasel/gguf_smoke.py`
- Modify: `src/neural_weasel/internal_cli.py`
- Test: `tests/test_gguf_smoke.py`

**Interfaces:**
- Produces `neural-weasel gguf-smoke` returning non-zero unless artifact hash, CUDA backend, expected GPU, full layer offload, smoke forward and minimum VRAM headroom all pass.
- Emits machine-readable JSON suitable for attaching to GitHub issues.

- [ ] Add mocked RED/GREEN tests for every fail-closed gate.
- [ ] Record before/after `nvidia-smi` VRAM, model load diagnostics, smoke-forward latency and remaining headroom.
- [ ] Require exact Q8_0/artifact identity.
- [ ] Run tests and commit.

### Task 7: Remove stale production assumptions and verify delivery

**Files:**
- Modify: `README.md`
- Modify: `docs/STATUS.md`
- Modify: relevant launcher/bundle tests

**Interfaces:**
- Documentation states the one supported production runtime and distinguishes target-machine CUDA proof from generic CI.

- [ ] Remove statements implying the supported runtime is 0.8B/int8 or 4B/NF4.
- [ ] Document model acquisition and `gguf-smoke`.
- [ ] Run `ruff check .`, `ruff format --check .`, full `pytest`, one-click workflow, and Windows bundle workflow.
- [ ] Review the complete PR diff against the spec.
- [ ] Post final verification evidence to PR #14 and relevant issues; do not merge without user instruction.
