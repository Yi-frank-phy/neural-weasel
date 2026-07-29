# Native Weasel/librime integration

## Status and scope

This document records the integration against pinned Weasel `0.17.4` revision
`9cc96e20dc71b80876b12f689bb5863c76c2a7ed` and librime. CI mutates only its
checked-out upstream tree, builds an independent experimental bundle, and never
registers a global profile.

The repository includes the identity-locked profile tool and build overlay.
Automatic Microsoft Pinyin activation remains excluded. Sensitive capture is
fail closed and intentionally conservative.

## Verified upstream seams

Weasel `0.17.4` already provides the required patterns:

- `WeaselTSF/EditSession.h` defines a reference-counted `CEditSession`;
- `WeaselTSF/Composition.cpp` obtains the selection inside
  `ITfEditSession::DoEditSession` and submits read work with
  `TF_ES_ASYNCDONTCARE | TF_ES_READ`;
- `WeaselTSF/TextEditSink.cpp` observes selection and text changes;
- `WeaselTSF/WeaselTSF.h` owns `weasel::Client`, while
  `WeaselIPC/WeaselClientImpl.cpp` carries focus, key and composition events to
  WeaselServer.

librime `1.15.0`'s sample module confirms that a translator plugin registers
`Component<TranslatorType>` with `RIME_REGISTER_MODULE`, and that multiple
candidates can be returned using `FifoTranslation`.

Its `plugins/plugins_module.cc` also contains an explicit Windows TODO for
external shared-library loading. Therefore a standalone translator DLL is not
a valid deployment route for stock Weasel `0.17.4`. The CMake skeleton builds a
static library instead.

`SurroundingTextEditSession` implements `ITfEditSession` independently. The
pinned overlay calls the repository adapter from Weasel's `TextEditSink`; it
does not copy upstream composition classes.

## Context path

```text
TextEditSink / focus change
  -> fail-closed capture policy
  -> RequestSurroundingText(TF_ES_READ)
  -> bounded before/after snapshot
  -> repository context bridge worker
  -> per-user model-service Named Pipe context_update
  -> service publishes a new immutable snapshot
  -> ai_translator query carries that epoch
```

The TSF edit session must never perform model or pipe I/O. Its callback should
enqueue a snapshot onto a WeaselTSF-owned worker queue and return immediately.
Only the worker performs model-service pipe I/O.

The standalone implementation of this post-callback boundary is
`neural_weasel_context_bridge`. It coalesces snapshots on an owned worker,
forwards `context_update`, waits for the exact service epoch to become ready,
and then publishes `EditorContextEpoch`. See
[context-bridge.md](context-bridge.md). It has no Weasel/librime header
dependency and does not register a profile.

Fast reads use `{8192, 4096}` UTF-16 code units. An idle timer may issue a
second request with `{32768, 32768}`. `ShiftStart`/`ShiftEnd` report the actual
movement; moving fewer units than requested marks that side as reaching the
current TSF region boundary. This is a region-completeness signal, not proof
that the entire editor document was exposed.

### Context transport

The TSF DLL connects directly to the separate per-user model-service pipe from
the bridge worker. This avoids changing upstream Weasel IPC buffers or message
numbers. The TSF edit-session callback never connects, waits, retries, or runs a
model forward.

## Sensitive-text gate

`CapturePolicyDecision` is intentionally required by the session constructor.
The caller must deny capture unless it has positively established that the
field is safe. A production gate should combine:

1. password/PIN `InputScope` values and TSF context properties;
2. secure-desktop/session checks;
3. an explicit process/application blacklist;
4. an allow/deny policy state that defaults to deny on query failure.

Denied snapshots contain no text. Diagnostics may record HRESULT, lengths,
boundary flags and a keyed hash, but never the original content. The model
service and pipe are not a fallback security boundary.

## Named Pipe contract

`NamedPipeClient` derives the current process `TokenUser` SID before connecting
and keeps one byte-mode connection to:

```text
\\.\pipe\NeuralWeasel-v1-<current-user-SID>
```

Each frame is:

```text
uint32_le byte_length
byte[byte_length] UTF-8 JSON
```

`TryQuery` uses an absolute deadline across connect, write and read, returns
`kBusy` rather than waiting for a concurrent caller, and cancels pending
overlapped I/O at expiry. The translator uses a 6 ms deadline. A timeout,
malformed response or epoch/revision mismatch produces no AI translation; it
must never block the Windows keystroke thread waiting for a model forward.

The name derivation matches the Python service and prevents users from
accidentally sharing a global endpoint. The pipe server must additionally
create the pipe with an ACL restricted to that SID and reject remote clients;
the name itself is not an authorization boundary.

The client also validates the connected server process before sending bytes:
`GetNamedPipeServerProcessId` identifies the process, and both its `TokenUser`
SID and the current process SID must be valid and equal. Any PID, token-query
or SID failure closes the connection. This mitigates cross-user pipe-name
squatting; the server must still use first-instance creation because same-user
processes share the SID.

## Rime partial-consumption semantics

The translator uses:

```text
candidate.start = segment.start
candidate.end   = segment.start + consumed_keys
```

Since v1 raw keys are ASCII full pinyin, byte count and key count are equal.
Every response is rejected unless:

- type is `candidates`;
- `session_id` and `revision` exactly match;
- a nonzero requested `context_epoch` exactly matches; zero accepts the
  service's resolved latest epoch;
- `0 < consumed_keys <= raw_keys.size()`;
- candidate text is a JSON string.

Candidate order is preserved from the service. The plugin does not add Rime
Ice frequency, an artificial long-token bonus, or a traditional fallback.
Coverage candidates are merely labelled for UI diagnostics.

The context bridge publishes its local epoch only after the service confirms
readiness. TSF and WeaselServer are separate processes, so the server-side
translator intentionally sends epoch zero to request the latest service
snapshot. Secure/failed capture sends `focus{secure:true}` without source text.

### Loading the translator on Windows

The static target is linked into the experimental
`RimeWithWeasel`/WeaselServer build. Because static-library dead
stripping can omit the registration object, the integration must reference:

```cpp
void rime_require_module_ai_translator();
```

Then, in `RimeWithWeaselHandler::_Setup()`, after `rime_api->setup(...)` and
before `rime_api->initialize(...)`, force the object into the link and load the
registered module:

```cpp
rime_require_module_ai_translator();
const char* modules[] = {"ai_translator", nullptr};
rime::LoadModules(modules);
```

The pinned overlay sets `RimeTraits.modules` before
`rime_api->initialize()`, so the registration object is retained and loaded.

## Experimental profile and CLSID

`experimental_profile_ids.h` reserves a distinct text-service CLSID and zh-CN
language-profile GUID. `NeuralWeaselProfileTool.exe` implements the minimum
per-user registration flow using:

- a distinct DLL name and installation directory;
- the experimental CLSID/profile GUIDs, never Weasel's official identifiers;
- the display name `神经小狼毫（实验）`;
- a separate uninstaller/rollback manifest;
- no default-profile activation during installation.

Registration is a high-risk step because a TSF DLL is loaded into arbitrary
applications. A bad in-process DLL can crash editors even if the model service
is separate. Before registering:

1. build and smoke-test the DLL in an isolated test user or VM;
2. verify architecture-specific binaries (x64 and any required ARM64/x86
   wrappers);
3. sign or explicitly account for unsigned-DLL warnings;
4. verify unload, process shutdown and COM reference counts;
5. prepare idempotent unregister that targets only the experimental GUIDs;
6. confirm Microsoft Pinyin and the existing Weasel profile remain enabled.

Do not hard-code Microsoft Pinyin GUIDs. The fallback implementation should
enumerate installed TSF profiles and store the user-approved target, then call
`ITfInputProcessorProfileMgr::ActivateProfile` only after cancelling the
experimental composition.

The read-only registration planner, profile enumerator and hard-failure state
machine live in `native/tsf/`. The separate profile tool is the identity-locked
mutation boundary. Automatic fallback activation is not wired.

## Build and verification status

Windows CI compiles repository native targets and the pinned upstream overlay,
runs CTest, assembles a hashed bundle, scans identities, and executes only
dry-run install safety cases. Required manual follow-up checks:

- verify `RequestEditSession` callback and COM lifetime behavior in all target
  editors;
- test whether selected text and reversed selections yield the intended active
  caret;
- test 6 ms cancellation under partial header/body reads and server restart;
- verify at runtime that `ai_translator` is loaded before the schema
  instantiates it;
- confirm the experimental schema does not include translators that reorder AI
  candidates.
