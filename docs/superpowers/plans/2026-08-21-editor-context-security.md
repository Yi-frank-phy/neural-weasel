# Secure Ephemeral Editor Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore useful surrounding-text context from ordinary Windows editors while keeping protected credential fields out of the model path, preserving crash containment, and ensuring Neural Weasel does not become a centralized raw-text oracle or persistence point.

**Architecture:** The editor-hosted TSF DLL performs only bounded read-only capture, explicit InputScope classification, source-session revisioning, and a best-effort overlapped one-way push. It sends only to `\\.\pipe\NeuralWeaselContext-v1-<current-user-SID>` and releases plaintext only after verifying that the pipe server process is the sibling `NeuralWeaselServer.exe`. A new out-of-process context broker inside `NeuralWeaselServer.exe` receives the compact binary frame, validates/coalesces source state, and reuses the existing heavy `ContextUpdateBridge` to send asynchronous context updates over the existing Python model-service pipe. The server-side Rime translator reads only the accepted context identity `(model_epoch, source_capability, source_revision)` and includes that identity in candidate queries. Python binds model epochs to that source capability/revision and refuses cross-session reuse. No raw-context retrieval API or persistence is added.

**Tech Stack:** C++17, Windows TSF/COM, Win32 named pipes with overlapped I/O, BCrypt RNG, existing Weasel 0.17.4 overlay, Rime, Python 3.12, pywin32, pytest, CMake/CTest, GitHub Actions Windows runner.

**Spec:** `docs/superpowers/specs/2026-08-21-editor-context-security-design.md`

## Global Constraints

- The production GGUF keypress path must never own a model forward.
- `NeuralWeaselExperimentalTSF.dll` must not link `context_update_bridge.cc`, the Python/model service, or the existing synchronous model `named_pipe_client.cc`.
- The TSF context pipe is exactly `\\.\pipe\NeuralWeaselContext-v1-<current-user-SID>`; the existing model-service pipe `\\.\pipe\NeuralWeasel-v1-<current-user-SID>` remains separate.
- Protected password/PIN/credential scopes are hard-denied before text serialization or IPC.
- Missing/unsupported InputScope metadata is eligible for ordinary ephemeral prediction.
- `IS_PRIVATE` is allowed for current local prediction but carries a private/no-persistence label.
- Raw context must not be written to logs, temp files, SQLite, telemetry, diagnostics, or automatic Engram memory.
- No public protocol operation may return captured raw context.
- Any broker/pipe failure degrades to reduced-context ordinary IME behavior; typing must not wait for recovery.
- Do not claim issue #11 fully solved: obsolete in-flight GPU forwards may still consume work, but obsolete results must never publish.

---

## Task 1: Make context diagnostics content-independent

**Files:**
- Modify: `src/neural_weasel/context.py`
- Modify: `tests/test_context.py`

**Interfaces:**
- `EditorContext.metadata() -> dict[str, object]` remains the log-safe metadata helper.
- It may expose application ID, UTF-16 lengths, flags, HRESULT, labels/reasons, and counters.
- It must not expose raw text or stable hashes/fingerprints derived from raw text.

- [ ] **Step 1: Write the failing regression test**

Add to `tests/test_context.py`:

```python
def test_metadata_contains_no_text_or_stable_content_fingerprint() -> None:
    secret = "NW_SENTINEL_BANK_PASSWORD_9b4b1b4e"
    context = EditorContext(
        before=secret,
        after="private research",
        app_id="editor.exe",
        partial=False,
        complete_region=True,
        secure=False,
    )
    metadata = context.metadata()
    serialized = repr(metadata)
    assert secret not in serialized
    assert "private research" not in serialized
    assert "before_sha256" not in metadata
    assert "after_sha256" not in metadata
    assert metadata["before_utf16"] == len(secret)
```

- [ ] **Step 2: Run focused test and confirm RED**

```bash
uv run pytest tests/test_context.py -q
```

Expected: failure because `before_sha256` and `after_sha256` are currently present.

- [ ] **Step 3: Implement minimum change**

Remove `hashlib` and both SHA-256 fields from `EditorContext.metadata()`; retain only content-independent metadata.

- [ ] **Step 4: Run focused tests and confirm GREEN**

```bash
uv run pytest tests/test_context.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/neural_weasel/context.py tests/test_context.py
git commit -m "security: remove context content fingerprints from diagnostics"
```

---

## Task 2: Split and correct TSF InputScope classification

**Files:**
- Create: `native/tsf/input_scope_policy.h`
- Create: `native/tsf/input_scope_policy.cc`
- Create: `native/tsf/input_scope_policy_test.cc`
- Modify: `native/CMakeLists.txt`
- Modify: `native/tsf/weasel_context_adapter.cc`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**

```cpp
enum class EditorSecurityLabel : std::uint8_t {
  kNormal = 0,
  kPrivate = 1,
  kPassword = 2,
};

struct InputScopePolicy {
  bool capture_allowed = true;
  EditorSecurityLabel label = EditorSecurityLabel::kNormal;
  CaptureDenyReason deny_reason = CaptureDenyReason::kNone;
};

EditorSecurityLabel LabelInputScopes(const InputScope* scopes,
                                     std::size_t count) noexcept;
InputScopePolicy ClassifyContextInputScope(ITfContext* context,
                                          TfEditCookie edit_cookie) noexcept;
```

Rules:
- password/PIN scopes -> `kPassword`, capture denied;
- `IS_PRIVATE` -> `kPrivate`, capture allowed;
- ordinary/empty/missing provider -> `kNormal`, capture allowed;
- secure desktop / known credential host process -> capture denied before InputScope lookup.

- [ ] **Step 1: Write failing native/source contract tests**

`native/tsf/input_scope_policy_test.cc`:

```cpp
assert(LabelInputScopes(nullptr, 0) == EditorSecurityLabel::kNormal);
InputScope private_scope[] = {IS_PRIVATE};
assert(LabelInputScopes(private_scope, 1) == EditorSecurityLabel::kPrivate);
InputScope password_scope[] = {IS_PASSWORD};
assert(LabelInputScopes(password_scope, 1) == EditorSecurityLabel::kPassword);
InputScope pin_scope[] = {IS_NUMERIC_PIN};
assert(LabelInputScopes(pin_scope, 1) == EditorSecurityLabel::kPassword);
```

`tests/test_native_contract_v02.py`:

```python
def test_input_scope_uses_application_property_and_unknown_is_not_denied() -> None:
    source = (ROOT / "native/tsf/input_scope_policy.cc").read_text(encoding="utf-8")
    assert "GetAppProperty" in source
    assert "GetProperty(kInputScopePropertyGuid" not in source
    assert "IS_PRIVATE" in source
    assert "EditorSecurityLabel::kPrivate" in source
```

- [ ] **Step 2: Run tests and confirm RED**

```bash
uv run pytest tests/test_native_contract_v02.py -q
```

Windows native build after test target is declared:

```powershell
cmake --build build/native --config Release
ctest --test-dir build/native -C Release --output-on-failure
```

- [ ] **Step 3: Implement classifier**

Use the Windows application-property API:

```cpp
ITfReadOnlyProperty* property = nullptr;
const HRESULT property_result =
    context->GetAppProperty(kInputScopePropertyGuid, &property);
if (SUCCEEDED(property_result) && property != nullptr) {
  value_result = property->GetValue(edit_cookie, selection.range, &value);
}
```

Do not require positive InputScope metadata to permit capture. Keep explicit secure-desktop/credential-process checks hard-deny. Remove classification logic from the old adapter so it delegates to this helper.

- [ ] **Step 4: Run Python contract + Windows CTest and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/tsf/input_scope_policy.* native/tsf/input_scope_policy_test.cc native/CMakeLists.txt native/tsf/weasel_context_adapter.cc tests/test_native_contract_v02.py
git commit -m "fix: classify TSF input scope through app properties"
```

---

## Task 3: Add source capability/revision state without a TSF worker

**Files:**
- Create: `native/tsf/context_capture_state.h`
- Create: `native/tsf/context_capture_state.cc`
- Create: `native/tsf/context_capture_state_test.cc`
- Modify: `native/CMakeLists.txt`

**Interfaces:**

```cpp
struct SourceContextIdentity {
  std::array<std::uint8_t, 16> capability{};
  std::uint64_t revision = 0;
  bool active = false;
};

class ContextCaptureState final {
 public:
  bool BeginFocus() noexcept;
  SourceContextIdentity ReserveCapture() noexcept;
  SourceContextIdentity EndFocus() noexcept;
  SourceContextIdentity Current() const noexcept;
};
```

Properties:
- `BeginFocus()` rotates to a fresh capability and resets that capability's revision sequence;
- revision is monotonic within one capability;
- `ReserveCapture()` while inactive returns `active=false`;
- capture reserves its revision **before** the async TSF read session is requested, so a callback scheduled before `EndFocus()` can never outrank the later clear revision;
- if thread-focus and document-focus hooks both call `BeginFocus()` before any edit callback, the later capability simply supersedes the earlier unused one.

- [ ] **Step 1: Write failing state-machine tests**

```cpp
ContextCaptureState state;
assert(!state.Current().active);
assert(state.BeginFocus());
auto first = state.ReserveCapture();
auto second = state.ReserveCapture();
assert(first.active && second.active);
assert(second.revision == first.revision + 1);
auto ended = state.EndFocus();
assert(ended.revision > second.revision);
assert(!state.Current().active);
assert(!state.ReserveCapture().active);
assert(state.BeginFocus());
auto next_focus = state.ReserveCapture();
assert(next_focus.capability != first.capability);
```

- [ ] **Step 2: Run CTest and confirm RED**

- [ ] **Step 3: Implement with `BCryptGenRandom`**

Use `BCRYPT_USE_SYSTEM_PREFERRED_RNG`; link `bcrypt`. No heap-backed history and no thread/worker creation.

- [ ] **Step 4: Run CTest and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/tsf/context_capture_state.* native/tsf/context_capture_state_test.cc native/CMakeLists.txt
git commit -m "feat: add ephemeral TSF context source identity"
```

---

## Task 4: Implement the authenticated one-way TSF context client

**Files:**
- Create: `native/tsf/context_capture_protocol.h`
- Create: `native/tsf/context_capture_client.h`
- Create: `native/tsf/context_capture_client.cc`
- Create: `native/tsf/context_capture_client_test.cc`
- Modify: `native/CMakeLists.txt`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**

The client connects only to `\\.\pipe\NeuralWeaselContext-v1-<current-user-SID>`. Binary frames are little-endian and bounded by the existing 8192/4096 UTF-16 limits:

```cpp
enum class ContextFrameKind : std::uint8_t { kContext = 1, kClear = 2 };

struct ContextFrameHeader {
  std::uint32_t magic;          // 'NWCT'
  std::uint16_t version;        // 1
  std::uint8_t kind;
  std::uint8_t security_label;
  std::uint32_t source_pid;
  std::uint64_t revision;
  std::array<std::uint8_t, 16> capability;
  std::uint32_t before_utf16_units;
  std::uint32_t after_utf16_units;
};
```

`ContextCaptureClient::TryPush(...) noexcept` must:
1. never call `WaitNamedPipe` or `FlushFileBuffers`;
2. never perform request/response I/O;
3. open with `FILE_FLAG_OVERLAPPED`;
4. keep one in-flight write plus one replaceable latest pending frame; each future capture call first reaps a completed write without waiting, then sends only the newest pending/current frame; if the pipe stays busy and input stops, an unsent pending normal context may be lost by design;
5. before the first plaintext write on a connection, call `GetNamedPipeServerProcessId`, open that PID with `PROCESS_QUERY_LIMITED_INFORMATION`, and require its canonical executable path to equal `<TSF-module-directory>\NeuralWeaselServer.exe`;
6. close/drop on identity failure, missing server, pressure, or protocol error;
7. when a `kClear` frame arrives while a normal write is in flight, replace any pending normal frame with the clear; do not wait for the in-flight operation.

- [ ] **Step 1: Write failing contract tests**

`tests/test_native_contract_v02.py`:

```python
def test_tsf_context_client_is_one_way_nonblocking_and_verifies_server() -> None:
    source = (ROOT / "native/tsf/context_capture_client.cc").read_text(encoding="utf-8")
    assert "NeuralWeaselContext-v1-" in source
    assert "FILE_FLAG_OVERLAPPED" in source
    assert "GetNamedPipeServerProcessId" in source
    assert "PROCESS_QUERY_LIMITED_INFORMATION" in source
    assert "NeuralWeaselServer.exe" in source
    assert "WaitNamedPipe" not in source
    assert "FlushFileBuffers" not in source
```

Native tests cover frame-size rejection, latest-pending replacement, clear replacing pending normal context, failed endpoint identity => no plaintext write, and absence of a response-reading API.

- [ ] **Step 2: Run contract/CTest and confirm RED**

- [ ] **Step 3: Implement smallest bounded client**

`TryPush` returns a nonfatal status (`kSent`, `kCoalesced`, `kDropped`, `kUnverified`). It owns no worker thread.

- [ ] **Step 4: Run tests and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/tsf/context_capture_protocol.h native/tsf/context_capture_client.* native/tsf/context_capture_client_test.cc native/CMakeLists.txt tests/test_native_contract_v02.py
git commit -m "feat: add authenticated nonblocking TSF context push"
```

---

## Task 5: Refactor the Weasel TSF adapter into capture-only host code and wire focus/edit hooks

**Files:**
- Modify: `native/tsf/weasel_context_adapter.h`
- Modify: `native/tsf/weasel_context_adapter.cc`
- Modify: `scripts/prepare-weasel-overlay.ps1`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**

```cpp
void BeginWeaselContextFocus() noexcept;
HRESULT CaptureWeaselContext(ITfContext* context, TfClientId client_id) noexcept;
void ClearWeaselContext() noexcept;
```

Remove `StartWeaselContext()` / `StopWeaselContext()` and all `ContextUpdateBridge`, model-pipe, mutex-owned worker, JSON, and background lifecycle from the adapter.

Pinned Weasel 0.17.4 overlay hooks:
- `WeaselTSF::OnSetThreadFocus()` -> `BeginWeaselContextFocus()`;
- `WeaselTSF::OnKillThreadFocus()` -> `ClearWeaselContext()` before/around composition abort;
- `WeaselTSF::OnSetFocus(ITfDocumentMgr*, ITfDocumentMgr*)` -> if focused document changes, clear the old source then begin the new source;
- `WeaselTSF::OnEndEdit(...)` in `WeaselTSF/TextEditSink.cpp` -> `CaptureWeaselContext(pContext, _tfClientId)` after existing edit bookkeeping and before `return S_OK;`;
- `Deactivate()` -> `ClearWeaselContext()` only; no worker shutdown.

The capture revision is reserved before `RequestEditSession` and carried by the edit-session object.

- [ ] **Step 1: Replace the old broad fail-closed source test with a finer failing boundary test**

Require the TSF overlay to include only these Neural Weasel context sources:
- `input_scope_policy.cc`
- `surrounding_text_edit_session.cc`
- `context_capture_state.cc`
- `context_capture_client.cc`
- `weasel_context_adapter.cc`

Continue to forbid in the TSF target:
- `native/pipe/named_pipe_client.cc`
- `native/context/context_update_bridge.cc`
- model/Python runtime files
- `StartWeaselContext`
- `StopWeaselContext`.

Assert overlay injections include `CaptureWeaselContext(pContext, _tfClientId)`, `BeginWeaselContextFocus`, and `ClearWeaselContext` at the pinned focus/edit/deactivate seams.

- [ ] **Step 2: Run `tests/test_native_contract_v02.py` and confirm RED**

- [ ] **Step 3: Refactor adapter and overlay**

For `kPassword`, never serialize before/after; enqueue only a `kClear` frame with password label. For `kNormal`/`kPrivate`, call bounded `CaptureSurroundingText` and push the context frame. Fold all failures into no-context behavior; no exception crosses COM.

- [ ] **Step 4: Run source tests + Windows native build/CTest and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/tsf/weasel_context_adapter.* scripts/prepare-weasel-overlay.ps1 tests/test_native_contract_v02.py
git commit -m "feat: restore crash-contained TSF surrounding context capture"
```

---

## Task 6: Host the heavy context broker in `NeuralWeaselServer.exe`

**Files:**
- Create: `native/context/context_capture_broker.h`
- Create: `native/context/context_capture_broker.cc`
- Create: `native/context/context_capture_broker_test.cc`
- Modify: `native/context/context_update_bridge.h`
- Modify: `native/context/context_update_bridge.cc`
- Modify: `native/context/context_update_bridge_test.cc`
- Modify: `native/CMakeLists.txt`
- Modify: `scripts/prepare-weasel-overlay.ps1`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**

```cpp
class ContextCaptureBroker final {
 public:
  ContextCaptureBroker();
  ~ContextCaptureBroker();
  bool Start() noexcept;
  void Stop() noexcept;
};
```

The broker listens on exactly `\\.\pipe\NeuralWeaselContext-v1-<current-user-SID>` and requires:
- current-user-only DACL;
- `FILE_FLAG_FIRST_PIPE_INSTANCE` for the first listener;
- `PIPE_REJECT_REMOTE_CLIENTS`;
- bounded binary decode of Task 4 frames;
- actual `GetNamedPipeClientProcessId` equals header `source_pid`;
- per-capability latest revision state;
- `kClear` revision invalidates that capability and makes any older late frame unusable;
- a password-labeled clear calls local identity invalidation immediately and sends secure cleanup to the Python service without raw text;
- a normal/private focus clear invalidates local accepted identity but need not synchronously erase Python model state; the 128-bit old capability is no longer exposed to Rime queries and will be overwritten by the next accepted context;
- broker threads may block because they live in `NeuralWeaselServer.exe`, not the editor host.

Extend `ContextUpdateMetadata`:

```cpp
std::string source_capability;  // 32 lowercase hex chars
std::uint64_t source_revision = 0;
EditorSecurityLabel security_label = EditorSecurityLabel::kNormal;
```

`BuildContextRequest` forwards `context_session`, `source_revision`, and `security_label`. Add an explicit bridge method for clear/focus cleanup so `kPassword` can issue `focus secure=true` without any text.

- [ ] **Step 1: Write failing broker/bridge tests**

Cover:
- malformed/oversized frame rejected;
- revision 5 accepted, later revision 4 discarded;
- clear revision 6 invalidates late context revision 5;
- different capability can become active; an invalidated capability cannot republish;
- private label forwards as private, not password;
- password clear generates no before/after JSON;
- source contains `FILE_FLAG_FIRST_PIPE_INSTANCE`, `PIPE_REJECT_REMOTE_CLIENTS`, `GetNamedPipeClientProcessId`.

Overlay test requires broker/bridge/model-pipe sources in `NeuralWeaselServer.exe`, and forbids them in TSF.

- [ ] **Step 2: Run tests and confirm RED**

- [ ] **Step 3: Implement broker and server lifecycle**

Patch pinned `WeaselServer/WeaselServer.cpp` through the overlay so a stack-owned broker starts before `WeaselServerApp::Run()` and stops/destructs after it. Broker startup failure leaves ordinary Weasel usable and disables neural editor context only.

- [ ] **Step 4: Run native/source tests and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/context/context_capture_broker.* native/context/context_capture_broker_test.cc native/context/context_update_bridge.* native/context/context_update_bridge_test.cc native/CMakeLists.txt scripts/prepare-weasel-overlay.ps1 tests/test_native_contract_v02.py
git commit -m "feat: move context forwarding into NeuralWeaselServer broker"
```

---

## Task 7: Publish accepted context identity to Rime candidate queries

**Files:**
- Modify: `native/rime/editor_context_epoch.h`
- Modify: `native/rime/editor_context_epoch.cc`
- Modify: `native/rime/epoch_semantics_test.cc`
- Modify: `native/context/context_update_bridge.cc`
- Modify: `native/rime/ai_translator.cc`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**

Replace scalar-only publication with a coherent accepted identity:

```cpp
struct AcceptedEditorContext {
  std::uint64_t model_epoch = 0;
  std::string source_capability;
  std::uint64_t source_revision = 0;
};

AcceptedEditorContext Load() const;
void Publish(std::uint64_t model_epoch,
             std::string_view source_capability,
             std::uint64_t source_revision);
void Reset() noexcept;
```

Use one mutex-protected coherent snapshot. After Python acknowledges a context update, `ContextUpdateBridge` publishes all three values. Clear/invalidation resets all three before future candidate queries.

`AiTranslator::Query` sends:

```json
{
  "type": "query_candidates",
  "session_id": "<rime translator session>",
  "revision": 12,
  "context_epoch": 44,
  "context_session": "0123456789abcdef0123456789abcdef",
  "source_revision": 9,
  "raw_keys": "...",
  "candidate_count": 5
}
```

If no accepted editor identity exists (`model_epoch == 0` or empty capability), the translator must not ask the Python service to reinterpret epoch zero as some previous application's latest context; it degrades to ordinary/non-neural candidates until a new context identity is accepted.

- [ ] **Step 1: Write failing epoch/translator tests**

Test coherent publish/load/reset and source-contract assertions that `ai_translator.cc` includes both `context_session` and `source_revision` from the same loaded identity and does not issue a contextual candidate query when identity is empty.

- [ ] **Step 2: Run CTest/source tests and confirm RED**

- [ ] **Step 3: Implement coherent identity publication**

Keep candidate path immutable-snapshot only; it reads tiny identity metadata and does no model forward.

- [ ] **Step 4: Run tests and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/rime/editor_context_epoch.* native/rime/epoch_semantics_test.cc native/context/context_update_bridge.cc native/rime/ai_translator.cc tests/test_native_contract_v02.py
git commit -m "security: bind candidate queries to accepted editor context"
```

---

## Task 8: Enforce context-session binding in the Python model pipe

**Files:**
- Modify: `src/neural_weasel/pipe_server.py`
- Modify: `tests/test_pipe_server.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ContextBinding:
    context_session: str
    source_revision: int
    security_label: str
```

`NamedPipeServer` owns `dict[int, ContextBinding]` capped at **8** most recently assigned model epochs.

`context_update` requires:
- `context_session`: exactly 32 lowercase hex chars;
- `source_revision >= 1`;
- `security_label in {"normal", "private"}`;
- bounded `before`/`after` strings.

After `engine.request_context_update`, bind assigned model epoch to the supplied identity and evict oldest bindings above 8.

`query_candidates` and `query_pinyin` require `context_session` and `source_revision` whenever `context_epoch > 0`; mismatch returns `context_session_mismatch` before `engine.query`. Epoch zero is not allowed to select a previous bound context for a caller without a current accepted identity.

- [ ] **Step 1: Write failing tests**

```python
accepted = server.handle_message({
    "type": "context_update",
    "context_epoch": 7,
    "context_session": "a" * 32,
    "source_revision": 4,
    "security_label": "normal",
    "before": "论文上下文",
    "after": "",
})
epoch = accepted["context_epoch"]

wrong = server.handle_message({
    "type": "query_candidates",
    "session_id": "rime-1",
    "revision": 1,
    "context_epoch": epoch,
    "context_session": "b" * 32,
    "source_revision": 4,
    "raw_keys": "lunwen",
    "candidate_count": 5,
})
assert wrong["ok"] is False
assert wrong["error"]["code"] == "context_session_mismatch"
```

Also test correct identity passes; old revision fails; malformed capability fails; 9th binding evicts the oldest; private label is accepted; unsupported `get_context`/`dump_context` remain unknown message types.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/test_pipe_server.py -q
```

- [ ] **Step 3: Implement binding checks**

Factor one private helper used by both pinyin and unified candidate endpoints.

- [ ] **Step 4: Run focused tests and confirm GREEN**

```bash
uv run pytest tests/test_pipe_server.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/neural_weasel/pipe_server.py tests/test_pipe_server.py
git commit -m "security: isolate model snapshots by editor context session"
```

---

## Task 9: Enforce no-persistence/no-oracle regressions end to end

**Files:**
- Create: `tests/test_context_privacy_contract.py`
- Modify: `src/neural_weasel/pipe_server.py` only if a failing test exposes leakage
- Modify: `src/neural_weasel/http_server.py` only if a failing test exposes feature-added leakage
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**
- no `get_context`, `dump_context`, `list_contexts`, or context-history operation;
- diagnostics expose only IDs/labels/lengths/counters/errors, never `before`/`after` or stable content fingerprints;
- restored TSF context does not use the Wisdom file bridge and creates no context files.

- [ ] **Step 1: Write sentinel-secret tests**

Use `NW_SENTINEL_SECRET_6d1f48f1` and assert:
- secret absent from diagnostics/stats representations;
- unknown-operation responses never echo it;
- test-created log/temp/cache/SQLite outputs from this feature contain zero sentinel hits;
- TSF context sender source contains no Wisdom file-bridge path/reference.

- [ ] **Step 2: Run and confirm RED where current behavior violates the contract**

```bash
uv run pytest tests/test_context_privacy_contract.py tests/test_context.py tests/test_pipe_server.py -q
```

- [ ] **Step 3: Make only leakage-removal changes required by tests**

Do not delete unrelated legacy components; the production restored TSF path simply must not use them.

- [ ] **Step 4: Run and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add tests/test_context_privacy_contract.py tests/test_native_contract_v02.py src/neural_weasel/pipe_server.py src/neural_weasel/http_server.py
git commit -m "test: enforce ephemeral editor context privacy contract"
```

---

## Task 10: Update shipped architecture documentation and Windows bundle contract

**Files:**
- Modify: `docs/STATUS.md`
- Modify: `docs/architecture/context-bridge.md`
- Modify: `docs/architecture/native-integration.md`
- Modify: `scripts/verify-windows-bundle.py`
- Modify: `tests/test_install_safety_v02.py`
- Modify: `tests/test_native_contract_v02.py`

**Documentation facts:**
- surrounding-text capture is real and bounded;
- TSF carries capture/classification/one-way sender only;
- heavy context broker/bridge lives in `NeuralWeaselServer.exe`;
- Python/model runtime remains outside editor processes;
- password/PIN fields are hard-denied;
- raw context is ephemeral with no read/history API;
- target-machine latency is a measured release gate, not a claimed property.

- [ ] **Step 1: Write failing bundle/document contract assertions**

Require new minimal TSF sources and server broker sources in the generated overlay/bundle while continuing to forbid heavy bridge/model IPC inside the TSF DLL.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/test_install_safety_v02.py tests/test_native_contract_v02.py -q
```

- [ ] **Step 3: Update docs and bundle verifier**

Correct the current `STATUS.md` contradiction; describe the two separate pipes and the TSF/server process boundary exactly.

- [ ] **Step 4: Run focused tests and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add docs/STATUS.md docs/architecture/context-bridge.md docs/architecture/native-integration.md scripts/verify-windows-bundle.py tests/test_install_safety_v02.py tests/test_native_contract_v02.py
git commit -m "docs: describe crash-contained editor context pipeline"
```

---

## Task 11: Full verification and target-machine latency/security smoke

**Files:**
- Create: `docs/manual/editor-context-security-smoke.md`
- Modify: `.github/workflows/ci.yml` only if the existing native `ctest` step does not automatically discover the new CTests
- No production-code change in this task; any defect returns to the owning earlier TDD task.

- [ ] **Step 1: Run complete Python suite**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

- [ ] **Step 2: Run Windows native/bundle suite**

Use the same configure/build/CTest/bundle/`verify-windows-bundle.py` commands exercised by `.github/workflows/ci.yml`.

- [ ] **Step 3: Perform target-machine security smoke**

Document and execute:
1. normal Notepad/VS Code/Chrome editable field: context update accepted and ranking changes;
2. control with no InputScope provider: context still accepted;
3. password/PIN field: no plaintext context update reaches broker/model and accepted identity is invalidated;
4. rapid VS Code -> browser -> VS Code switching: no candidate query succeeds with a foreign capability/revision;
5. stop `NeuralWeaselServer.exe`: typing remains usable and capture drops without host stall;
6. pre-create/squat `NeuralWeaselContext-v1-<SID>` from a test process: real broker refuses first-instance startup and TSF endpoint verification sends no plaintext to the squatter;
7. scan feature-created logs/temp/cache outputs for sentinel secret: zero hits.

- [ ] **Step 4: Measure latency separately from model refresh**

Record at least 200 normal capture events and report:
- TSF capture + enqueue/push p50/p95/p99;
- broker receive/accept p50/p95/p99;
- background model refresh p50/p95/p99;
- dropped/coalesced/stale-discard counts.

Acceptance: no synchronous model forward on keypress and no editor-host wait for backend recovery. Do not close #11 merely because stale results are discarded; in-flight cancellation/full-prefill waste remains separate.

- [ ] **Step 5: Commit manual evidence procedure**

```bash
git add docs/manual/editor-context-security-smoke.md .github/workflows/ci.yml
git commit -m "test: add editor context security and latency smoke gate"
```

---

## Final Integration Gate

Before marking PR #20 ready or merging:

- [ ] Compare implementation against every acceptance criterion in the approved spec.
- [ ] Confirm `NeuralWeaselExperimentalTSF.dll` has no heavy context bridge/model runtime dependency.
- [ ] Confirm explicit password/PIN scopes never serialize `before`/`after`.
- [ ] Confirm missing InputScope metadata does not suppress ordinary capture.
- [ ] Confirm pipe squatting cannot turn the TSF sender into a plaintext oracle under the designed endpoint-identity checks.
- [ ] Confirm candidate queries cannot reuse another source capability/revision.
- [ ] Confirm no raw-context read/history API exists.
- [ ] Confirm no new raw-context persistence path exists.
- [ ] Confirm full Python + Windows native + bundle CI is green.
- [ ] Keep PR #20 draft until implementation, review, CI, and target-machine smoke evidence are complete.
