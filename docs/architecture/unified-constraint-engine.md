# Unified constraint engine

## Purpose

The Base causal model estimates continuation probability. It does not decide
whether current keys represent pinyin or an English prefix, and it does not get
a chat prompt asking it to translate. One constraint engine owns legality,
script policy, score composition, de-duplication, and publication for both
languages.

```text
immutable BackendState
        |
raw keys + context
        |
UnifiedConstraintEngine
  - PinyinConstraint
  - LatinPrefixConstraint
  - ContextScriptPolicy
        |
shared Candidate ranking/publication
```

## Boundaries

### Model service

The service owns:

- model loading;
- context tokenization and cache reuse;
- all model forwards;
- immutable backend-state publication;
- full-logits indexing or sparse lm-head projection;
- backend timing and memory diagnostics.

### Constraint engine

The constraint engine owns:

- context script classification;
- legal pinyin/token paths;
- legal Latin-prefix token sequences;
- sequence scoring and length normalization;
- script rejection/priors;
- literal fallback;
- shared de-duplication and top-k.

It receives an immutable `BackendState`; it does not request a new state.

### Native key path

The TSF/Rime boundary owns:

- raw composition keys;
- Chinese versus English acceptance-key semantics;
- a short, absolute Named Pipe deadline;
- response session/revision/epoch validation;
- safe cancellation and literal commit;
- publication into the existing candidate UI.

It does not load a model, run a forward, wait for a context refresh, or mutate a
snapshot.

## Data model

```python
Candidate(
    text,
    consumed_keys,
    constraint_kind,
    script,
    model_score,
    constraint_cost,
    language_prior,
    total_score,
    context_epoch,
    token_path,
)
```

`token_path` is a tuple even for one-token candidates. `model_score=None`
distinguishes a literal/coverage fallback from a model-scored candidate. A
fallback must not pretend to have a probability.

Backend state is a tagged immutable value:

```text
FullLogitsState(epoch, CPU logits, hashes, timestamps)
SparseProjectionState(epoch, hidden continuation state, hashes, timestamps)
```

Both implement allowed-token scoring. A sparse state may retain a GPU tensor,
but it is never mutated after publication.

## Query flow

1. Capture the currently published backend state reference.
2. Classify bounded context with `ContextScriptPolicy`.
3. Ask both constraints whether the current raw keys are structurally
   compatible.
4. Each compatible constraint enumerates bounded legal token ids or token
   sequences.
5. Score only those legal paths through the backend state.
6. Convert paths into unified candidates.
7. Apply hard script rejection.
8. Add language prior and constraint cost to the normalized model score.
9. De-duplicate and sort once.
10. Publish candidates carrying the captured state epoch.

Steps 1-10 use one epoch. A newer snapshot published during the query can serve
the next key; it never changes the current result.

## Pinyin path

The existing pinyin index remains the legality source. It produces legal direct
token candidates and bounded multi-token/coverage candidates. The adapter
converts v0.1 pinyin metadata into the unified candidate fields before shared
ranking.

The pinyin adapter does not own a language mode. In an English context its Han
candidates are produced internally and then hard rejected by the policy. This
keeps policy observable and prevents two ranking implementations.

## Latin-prefix path

Latin search operates on Base tokenizer pieces, not a chat response or a
separate dictionary ranker.

The typed prefix is a hard surface constraint. Search state contains:

```text
token_path
decoded_surface
sum_log_probability
is_terminal
```

The first token may include leading whitespace introduced by the tokenizer; it
is removed only when it represents the already-present word boundary. Internal
spaces terminate a word. Hyphen and apostrophe are legal internal characters.
Han output and control characters invalidate the path.

At every depth, search requests scores only for a bounded allowed-token set.
Full logits index the CPU vector; sparse projection selects corresponding
`lm_head` rows. The same search therefore compares backend correctness without
duplicating constraint logic.

The literal prefix is injected after model path enumeration and before shared
ranking. It carries `constraint_kind=literal`, `model_score=None`, and its own
token path is empty.

## Script policy

The policy returns:

```text
ContextDecision(
    kind=chinese | english | ambiguous,
    stable_prior=chinese | english | none,
    hard_forbidden_scripts,
    prior_for(script, raw_keys, model_margin),
)
```

Hard rejection is deliberately asymmetric:

- English context rejects Han leakage.
- Chinese context permits Latin with a modest penalty.
- Ambiguous context permits both.

The stable state changes only after a commit or a decisive context update. A
candidate list alone cannot flip it repeatedly from key to key.

## English key state

AI completion is an offer layered over a literal composition:

```text
idle
  -> type Latin -> composing(literal, optional completion)
  -> Space      -> commit literal + " "
  -> Tab        -> commit selected completion
  -> Escape     -> dismiss completion, keep literal
  -> Backspace  -> recompute from current literal
```

The literal text is the source of truth. Selection state never overwrites it
until an explicit completion accept action.

## Epoch and concurrency

The context worker assigns requested epochs monotonically. A completed model
state is publishable only if its request epoch is still newest. Publication is
an atomic reference replacement. Older immutable states may remain in a small
bounded history for in-flight requests.

Query diagnostics record:

- requested and used epoch;
- snapshot age at query start;
- candidate query duration;
- backend kind;
- stale-by epoch distance where known.

Staleness is measured, not treated as failure without replay evidence.

## Failure handling

- No state: literal fallback only.
- Full backend scoring failure: discard model candidates for that query.
- Sparse projection unsupported or fails during setup: choose the full backend
  explicitly and record the reason.
- Pipe timeout/disconnect: native boundary returns no AI translation.
- Epoch mismatch: discard the entire response.
- Protected context: invalidate model private state and clear candidate history.

No watchdog, auto-restart tree, or automatic Microsoft Pinyin activation is
introduced in v0.2.

## Test seams

Tests use:

- deterministic fake tokenizer/token paths;
- fake full logits and lm-head tensors;
- a spy backend whose `update_context` fails if called by query;
- controllable epoch publication barriers;
- a pure key-state reducer;
- pure install/uninstall plan validation;
- replay clocks that measure real execution duration while using deterministic
  fixture model scores.

The real Qwen benchmark is separate and opt-in because it requires the specified
Windows RTX 4060 Laptop GPU and downloaded weights.
