# Experimental context-conditioned candidate scoring

GitHub issue #39 distinguishes candidate scoring from free-form completion.
Phase 1 adds an explicit `--context-mode fim` option to predict, serve,
serve-http, simulate and benchmark. The release default remains `continuation`.
The launcher already forwards these arguments. No installation or service
restart is required to review the implementation.

The background runtime serializes:

`[optional BOS] <|fim_prefix|> before <|fim_suffix|> after <|fim_middle|>`

Only the three trusted markers use special-token parsing. Both editor sides
use literal tokenization. Startup requires distinct native FIM IDs, matching
native getters, CONTROL attributes and exact byte round trips; unsupported
models fail explicitly. Native vocabulary policy determines whether BOS is
inserted once. No artificial EOS is inserted into an empty FIM prefix.

The window budget includes BOS, three markers and at least 16 candidate
tokens. The left side retains its tail and the right side retains its head,
with a configurable right-side limit of 4096 tokens. A nonempty left side can
retain half the available text budget before the right side uses the rest.
Unused left capacity is available to the right side. The assembled control
sequence is never truncated.

The full prompt tuple identifies the snapshot cache and continuation root.
Suffix-only changes can therefore change logits. Native branch replay includes
both editor sides and all markers. Existing session/revision checks, private
invalidation, immutable keypress snapshots, constrained candidate generation
and pagination remain authoritative. The TSF DLL does not run a model or wait
for a backend. Diagnostics expose only fixed enums, counts and booleans.

## Verification and scope

The Python regressions cover marker rejection, literal marker text, BOS,
budget boundaries, trimming, suffix refresh, complete native branch replay,
cache restoration, private clearing, zero-eval keypress queries, CLI opt-in
and diagnostic filtering. The target Q4 vocabulary-only probe reports
248320 entries, FIM IDs 248060/248062/248061, CONTROL attributes 8/8/8 and no
required BOS. This proves tokenizer support, not useful model behavior.

`scripts/benchmark-fim-context.py` provides a CPU-only native A/B over six
public synthetic fixtures. Chinese alternatives share exact pinyin; Latin
alternatives satisfy their typed prefix. It compares full-vocabulary joint
log probabilities with and without the suffix. It is not a production search,
TSF display/commit, GPU latency or representative quality benchmark.

For an isolated high-RAM run, use:

```powershell
colab-job highram scripts/benchmark-fim-colab.py --source-commit <full pushed commit>
```

This
fetches that immutable source commit and the pinned production Q8 artifact,
uses llama-cpp-python 0.3.23 and prints a JSON result with model digest, runtime
version and per-case rankings. The wrapper must release the ephemeral runtime.
Only synthetic inputs are uploaded; the benchmark never reads editor text.

Do not switch the production default based on marker support or six fixtures.
Representative constrained-candidate A/B and real Windows editor smoke tests
remain required. Generic out-of-process UIA high-context capture is a later
phase and is not implemented by this change.

## Native Q8 pilot result (2026-10-02)

The ephemeral Colab CPU run used source
`e88f2326d191dfe3a272646a2d0530e850af99e0`, the pinned production Q8 Hub
revision, llama-cpp-python 0.3.23, 256 context tokens and two CPU threads.
The downloaded GGUF SHA-256 was
`2a5266475777daf23e21991592a006f6dbc0be4620ac6a12d7d57240b0027711`.
The runtime was released after result retrieval.

| Public fixture | Continuation top candidate | FIM top candidate | Expected |
| --- | --- | --- | --- |
| Policy, `shishi` | 实施 | 实施 | 实施 |
| Facts, same left text and `shishi` | 实施 | 事实 | 事实 |
| Archive, `comp` | compare | compress | compress |
| Executable, same left text and `comp` | compare | compile | compile |
| List method, `app` | apply | append | append |
| String method, `rep` | replace | replace | replace |

Top-1 matches were 2/6 for continuation and 6/6 for FIM. The paired fixtures
demonstrate a suffix-conditioned ranking change, not population accuracy.
The 10.847 second run duration includes CPU model initialization and multiple
isolated prompts; it is not per-keypress latency. All candidate paths in this
pilot tokenized to one token, so native multi-token behavior is not established
by this pilot; root replay regressions cover multi-token paths using fakes.

Full probabilities and provenance are in
`docs/experiments/context-fim-ab-20261002.json`. Windows CLI transport damaged
Chinese labels by decoding GBK bytes as UTF-8. Those fixed public fixture labels
were restored only after exact matching against that transformation; numerical
results and correctness flags were preserved and checked against the rankings.
The drivers now emit ASCII JSON to avoid this transport issue. The earlier
run completed without forwarding child stdout and provided no usable rankings.

## Deferred Windows context acquisition

- [ ] Evaluate direct Windows editor-context acquisition (including UIA high-context)
  after the current TSF installation passes target-machine smoke and latency tests.
  This is a separate future task, not part of the 2026-10-02 deployment.
  Preserve zero capture in protected/password fields, ephemeral PRIVATE context,
  no raw-text persistence, bounded nonblocking transport, and stale-focus rejection.
