# Editor Context Security Design

## Goal

Restore useful arbitrary **ordinary editor surrounding context** to Neural Weasel without turning the IME into a new system-wide text archive, query oracle, or unusually attractive local exfiltration point.

The security target is deliberately narrower than “defend Windows against any same-user spyware”. Neural Weasel must instead satisfy this invariant:

> Installing Neural Weasel must not make text materially easier to steal than it already is from the focused editor/browser itself.

The design therefore allows the IME to use bounded plaintext context for local prediction, while refusing protected credential fields, avoiding persistence, avoiding global history, and exposing no API that returns captured raw text.

## Threat model

### In scope

- Accidental plaintext persistence in logs, temp files, caches, SQLite, traces, crash-oriented diagnostics, or telemetry.
- A normal local program looking for an easy centralized source of recently typed text.
- A local program discovering Neural Weasel IPC and attempting to use it as a cross-application text oracle.
- A broken or stalled Neural Weasel backend causing an editor/TSF host to freeze or crash.
- Focus changes, stale revisions, or delayed background inference causing one application’s context to be reused in another application.
- Password/PIN/credential fields that the host/Windows explicitly identifies as protected.

### Out of scope

- Administrator, SYSTEM, kernel/rootkit compromise.
- A determined same-user process that can already read/inject into the focused editor process or capture its screen/keystrokes. Neural Weasel must not make that attacker’s job easier, but it cannot provide a stronger confidentiality boundary than the host application itself.
- General Windows application isolation for unrelated third-party software.
- Persistent Engram memory. Engram is a separate future security domain and must not be implemented by retaining IME context history.

## Design principles

1. **See broadly, remember almost nothing.** Ordinary focused-editor text may be used for prediction; raw context is ephemeral.
2. **Never aggregate history.** Neural Weasel has no system-wide context database or previous-app text store.
3. **No raw-context read API.** IPC accepts current context and returns candidates/status only; no caller can request captured text back.
4. **Protected means hard deny.** Explicit password/PIN/credential scopes never enter the model path.
5. **Current session only.** Context is bound to focus/session identity and monotonically increasing revision; stale or foreign-session state is unusable.
6. **Editor-host code stays minimal.** TSF code performs bounded read, classification, bounded serialization/copy, best-effort nonblocking send, and return. No model, JSON, disk I/O, wait loops, or worker/model lifecycle belongs in the host process.
7. **Backend failure degrades to ordinary IME behavior.** Context loss or backend failure may reduce prediction quality but must not block typing.

## Security labels

The capture path uses three states.

### `NORMAL`

- Bounded surrounding text may be captured and used for local prediction.
- Raw text is not persisted.
- No history API is created.

### `PRIVATE`

Examples include host-provided private/no-learning scopes such as incognito/private-editing signals.

- Bounded surrounding text may still be used for the **current local prediction** so intelligence is not unnecessarily degraded.
- Learning, history, persistence, telemetry, and future automatic Engram ingestion are forbidden.
- In the current no-history architecture, `PRIVATE` mainly preserves a stronger contract for future features.

### `PASSWORD`

Examples include explicit password, PIN, credential, or equivalent protected input scopes.

- No surrounding-text capture is sent to Neural Weasel.
- Existing context for the source session is immediately invalidated.
- The model receives no password-field plaintext.

Unknown/missing InputScope metadata is **not** treated as password by default. The earlier fail-closed-on-missing-metadata behavior made ordinary editors context-blind and is outside this design.

## InputScope classification

The TSF classifier must use the Windows application-property path for input scope:

`ITfContext::GetAppProperty(GUID_PROP_INPUTSCOPE, ...)`

It must not use `ITfContext::GetProperty` for this application property.

Classification must be conservative only for **explicit protected signals**. Missing metadata, unsupported scope providers, or ordinary controls remain eligible for ephemeral prediction.

Additional explicit protected signals may be added when they are reliable and cheap, but semantic “is this research sentence sensitive?” classification is a non-goal.

## Capture boundary

The reusable surrounding-text read logic remains bounded:

- up to 8192 UTF-16 code units before the caret;
- up to 4096 UTF-16 code units after the caret.

Capture occurs only for the currently focused TSF context through a read-only edit session. The capture code must not traverse unrelated documents, processes, workspaces, browser tabs, or filesystem content.

The payload contains only what prediction needs:

- random source-session capability/nonce;
- source process identity for diagnostics and stale-session checks;
- context revision;
- security label;
- bounded `before` text;
- bounded `after` text;
- caret/selection metadata only if required by scoring.

No document path, project scan, clipboard history, browser history, or unrelated application state is added by this feature.

## Transport

Do **not** restore the old `ContextUpdateBridge` architecture inside the TSF DLL unchanged. It placed a worker thread, custom transport lifecycle, serialization, and pipe machinery inside editor-hosted processes and was deliberately removed by crash containment.

Use a dedicated **best-effort, one-way, nonblocking context push** to the out-of-process Neural Weasel service/broker.

Required transport properties:

- no `WaitNamedPipe` loop;
- no `FlushFileBuffers` on the TSF callback path;
- no synchronous request/response carrying raw context;
- bounded payload size;
- at most a small fixed number of in-flight buffers;
- if the backend is absent, busy, or slow, drop/coalesce the update rather than wait;
- the service never offers an operation that returns submitted raw context.

A Windows named pipe with overlapped/best-effort writes is the preferred first implementation because it avoids introducing a globally readable plaintext shared-memory mailbox. The exact low-level transport may change if testing shows the TSF host path can still block, but the nonblocking/drop-on-pressure contract may not change.

## Session and stale-state isolation

Each focused source session owns a cryptographically random capability/nonce and a monotonic revision.

The service accepts a context update only as state for that session. Candidate queries must identify the same active session/revision relationship; a different process/session cannot ask the service to reuse another source session’s context.

On focus change, protected-field transition, or source teardown:

1. invalidate the old source session;
2. publish/queue a clear marker;
3. never deliberately reuse the old plaintext snapshot for the new focus.

The broker is latest-wins. Intermediate context updates may be discarded before GPU work begins. If an obsolete GPU refresh has already started, its result must not be published as the active snapshot after a newer revision exists.

This design therefore also provides the foundation for resolving stale-refresh waste in issue #11.

## Broker/model boundary

The out-of-process broker/model path may temporarily hold the **current** bounded plaintext snapshot and model state required for prediction.

It must not create:

- context-history files;
- raw prompt/request dumps;
- plaintext SQLite rows;
- plaintext debug logs;
- raw-context telemetry;
- a `get_context`, `dump_context`, `list_contexts`, or equivalent API;
- automatic Engram persistence.

Diagnostics may expose metadata such as source PID/application identity, label, revision, capture length, drop reason, latency, and stale/discard counters, but not the captured text itself.

## Candidate behavior

The production GGUF path keeps its existing realtime invariant: **keypress handling never owns a model forward**.

- Background context refresh consumes the latest accepted session snapshot and publishes immutable next-token scores.
- Keypress candidate queries read only published state associated with the active source session/revision policy.
- Missing, dropped, stale, private, or protected context must fail toward reduced-context prediction, not toward synchronous inference on the keypress path.

Right-of-caret context may be retained for future suffix-aware/background rescoring, but this feature must not add synchronous suffix scoring to the 6 ms native path.

## Failure handling

All failure modes are fail-soft for typing:

- InputScope lookup failure on an ordinary control: treat as ordinary/unknown, not password.
- Explicit protected scope: clear/drop context.
- Capture failure: publish no new context.
- Pipe unavailable/busy: drop or coalesce; never wait for backend recovery.
- Broker crash: ordinary IME and context-free/stale-safe candidate behavior continue.
- Malformed/oversized message: broker rejects it without publishing state.
- Session/revision mismatch: reject/discard.
- Model refresh failure: do not publish a partial or foreign snapshot.

No exception may cross the TSF/COM boundary.

## Test strategy

Implementation is test-driven. Tests must be written failing first for each contract.

### Native/TSF contract tests

- classifier uses `GetAppProperty`, not `GetProperty`, for `GUID_PROP_INPUTSCOPE`;
- explicit password/PIN/credential scopes produce `PASSWORD` and no context push;
- missing/unsupported scope metadata does not suppress ordinary capture;
- capture remains bounded to configured before/after limits;
- TSF shipped shell contains no model runtime or background model worker;
- context-send path contains no wait loop / synchronous flush contract;
- backend absence does not block or fail the TSF callback.

### Protocol/broker tests

- raw context can be pushed but cannot be retrieved through any public operation;
- malformed/oversized payloads are rejected;
- session nonce and revision isolate context updates;
- focus/secure transition invalidates prior state;
- latest-wins coalescing discards obsolete queued updates;
- obsolete model results cannot publish after a newer revision;
- diagnostics contain lengths/reasons/IDs but never raw text.

### Persistence regression tests

Use distinctive sentinel secrets and verify repository/runtime test outputs do not write them to logs, temp files, SQLite/cache fixtures, or diagnostic dumps created by the feature.

### Integration tests

- normal editor: context reaches background model and changes ranking;
- missing InputScope metadata: context still works;
- password control: no model-context update is observed;
- rapid typing/focus changes: no cross-session context publication;
- broker stopped/hung: typing path remains responsive and ordinary IME remains usable.

Target-machine latency measurements should record p50/p95/p99 capture/send and background refresh separately. The security design does not claim the slow-context issue solved until those measurements exist.

## Compatibility with crash containment

The 2026-08-04 crash-containment change was correct to remove the old heavy context bridge from the editor-hosted TSF shell. This design does **not** revert that architectural decision.

Instead it reintroduces only the minimum bounded capture/send seam required for useful context, while keeping model work, long-lived workers, blocking waits, serialization-heavy orchestration, and GPU refresh outside the editor process.

The shipped architecture documentation must be updated so it no longer claims surrounding-text capture exists before the safe capture seam is actually linked and tested.

## Non-goals

- No complete defense against malicious same-user Windows applications.
- No administrator/kernel malware defense.
- No semantic secret classifier for arbitrary prose.
- No global input history.
- No automatic Engram ingestion.
- No project/workspace/repository scanning.
- No clipboard-history ingestion.
- No network/cloud context upload.
- No synchronous model forward on the keypress path.
- No reintroduction of the legacy in-TSF context worker/bridge unchanged.

## Acceptance criteria

The design is complete when all of the following are true:

1. A normal editor with absent InputScope metadata can provide bounded surrounding text to the local model.
2. Explicit password/PIN/credential fields provide no surrounding plaintext to the model path.
3. Raw context exists only as current ephemeral state; no history or raw-context query API exists.
4. Focus/session/revision isolation prevents deliberate cross-application reuse of captured context.
5. TSF capture/send performs no backend wait and no model work; backend failure cannot block typing.
6. No feature-added log/cache/temp/telemetry path persists raw context.
7. Production keypress handling remains immutable-snapshot/CPU-query only.
8. Automated tests enforce the contracts above, and target-machine measurements establish the resulting latency behavior.
