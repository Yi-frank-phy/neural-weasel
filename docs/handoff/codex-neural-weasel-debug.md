# Handoff: Neural Weasel target-machine candidate and latency RED

Updated: 2026-08-31 (Asia/Shanghai)

## Current user-visible state

The experimental Neural TSF is registered and can show and commit Chinese candidates, but it is **not accepted as healthy**. The latest target-machine report is:

> 卡，候选诡异，很晚出，甚至没有词组就不出，长了也不出，而且无法数字选择

Backend protocol checks are green, but they do not prove the live TSF UI. Keep generation, display, selection, commit, and latency as separate acceptance dimensions.

## Branch scope ready for cloud review

Branch: `codex/combine-candidate-ui`, based on `origin/agent/q4-runtime-selector` (`31aa7f6`).

The change set at this handoff contains:

- opaque candidate-window colors without changing geometry/font defaults;
- removal of the internal `[coverage]` marker from visible comments;
- hard ranking tiers: exact pinyin before extension, fuzzy, and Latin; model scores rank only within a tier;
- English-context Latin candidates before Han candidates;
- Chinese number keys passed to Rime's selector, while English number keys remain literal input;
- regression tests for the above behavior.

Validation already completed against this exact source set:

- Python: `322 passed` (targeted candidate/UI subset: `37 passed`);
- native CTest: `9/9 passed`;
- `git diff --check`: passed;
- real Weasel/xmake build relinked both changed native sources;
- built bundle verification and installer dry-run: passed.

Installed binary hashes from that build:

- `NeuralWeaselExperimentalTSF.dll`: `9a809ebcd5a8ce54e9b70df14e7acb98658e70b055234a786f83cb7157bfef12`
- `NeuralWeaselServer.exe`: `0281a17818472242cee388bb8be3def3a91395de973e61ce46b45d158d75a498`

## Confirmed RED 1: installer leaves the old managed schema active

The source fix orders processors as:

```yaml
- bilingual_key_processor
- selector
- speller
- punctuator
```

The installed bundle contained that schema, but `scripts/install-dev-profile.ps1:267-272` only copies files from `rime-user` when the destination does not exist. The target therefore kept its 2026-08-04 schema with the old order:

```yaml
- speller
- bilingual_key_processor
- punctuator
- selector
```

Because `speller.alphabet` includes digits, the old runtime order consumes number keys before `selector`. This is the demonstrated cause of the number-selection failure.

Target-machine evidence:

- old live schema hash: `6EFE754AE7678D56DC4D313F13622D002BEF52BCAE46A0550D8F445BAD8DF28D`;
- bundle/new schema hash: `356F55673F236B4A5A832BCDAE9DCBF589FECA0B2C42C46961E151E3FFBE11D1`;
- even after copying the source schema, `RimeUser/build/neural_weasel.schema.yaml` remained old until it was patched explicitly.

The live source and generated schema were migrated manually and the experimental server was restarted. This still needs a user manual number-key retest.

### Cloud-safe next work

Add a regression and change installation/deployment so product-owned `neural_weasel.schema.yaml` is upgraded atomically. Preserve user-owned `*.custom.yaml` and unrelated configuration. Do not broadly overwrite `weasel.yaml` or the entire `RimeUser` directory.

## Confirmed RED 2: context refresh can starve the 50 ms query path

Metadata-only `ai-translator.log` evidence from the real TSF session:

- one focus/context transition left input lengths 1/2/3 at `identity-valid=0 model-epoch=0`; the first usable epoch arrived about `4359 ms` later;
- at epoch 33, the first three inputs returned `pipe-failure status=2 error=121` around the native 50 ms deadline; epoch 34 returned candidates about `5.6 s` later;
- epoch 38 repeated the three 50 ms failures; epoch 39 then returned candidates;
- successful queries are usually 0-47 ms, so the failure is correlated with model refresh rather than permanent pipe loss.

Relevant defaults:

- `native/rime/ai_translator.h`: `query_timeout_{50}`;
- `LlamaCppBackend`: `max_before_tokens=3072`, `n_ctx=4096`, `n_batch=512`;
- the Q4 launcher currently does not expose overrides for those values;
- `create_snapshot()` performs `llama.eval()` while holding the backend lock;
- there is no ordinary pinyin dictionary fallback in the deployed schema, so epoch 0 cannot produce Chinese candidates.

A synthetic, non-private benchmark against the running Q4 service measured refreshes of approximately 608 ms (64 synthetic words), 785 ms (256), 1192 ms (512), and 1219 ms (1024). A separate probe observed an old-snapshot query taking 788 ms during refresh. The Python client's timeout limits connection acquisition, not the response read, so use these as starvation evidence, not as a native deadline pass.

### Cloud-safe next work

- Add latency/refresh diagnostics that record durations and token counts only, never raw editor context.
- Make the production context-window/runtime parameters explicit and testable instead of silently relying on 3072/4096/512.
- Reproduce query starvation with a controlled blocking backend and prove that immutable previous snapshots remain queryable while a new snapshot is computed.
- Evaluate a bounded target profile (for example a smaller retained left context) with tests, but do not claim a parameter value is fixed until target-hardware measurement.
- Keep all model work outside the TSF DLL.

Do **not** paper over this by increasing the native 50 ms timeout, reusing an epoch from another focus/session, relaxing identity checks, or moving model work into the TSF process.

## Work that requires this Windows target machine

These cannot be completed honestly by cloud CI alone:

1. Confirm number keys 1-9 select the visible candidate after the live schema migration; confirm English digits remain literal.
2. Type short and long pinyin in a real editor and correlate each visible result with metadata-only epochs and query timings.
3. Rebuild the pinned Weasel/librime overlay, create the Windows bundle, perform the UAC install, and verify installed hashes.
4. Verify registration, `Win+Space`, default-input-method preservation, normal editor typing, real surrounding context, password/PIN zero capture, server-failure behavior, latency, uninstall, and recovery.
5. Repeat on the target GPU with Q4/Q8 choices treated only as runtime choices; functional ranking and security contracts remain quantization-independent.

## Target-machine recovery state

- Isolated worktree: `C:\Users\zhaoy\Downloads\neural-weasel-target-machine\combined-candidate-ui`
- Runtime: `C:\Users\zhaoy\AppData\Local\NeuralWeasel\Experimental\experimental-profile`
- Q4 GGUF: `C:\Users\zhaoy\AppData\Local\NeuralWeasel\gguf-poc\models\Qwen3.5-4B-Q4_K_M.gguf`
- Current bundle: `C:\Users\zhaoy\Downloads\neural-weasel-target-machine\combined-candidate-ui\dist\candidate-ranking-fix-20260831-1945`
- Schema rollback directory: `C:\Users\zhaoy\Downloads\neural-weasel-target-machine\backups\rime-schema-before-ranking-fix-20260831-2230`
- Pinned Weasel tree: `C:\Users\zhaoy\Downloads\neural-weasel-build-deps\weasel-combined` at `9cc96e20dc71b80876b12f689bb5863c76c2a7ed`
- Pinned librime: `1c23358157934bd6e6d6981f0c0164f05393b497`

The default input method was not changed. The experimental model and server processes may simply disappear at machine shutdown; no remote state depends on them.

## Privacy/security invariants

- Password/PIN/protected fields: zero surrounding-text capture.
- PRIVATE context may be used ephemerally but must not persist.
- Raw editor context must not enter logs, databases, telemetry, caches, crash artifacts, or Engram.
- Context transport remains bounded, one-way, nonblocking, identity-checked, and latest-revision-wins.
- Stale focus/session/revision state must never publish candidates into a newer editor context.
- Do not upload target logs without first confirming they contain metadata only.
