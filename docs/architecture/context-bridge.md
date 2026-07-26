# Native editor-context bridge

## Boundary

`neural_weasel_context_bridge` is a standalone static library. It depends only
on:

- the DTO declared by `tsf/surrounding_text_edit_session.h`;
- `neural_weasel_pipe`;
- the atomic `rime_plugin::EditorContextEpoch` handoff.

It does not include Weasel or librime headers, register a TSF profile, or
perform TSF edit-session work. The eventual Weasel adapter may reconstruct the
same DTO after internal Weasel IPC; that adapter is outside this target.

## Threading and latest-wins behavior

The `SurroundingTextEditSession` callback may move its snapshot into
`ContextUpdateBridge::Submit`. `Submit` only:

1. allocates the next monotonically increasing sequence;
2. replaces the latest ordinary pending item, or records a cleanup barrier;
3. signals the owned worker thread.

It performs no pipe I/O, model forward, health polling or epoch publication.
The queue is latest-wins. While sequence `N` is in flight, submission `N+1`
immediately makes every acknowledgement for `N` ineligible for publication.
A secure or failed-capture update is the exception to ordinary coalescing: it
occupies a cleanup-barrier slot that a later normal update cannot replace
before the cleanup reaches the service.

The bridge sends the same monotonic value as:

```text
client context_epoch, revision, sequence
```

`request_id` is the protocol-safe string `ctx-<sequence>`, never a JSON number.
It validates that exact echoed string and `client_context_epoch`. After the
service accepts the update and assigns a service epoch, the worker polls
`health` for at most 200 ms. Publication occurs only when:

- the service's ready `context_epoch` exactly equals the assigned epoch;
- the bridge sequence is still the latest;
- the bridge has not stopped or been invalidated.

A greater ready epoch means another update superseded this request and is not
silently attributed to it. `Invalidate` increments the bridge sequence and
resets the published epoch while holding the same final-publication mutex,
closing the check/publish race.

Service epochs are process-local and may decrease after restart, for example
from 100 to 1. `EditorContextEpoch::Publish` therefore stores the exact
confirmed epoch rather than applying a numeric maximum. Stale rejection comes
from the bridge sequence and final mutex, not from comparing epochs belonging
to different service processes.

## Fail-closed serialization

The bridge accepts:

- `SurroundingTextSnapshot`;
- application ID;
- session ID;
- `secure`;
- `partial`.

Context text is eligible only when all of these are true:

```text
secure == false
snapshot.result == S_OK
application ID is valid UTF-16
before and after are valid UTF-16
```

If any check fails, the serializer takes a separate denied branch. It does not
convert or append `snapshot.before` or `snapshot.after`, and it does not submit
an empty `context_update`. It immediately resets the local
`EditorContextEpoch`, then the worker sends only:

```json
{"type":"focus","request_id":"ctx-42","session_id":"...","focused":true,"secure":true}
```

The service's secure-focus handler invalidates pending/in-flight context work,
clears all published snapshots and clears commit-history fallback state. The
bridge never publishes an epoch for this request. A later non-secure update
must run as a new sequence and produce a newly ready service epoch.

Allowed `context_update` messages carry non-text metadata:

```text
app_id, secure, partial, complete_region, capture_hresult
```

No native diagnostic API exposes the source text. Logging integrations must
keep this property.

## Service acknowledgement

The current service acknowledges `context_update` before its background model
forward finishes. Publishing that assigned epoch immediately would make Rime
query a snapshot that does not exist yet. The worker therefore polls the
existing `health` message and publishes only the exact ready epoch.

The response parser is intentionally narrow: it accepts the canonical compact
JSON emitted by the local Python server and requires unique fields for type,
status, request ID and epochs. It is not a general JSON parser. A malformed or
unexpected response becomes `kProtocolError` and never publishes an epoch.

## Named Pipe peer identity

The per-user pipe name and server DACL are necessary but not sufficient:
another process can attempt to create the predictable name first.

After `CreateFileW`, `NamedPipeClient` now:

1. calls `GetNamedPipeServerProcessId`;
2. opens that process with `PROCESS_QUERY_LIMITED_INFORMATION`;
3. reads its `TokenUser`;
4. reads the current process `TokenUser`;
5. requires `EqualSid` before sending any request bytes.

Any failure closes the pipe. This complements the Python server's first-
instance and current-user ACL policy; it does not make an untrusted process
running as the same Windows user trustworthy by itself.

## Build and verification

The CMake target:

```text
neural_weasel_context_bridge
```

contains only `context/context_update_bridge.cc` and
`rime/editor_context_epoch.cc`, and links `neural_weasel_pipe`. It is built
without `NEURAL_WEASEL_BUILD_RIME_PLUGIN`, a Weasel source checkout, librime,
or nlohmann-json.

This workstation still has no MSVC, clang-cl or MinGW compiler on `PATH`.
Source and Windows SDK symbol review were performed, but the target has not
been compiled. Required Windows CI checks are:

- build the standalone target with the Weasel-supported Windows SDK;
- fake-transport tests for coalescing, stale responses, exact epoch readiness,
  invalidation races and 200 ms timeout;
- secure snapshots containing sentinel text and an assertion that the sentinel
  never occurs in the serialized request;
- pipe-server PID failure, inaccessible process token and unequal SID tests;
- Thread Sanitizer-equivalent or stress coverage for submit/invalidate/stop
  ordering where available.

An opt-in, dependency-free source test is included as
`neural_weasel_context_bridge_test`. Configure with
`-DNEURAL_WEASEL_BUILD_NATIVE_TESTS=ON`. Its fake transport blocks the first
context response, submits a secure cleanup, verifies that the stale epoch 5 is
never published and that the secure period remains at local epoch zero. It
then simulates a service restart and verifies that ready epoch 1 replaces a
previous-process epoch 100. The secure snapshot contains sentinel private text;
neither sentinel may appear in any request, and the cleanup request contains
no `before` or `after` fields.
