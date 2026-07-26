# Native Weasel/librime integration

## Status and scope

This document records the integration seam verified against the source layout
of Weasel `0.17.4` and librime `1.15.0`. It does not install, register, or
replace any input method.

The current native files are a compile-oriented skeleton. They deliberately
exclude:

- executable TSF registration and unregistration code;
- mutation of an installed Weasel tree;
- Weasel IPC message-number changes;
- the model-service Named Pipe server and its ACL implementation;
- wiring automatic activation of Microsoft Pinyin into installed Weasel;
- production sensitive-field classification.

Those exclusions prevent an incomplete experiment from changing the user's
primary input path.

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

The skeleton avoids copying upstream classes. `SurroundingTextEditSession`
implements `ITfEditSession` independently so it can first be tested as a
probe, then adapted to inherit Weasel's `CEditSession` during the actual fork.

## Proposed context path

```text
TextEditSink / focus change
  -> fail-closed capture policy
  -> RequestSurroundingText(TF_ES_READ)
  -> bounded before/after snapshot
  -> new Weasel IPC context-update message
  -> WeaselServer forwards context_update to the model service
  -> after acknowledgement, publish EditorContextEpoch
  -> ai_translator query carries that epoch
```

The TSF edit session must never perform model or pipe I/O. Its callback should
enqueue a snapshot onto a WeaselTSF-owned worker queue and return immediately.
Only the worker extends Weasel IPC.

Fast reads use `{8192, 4096}` UTF-16 code units. An idle timer may issue a
second request with `{32768, 32768}`. `ShiftStart`/`ShiftEnd` report the actual
movement; moving fewer units than requested marks that side as reaching the
current TSF region boundary. This is a region-completeness signal, not proof
that the entire editor document was exposed.

### Weasel IPC extension

Weasel `0.17.4`'s `PipeChannel` already supports a request body, but its default
buffer is 64 KiB. The fast snapshot fits after UTF-16 serialization; a full
`32768 + 32768` snapshot does not. Increasing the shared buffer globally would
change every existing command and is not the low-risk option.

Add context transfer as three explicit commands after
`WEASEL_IPC_CHANGE_PAGE`:

```text
WEASEL_IPC_CONTEXT_BEGIN
WEASEL_IPC_CONTEXT_CHUNK
WEASEL_IPC_CONTEXT_END
```

Each chunk must stay below 24,000 UTF-16 code units and carry a small header:

```text
session_id, context_epoch, chunk_index, chunk_count,
before_length, after_length, flags, payload
```

WeaselServer assembles chunks in a bounded per-session buffer, rejects missing,
duplicate, stale or over-limit chunks, and discards incomplete assemblies after
200 ms. `CONTEXT_END` only publishes the epoch after the complete snapshot has
been forwarded successfully to the model service. Focus-out, session removal,
deny-policy transition and service restart clear all partial assemblies.

The exact wire encoding should reuse Weasel's existing wide-body stream for
this internal hop. It must not reuse the Python service's UTF-8 JSON framing:
these are separate protocols with different lifecycle and buffer constraints.

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

## Rime partial-consumption semantics

The translator uses:

```text
candidate.start = segment.start
candidate.end   = segment.start + consumed_keys
```

Since v1 raw keys are ASCII full pinyin, byte count and key count are equal.
Every response is rejected unless:

- type is `candidates`;
- `session_id`, `revision` and `context_epoch` exactly match;
- `0 < consumed_keys <= raw_keys.size()`;
- candidate text is a JSON string.

Candidate order is preserved from the service. The plugin does not add Rime
Ice frequency, an artificial long-token bonus, or a traditional fallback.
Coverage candidates are merely labelled for UI diagnostics.

The current `EditorContextEpoch` is only an in-process handoff point. The
actual WeaselServer IPC extension still needs to publish it after forwarding a
context snapshot successfully. Until that exists, epoch zero is expected.

### Loading the translator on Windows

The static target `neural_weasel_rime_plugin` must be linked into the
experimental `RimeWithWeasel`/WeaselServer build. Because static-library dead
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

This requires librime private headers (`rime/setup.h`) already available to the
Weasel source build. The exact call placement must be integration-tested
against Weasel startup and deployer paths; it is not applied to the installed
0.17.4 binaries by this skeleton.

## Experimental profile and CLSID

`experimental_profile_ids.h` reserves a distinct text-service CLSID and
zh-CN language-profile GUID. Production registration must clone the minimum
Weasel registration flow but use:

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
machine now live in `native/tsf/`. Their exact safety and lifecycle contract is
documented in [profile-fallback.md](profile-fallback.md). No caller is wired
into the installed input method, and the registration planner intentionally has
no mutating executor.

## Build and verification status

The machine used to create this skeleton did not expose `cmake`, `cl`, or
`ninja` on `PATH`, so no native target was compiled. Static review was performed
against the upstream source interfaces noted above. Required follow-up checks:

- compile with the Visual Studio toolset and Windows SDK used by Weasel
  `0.17.4`;
- verify `RequestEditSession` callback and COM lifetime behavior in all target
  editors;
- test whether selected text and reversed selections yield the intended active
  caret;
- test 6 ms cancellation under partial header/body reads and server restart;
- compile the module against the exact librime binary bundled with the chosen
  Weasel package;
- verify the static registration object is retained and `ai_translator` is
  loaded before any schema instantiates it;
- confirm the experimental schema does not include translators that reorder AI
  candidates.
