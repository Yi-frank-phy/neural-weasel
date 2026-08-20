# Qwen3.5-4B-Base Q8_0 GGUF CUDA Runtime Design

## Goal

Neural Weasel's supported production model runtime is exactly **Qwen/Qwen3.5-4B-Base**, quantized as **Q8_0 GGUF**, executed through a **CUDA-enabled llama.cpp runtime** on the user's NVIDIA GeForce RTX 4060 Laptop GPU.

The runtime must fail closed if it cannot prove that the expected GGUF identity is loaded or if model layers are not fully offloaded to CUDA. CPU-only inference and model-layer CPU fallback are unsupported.

## Model artifact contract

- Upstream checkpoint identity: `Qwen/Qwen3.5-4B-Base`.
- GGUF source: `mradermacher/Qwen3.5-4B-Base-GGUF`.
- Quantization: `Q8_0`.
- Model file is pinned by exact filename, Hugging Face revision, and SHA-256 before it is accepted.
- `mmproj` is not loaded; the IME runtime is text-only.
- The service never silently substitutes another model, quantization, or checkpoint revision.

## Runtime architecture

Use `llama-cpp-python` in-process rather than an HTTP `llama-server` process. Neural Weasel needs direct access to complete next-token score vectors and runtime state; a separate generation-oriented HTTP API would require a custom protocol and add another realtime boundary.

A dedicated GGUF runtime adapter owns llama.cpp model/context state. Existing pinyin legality, ranking, immutable snapshot publication, and service integration remain outside the runtime adapter.

The keypress path must never own a model forward. Context refresh and any multi-token continuation remain background GPU work; keypress handling reads immutable published scores only.

## CUDA-only contract

The launcher continues to isolate the single physical `NVIDIA GeForce RTX 4060 Laptop GPU` with `CUDA_VISIBLE_DEVICES` and records its UUID.

The llama.cpp runtime is constructed with full GPU offload (`n_gpu_layers=-1`) and no intentional model-layer CPU offload. Startup is accepted only if:

1. a CUDA backend is available to llama.cpp;
2. the isolated device matches the expected RTX 4060 identity;
3. the model reports all model layers assigned to GPU rather than a partial offload;
4. CUDA-visible VRAM consumption increases consistently with loading the 4B Q8_0 artifact;
5. a real smoke forward succeeds.

If any check cannot be proved, startup fails. Host-side tokenization, orchestration, SQLite/index lookup, and candidate search remain CPU work and are not considered model fallback.

## Vocabulary and pinyin index identity

The pinyin index must use the **GGUF/llama.cpp vocabulary token IDs**, not a separately loaded Hugging Face tokenizer. This prevents a silent mismatch between HF token IDs and the token IDs whose logits are produced by llama.cpp.

The index identity is bound to:

- GGUF SHA-256;
- a stable fingerprint of the GGUF vocabulary/tokenizer metadata;
- pypinyin version;
- pinyin index schema version.

Service startup compares runtime identity and index identity and fails closed on any mismatch.

## Diagnostics contract

`/health` exposes canonical facts rather than aliases:

- `model = Qwen/Qwen3.5-4B-Base`
- `format = gguf`
- `quantization = Q8_0`
- `runtime = llama.cpp`
- `backend = CUDA`
- `gpu_name = NVIDIA GeForce RTX 4060 Laptop GPU`
- `gpu_uuid`
- `gpu_layers = all`
- `gguf_sha256`
- `vocab_fingerprint`
- `index_vocab_fingerprint`
- `index_schema_version`

Wisdom/one-click startup accepts an already-running backend only when this identity matches exactly.

## Packaging

The Windows runtime must install or build a CUDA-enabled `llama-cpp-python` distribution. A CPU-only wheel is not an acceptable fallback. The dependency/build path must be explicit and testable from the launcher bundle.

The 4.6 GB model is not committed to GitHub or bundled into the repository artifact. A model acquisition step downloads the pinned GGUF into the per-user Neural Weasel model cache and verifies its SHA-256 before use.

## Verification

Repository CI can verify contracts, index identity logic, launcher wiring, PowerShell parsing, and the Windows bundle. Generic GitHub runners cannot prove RTX 4060 CUDA execution.

A local command must therefore perform the final runtime gate on the target machine: load the pinned GGUF, prove CUDA/full-layer offload, execute a smoke forward, report VRAM/headroom, and return non-zero on any fallback or identity mismatch.

## Non-goals

- No post-trained/chat `Qwen3.5-4B` checkpoint.
- No NF4 or bitsandbytes production runtime.
- No CPU inference fallback.
- No partial model-layer offload.
- No migration of keypress-time scoring back onto the GPU critical path.
