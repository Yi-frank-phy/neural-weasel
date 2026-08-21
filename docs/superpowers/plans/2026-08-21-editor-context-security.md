# Secure Ephemeral Editor Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore useful surrounding-text context from ordinary Windows editors while keeping protected credential fields out of the model path, preserving crash containment, and ensuring Neural Weasel does not become a centralized raw-text oracle or persistence point.

**Architecture:** The editor-hosted TSF DLL performs only bounded read-only capture, explicit InputScope classification, source-session revisioning, and a best-effort overlapped one-way push. Plaintext is sent only after verifying that the receiving pipe belongs to the sibling `NeuralWeaselServer.exe`. A new out-of-process context broker inside `NeuralWeaselServer.exe` receives the compact binary frame, coalesces/validates source state, and reuses the existing heavy `ContextUpdateBridge` to send asynchronous context updates to the Python model service. The server-side Rime translator reads only the accepted context identity `(model_epoch, source_capability, source_revision)` and includes that identity in candidate queries. Python binds model epochs to that source capability/revision and refuses cross-session reuse. No raw-context retrieval API or persistence is added.

**Tech Stack:** C++17, Windows TSF/COM, Win32 named pipes with overlapped I/O, BCrypt RNG, existing Weasel 0.17.4 overlay, Rime, Python 3.12, pywin32, pytest, CMake/CTest, GitHub Actions Windows runner.

**Spec:** `docs/superpowers/specs/2026-08-21-editor-context-security-design.md`

## Global Constraints

- The production GGUF keypress path must never own a model forward.
- `NeuralWeaselExperimentalTSF.dll` must not link `context_update_bridge.cc`, the Python/model service, or the existing synchronous model `named_pipe_client.cc`.
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
- `EditorContext.metadata() -> dict[str, object]` remains the only log-safe metadata helper.
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

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
uv run pytest tests/test_context.py -q
```

Expected: failure because `before_sha256` and `after_sha256` are currently present.

- [ ] **Step 3: Implement the minimum change**

Remove `hashlib` and both SHA-256 fields from `EditorContext.metadata()`; retain only content-independent metadata.

- [ ] **Step 4: Run focused tests and confirm GREEN**

```bash
uv run pytest tests/test_context.py -q
```

Expected: all context tests pass.

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

In `native/tsf/input_scope_policy_test.cc`, assert pure scope classification:

```cpp
assert(LabelInputScopes(nullptr, 0) == EditorSecurityLabel::kNormal);
InputScope private_scope[] = {IS_PRIVATE};
assert(LabelInputScopes(private_scope, 1) == EditorSecurityLabel::kPrivate);
InputScope password_scope[] = {IS_PASSWORD};
assert(LabelInputScopes(password_scope, 1) == EditorSecurityLabel::kPassword);
InputScope pin_scope[] = {IS_NUMERIC_PIN};
assert(LabelInputScopes(pin_scope, 1) == EditorSecurityLabel::kPassword);
```

In `tests/test_native_contract_v02.py`, add:

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

On Windows native build after file scaffolding:

```powershell
cmake --build build/native --config Release
ctest --test-dir build/native -C Release --output-on-failure
```

Expected: source test fails because the new file does not exist; native target does not yet exist.

- [ ] **Step 3: Implement classifier**

Use the documented application-property path:

```cpp
ITfReadOnlyProperty* property = nullptr;
const HRESULT property_result =
    context->GetAppProperty(kInputScopePropertyGuid, &property);
if (SUCCEEDED(property_result) && property != nullptr) {
  value_result = property->GetValue(edit_cookie, selection.range, &value);
}
```

Do not require a positive InputScope classification to permit capture. Keep explicit secure-desktop/credential-process checks hard-deny. Remove classification responsibility from the old heavy adapter so it calls the new helper only.

- [ ] **Step 4: Run Python contract + Windows CTest and confirm GREEN**

```bash
uv run pytest tests/test_native_contract_v02.py -q
```

```powershell
cmake --build build/native --config Release
ctest --test-dir build/native -C Release --output-on-failure
```

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
  bool BeginFocus() noexcept;                 // BCryptGenRandom new capability
  SourceContextIdentity ReserveCapture() noexcept; // increments revision
  SourceContextIdentity EndFocus() noexcept;  // increments, then inactive
  SourceContextIdentity Current() const noexcept;
};
```

Properties:
- capability rotates on every `BeginFocus()`;
- revision is monotonic within one capability;
- `ReserveCapture()` while inactive returns `active=false` and no usable frame;
- a capture reserves its revision **before** the async TSF read session is requested, so a late completion from before `EndFocus()` is older than the clear revision.

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

Expected: target/file missing.

- [ ] **Step 3: Implement minimal state with `BCryptGenRandom`**

Use `BCRYPT_USE_SYSTEM_PREFERRED_RNG`; link `bcrypt`. No heap-backed history and no thread/worker creation.

- [ ] **Step 4: Run CTest and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add native/tsf/context_capture_state.* native/tsf/context_capture_state_test.cc native/CMakeLists.txt
git commit -m "feat: add ephemeral TSF context source identity"
```

---

## Task 4: Implement the minimal authenticated one-way TSF context client

**Files:**
- Create: `native/tsf/context_capture_protocol.h`
- Create: `native/tsf/context_capture_client.h`
- Create: `native/tsf/context_capture_client.cc`
- Create: `native/tsf/context_capture_client_test.cc`
- Modify: `native/CMakeLists.txt`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**

Binary frame, little-endian, maximum bounded by the existing 8192/4096 UTF-16 limits:

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
3. use `FILE_FLAG_OVERLAPPED`;
4. keep one in-flight write and one replaceable latest pending frame; if pressure persists, retain only latest;
5. before the first plaintext write on a connection, call `GetNamedPipeServerProcessId`, open the server with `PROCESS_QUERY_LIMITED_INFORMATION`, and require its canonical executable path to equal the expected sibling `NeuralWeaselServer.exe` path;
6. close/drop on any identity or protocol failure.

- [ ] **Step 1: Write failing contract tests**

Add source assertions:

```python
def test_tsf_context_client_is_one_way_nonblocking_and_verifies_server() -> None:
    source = (ROOT / "native/tsf/context_capture_client.cc").read_text(encoding="utf-8")
    assert "FILE_FLAG_OVERLAPPED" in source
    assert "GetNamedPipeServerProcessId" in source
    assert "PROCESS_QUERY_LIMITED_INFORMATION" in source
    assert "NeuralWeaselServer.exe" in source
    assert "WaitNamedPipe" not in source
    assert "FlushFileBuffers" not in source
```

Native tests cover frame-size rejection, pending replacement, failed identity => no write, and no response read API.

- [ ] **Step 2: Run contract/CTest and confirm RED**

- [ ] **Step 3: Implement the smallest client satisfying the contract**

Keep all state fixed/bounded. `TryPush` may return an enum such as `kSent`, `kCoalesced`, `kDropped`, `kUnverified`; none is fatal to the host.

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

Overlay hooks on pinned Weasel 0.17.4:
- `WeaselTSF::OnSetThreadFocus()` -> `BeginWeaselContextFocus()`;
- `WeaselTSF::OnKillThreadFocus()` -> `ClearWeaselContext()` before/around composition abort;
- `WeaselTSF::OnSetFocus(...)` -> clear old source then begin the new focused document source;
- `WeaselTSF::OnEndEdit(...)` -> call `CaptureWeaselContext(pContext, _tfClientId)` after the host’s existing edit bookkeeping and before return;
- `Deactivate()` -> `ClearWeaselContext()` only; no worker shutdown.

The capture revision must be reserved before `RequestEditSession`, then carried by the edit-session object so a late callback cannot outrank a later focus clear.

- [ ] **Step 1: Replace the old fail-closed source test with a finer failing boundary test**

The new test should assert the TSF overlay **does** include only:
- `input_scope_policy.cc`
- `surrounding_text_edit_session.cc`
- `context_capture_state.cc`
- `context_capture_client.cc`
- `weasel_context_adapter.cc`

and **does not** include:
- `native/pipe/named_pipe_client.cc`
- `native/context/context_update_bridge.cc`
- model/Python runtime files
- `StartWeaselContext`
- `StopWeaselContext`.

Also assert the pinned hook strings for `OnEndEdit`, thread focus, document focus, and deactivate are present in the overlay script.

- [ ] **Step 2: Run `tests/test_native_contract_v02.py` and confirm RED**

Expected: current crash-containment test rejects all context code and hooks are absent.

- [ ] **Step 3: Refactor adapter and overlay**

`ClassifyContextInputScope` decides label. For `kPassword`, send only a clear marker with no before/after payload. For `kNormal`/`kPrivate`, call bounded `CaptureSurroundingText` and push the frame. All methods catch/fold errors into a no-context result; no exception crosses COM.

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

Broker pipe requirements:
- separate context-capture pipe name from the Python model-service pipe;
- current-user-only DACL;
- `FILE_FLAG_FIRST_PIPE_INSTANCE` on first listener;
- `PIPE_REJECT_REMOTE_CLIENTS`;
- bounded binary decoder using the Task 4 frame format;
- `GetNamedPipeClientProcessId` must equal the frame `source_pid` (integrity sanity check; not a full same-user security boundary);
- per-capability latest revision table; a `kClear` revision invalidates that capability and any older late frame;
- one active accepted source identity exported to the server-side model query path;
- normal pressure coalesces latest context; server threads may block because this is outside editor processes.

Extend `ContextUpdateMetadata` with:

```cpp
std::string source_capability;
std::uint64_t source_revision = 0;
EditorSecurityLabel security_label = EditorSecurityLabel::kNormal;
```

`BuildContextRequest` forwards these as `context_session`, `source_revision`, `security_label`. Secure clear continues to contain no raw text.

- [ ] **Step 1: Write failing broker tests**

Native tests cover:
- malformed/oversized frame rejected;
- revision 5 accepted, later revision 4 discarded;
- clear revision 6 invalidates late context revision 5;
- different capability may become active without allowing the previous capability to overwrite it via an older frame;
- broker receives private label without turning it into password deny;
- broker source contains `FILE_FLAG_FIRST_PIPE_INSTANCE`, `PIPE_REJECT_REMOTE_CLIENTS`, and `GetNamedPipeClientProcessId`.

Overlay source test asserts `NeuralWeaselServer.exe` receives `context_capture_broker.cc`, `context_update_bridge.cc`, and the existing model pipe client, while the TSF target does not.

- [ ] **Step 2: Run tests and confirm RED**

- [ ] **Step 3: Implement broker and server lifecycle wiring**

Patch pinned `WeaselServer/WeaselServer.cpp` through the overlay so a stack-owned broker starts before `WeaselServerApp::Run()` and stops/destructs after it. Broker startup failure must leave ordinary Weasel usable; it only disables neural editor context.

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

Replace scalar-only publication with one coherent accepted identity:

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

Use a mutex or seqlock-style coherent snapshot; do not read epoch/capability/revision independently.

After Python acknowledges a context update, `ContextUpdateBridge` publishes all three accepted values. On focus clear/invalidation, reset all three before any candidate query can reuse them.

`AiTranslator::Query` sends:

```json
{
  "type": "query_candidates",
  "session_id": "<rime translator session>",
  "revision": 12,
  "context_epoch": 44,
  "context_session": "<128-bit source capability hex>",
  "source_revision": 9,
  "raw_keys": "..."
}
```

- [ ] **Step 1: Write failing epoch/translator contract tests**

Test coherent publish/load/reset and assert `ai_translator.cc` includes both `context_session` and `source_revision` from the same loaded identity.

- [ ] **Step 2: Run CTest/source tests and confirm RED**

- [ ] **Step 3: Implement coherent identity publication**

Do not change the candidate path into a model-forward path; it reads only this tiny accepted identity plus immutable model snapshot epoch.

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

Add bounded server-side bindings:

```python
@dataclass(frozen=True, slots=True)
class ContextBinding:
    context_session: str
    source_revision: int
    security_label: str

self._context_bindings: dict[int, ContextBinding]
```

`context_update` must require and validate:
- `context_session`: exactly 32 lowercase hex chars;
- `source_revision >= 1`;
- `security_label in {"normal", "private"}`;
- bounded before/after strings.

After `engine.request_context_update`, bind the assigned model epoch to that identity. Keep bindings only for epochs still queryable by the engine; cap the dictionary to the same small retention order used for snapshots (or a fixed conservative bound such as 8).

`query_candidates` / `query_pinyin` must carry `context_session` and `source_revision` when `context_epoch > 0`; reject mismatch with a structured `context_session_mismatch` error before `engine.query`.

Epoch zero with no accepted identity must **not** silently select a previous application’s latest bound context. Return a retryable no-context/not-ready response until a current identity is accepted.

- [ ] **Step 1: Write failing tests**

Add tests for:

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

Also test correct capability/revision passes; old revision fails; unknown extra raw-context read operation remains `unknown_message_type`; private label is accepted but never persisted/logged.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/test_pipe_server.py -q
```

- [ ] **Step 3: Implement the binding checks**

Refactor common query validation into a small private helper to avoid divergence between pinyin and unified candidate endpoints.

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
- Modify: `src/neural_weasel/pipe_server.py` only if a failing test reveals leakage
- Modify: `src/neural_weasel/http_server.py` only if a failing test reveals feature-added leakage
- Modify: `tests/test_native_contract_v02.py`

**Interfaces:**
- There is no `get_context`, `dump_context`, `list_contexts`, or history operation.
- Diagnostics contain only IDs/labels/lengths/counters/errors, never `before`/`after` or stable content fingerprints.
- The restored TSF path does not use the file bridge or create context files.

- [ ] **Step 1: Write sentinel-secret tests**

Use a distinctive string such as `NW_SENTINEL_SECRET_6d1f48f1` and assert:
- it does not appear in diagnostics/stats representations;
- protocol unknown-operation responses do not echo it;
- no test-created log/temp/cache/SQLite output from this feature contains it;
- source contract confirms the TSF context sender does not reference the Wisdom file-bridge path.

- [ ] **Step 2: Run and confirm RED where current diagnostics violate the contract**

```bash
uv run pytest tests/test_context_privacy_contract.py tests/test_context.py tests/test_pipe_server.py -q
```

- [ ] **Step 3: Make only the leakage-removal changes required by the tests**

Do not broaden this task into deleting unrelated legacy components; the production restored TSF context route simply must not use them.

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
- Modify: `docs/architecture/` relevant context/runtime document(s) discovered during execution
- Modify: `scripts/verify-windows-bundle.py`
- Modify: `tests/test_install_safety_v02.py`
- Modify: `tests/test_native_contract_v02.py`

**Interfaces / documentation facts:**
- surrounding-text capture is now real and bounded;
- TSF carries only capture/classification/one-way sender code;
- heavy context broker/bridge is in `NeuralWeaselServer.exe`;
- Python/model runtime remains out of editor processes;
- password/PIN fields are hard-denied;
- raw context is ephemeral and has no read/history API;
- target-machine latency remains a measured release gate, not a claimed property.

- [ ] **Step 1: Write failing bundle/document contract assertions**

Update tests to require the new TSF capture sources and server broker sources in the generated overlay/bundle, while continuing to forbid heavy bridge/model IPC inside the TSF DLL.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/test_install_safety_v02.py tests/test_native_contract_v02.py -q
```

- [ ] **Step 3: Update docs and bundle verifier**

Correct the old `STATUS.md` contradiction: do not say context capture exists unless the shipped overlay and tests prove it.

- [ ] **Step 4: Run focused tests and confirm GREEN**

- [ ] **Step 5: Commit**

```bash
git add docs/STATUS.md docs/architecture scripts/verify-windows-bundle.py tests/test_install_safety_v02.py tests/test_native_contract_v02.py
git commit -m "docs: describe crash-contained editor context pipeline"
```

---

## Task 11: Full verification and target-machine latency/security smoke

**Files:**
- Create: `docs/manual/editor-context-security-smoke.md`
- Modify: `.github/workflows/ci.yml` only if the new native CTests are not already included by the existing `ctest` step
- No production code changes unless verification exposes a defect; defects return to the relevant earlier TDD task.

- [ ] **Step 1: Run the complete Python suite**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Expected: all pass (platform-specific Windows tests may remain skipped off Windows).

- [ ] **Step 2: Run the Windows native/bundle suite**

Use the same commands exercised by `.github/workflows/ci.yml`: configure/build native tests, run CTest, build the pinned Weasel overlay/bundle, and run `verify-windows-bundle.py`.

Expected: all native tests and bundle isolation tests pass.

- [ ] **Step 3: Perform target-machine security smoke**

Document and execute on the Windows target:
1. normal Notepad/VS Code/Chrome editable field: context update accepted and ranking changes;
2. control with no InputScope provider: context still accepted;
3. password/PIN field: broker/model context-update counter does not increase with plaintext and previous accepted identity is invalidated;
4. switch rapidly VS Code -> browser -> VS Code: no candidate query succeeds with a foreign context capability;
5. stop `NeuralWeaselServer.exe`: typing remains usable; capture calls drop without host stall;
6. pre-create/squat the context pipe from a test process: real broker refuses first-instance startup and TSF identity verification sends no plaintext to the squatter;
7. scan feature-created logs/temp/cache outputs for a sentinel secret: zero hits.

- [ ] **Step 4: Measure latency separately from model refresh**

Record at least 200 normal capture events and report:
- TSF capture + enqueue/push p50/p95/p99;
- broker receive/accept p50/p95/p99;
- background model refresh p50/p95/p99;
- dropped/coalesced/stale-discard counts.

Acceptance: no synchronous model forward on keypress; no editor-host wait on backend recovery. Do not close #11 merely because obsolete results are discarded; in-flight cancellation/full-prefill waste is a separate optimization.

- [ ] **Step 5: Commit the manual evidence procedure**

```bash
git add docs/manual/editor-context-security-smoke.md .github/workflows/ci.yml
git commit -m "test: add editor context security and latency smoke gate"
```

---

## Final Integration Gate

Before marking PR #20 ready or merging:

- [ ] Compare the implementation against every acceptance criterion in the approved spec.
- [ ] Confirm `NeuralWeaselExperimentalTSF.dll` has no heavy context bridge/model runtime dependency.
- [ ] Confirm explicit password/PIN scopes never serialize `before`/`after`.
- [ ] Confirm missing InputScope metadata does not suppress ordinary capture.
- [ ] Confirm pipe squatting cannot turn the TSF sender into a plaintext oracle under the designed endpoint-identity checks.
- [ ] Confirm candidate queries cannot reuse another source capability/revision.
- [ ] Confirm no raw-context read/history API exists.
- [ ] Confirm no new raw-context persistence path exists.
- [ ] Confirm full Python + Windows native + bundle CI is green.
- [ ] Keep PR #20 draft until implementation, review, CI, and target-machine smoke evidence are complete.
