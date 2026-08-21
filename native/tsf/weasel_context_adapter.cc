#include "tsf/weasel_context_adapter.h"

#include <inputscope.h>
#include <propvarutil.h>

#include <algorithm>
#include <atomic>
#include <cwctype>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <utility>

#include "context/capture_pipeline.h"
#include "context/context_update_bridge.h"
#include "context/source_context_identity.h"
#include "tsf/input_scope_policy.h"
#include "tsf/surrounding_text_edit_session.h"

namespace neural_weasel::tsf {
namespace {

// InputScope.idl defines GUID_PROP_INPUTSCOPE with this value. Some Windows
// SDK/linker combinations expose only the declaration, so keep the property
// key local instead of depending on a global GUID definition in uuid.lib.
constexpr GUID kInputScopePropertyGuid = {
    0x1713dd5a,
    0x68e7,
    0x4a5b,
    {0x9a, 0xf6, 0x59, 0x2a, 0x59, 0x5c, 0x77, 0x8d}};

template <typename T>
void SafeRelease(T*& value) {
  if (value != nullptr) {
    value->Release();
    value = nullptr;
  }
}

std::wstring Lower(std::wstring value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](wchar_t character) {
                   return static_cast<wchar_t>(towlower(character));
                 });
  return value;
}

std::wstring ProcessName() {
  std::wstring path(32768, L'\0');
  DWORD size = static_cast<DWORD>(path.size());
  if (!QueryFullProcessImageNameW(
          GetCurrentProcess(), 0, path.data(), &size)) {
    return {};
  }
  path.resize(size);
  return std::filesystem::path(path).filename().wstring();
}

bool IsBlacklistedProcess() {
  const std::wstring name = Lower(ProcessName());
  return name == L"credentialuibroker.exe" || name == L"logonui.exe" ||
         name == L"lsass.exe";
}

bool IsInputDesktop() {
  HDESK input = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
  if (input == nullptr) {
    return false;
  }
  HDESK thread = GetThreadDesktop(GetCurrentThreadId());
  wchar_t input_name[256] = {};
  wchar_t thread_name[256] = {};
  DWORD ignored = 0;
  const bool valid =
      thread != nullptr &&
      GetUserObjectInformationW(input, UOI_NAME, input_name,
                                sizeof(input_name), &ignored) &&
      GetUserObjectInformationW(thread, UOI_NAME, thread_name,
                                sizeof(thread_name), &ignored);
  CloseDesktop(input);
  return valid && _wcsicmp(input_name, thread_name) == 0;
}

InputScopePolicyResult ReadInputScopePolicy(
    ITfContext* context, TfEditCookie edit_cookie) noexcept {
  const InputScopePolicyResult normal = ClassifyInputScopes(nullptr, 0);
  if (context == nullptr) {
    return normal;
  }

  ITfReadOnlyProperty* property = nullptr;
  if (FAILED(context->GetAppProperty(kInputScopePropertyGuid, &property)) ||
      property == nullptr) {
    return normal;
  }

  TF_SELECTION selection{};
  ULONG fetched = 0;
  if (FAILED(context->GetSelection(
          edit_cookie, TF_DEFAULT_SELECTION, 1, &selection, &fetched)) ||
      fetched != 1 || selection.range == nullptr) {
    SafeRelease(selection.range);
    SafeRelease(property);
    return normal;
  }

  VARIANT value;
  VariantInit(&value);
  const HRESULT value_result =
      property->GetValue(edit_cookie, selection.range, &value);
  SafeRelease(selection.range);
  SafeRelease(property);

  InputScopePolicyResult policy = normal;
  if (SUCCEEDED(value_result) && value.vt == VT_UNKNOWN &&
      value.punkVal != nullptr) {
    ITfInputScope* input_scope = nullptr;
    if (SUCCEEDED(value.punkVal->QueryInterface(
            IID_ITfInputScope,
            reinterpret_cast<void**>(&input_scope))) &&
        input_scope != nullptr) {
      InputScope* scopes = nullptr;
      UINT count = 0;
      if (SUCCEEDED(input_scope->GetInputScopes(&scopes, &count))) {
        policy = ClassifyInputScopes(scopes, count);
      }
      CoTaskMemFree(scopes);
      input_scope->Release();
    }
  }
  VariantClear(&value);
  return policy;
}

struct BridgeState {
  std::mutex mutex;
  std::unique_ptr<context::ContextUpdateBridge> bridge;
  context::SourceContextIdentity identity;
};

BridgeState& State() {
  // The inert mutex holder intentionally outlives DLL statics. The owned
  // worker is always stopped from WeaselTSF::Deactivate before DLL unload.
  static auto* state = new BridgeState;
  return *state;
}

context::ContextUpdateMetadata Metadata(bool secure) {
  context::ContextUpdateMetadata metadata;
  metadata.application_id = ProcessName();
  metadata.session_id =
      "tsf-" + std::to_string(GetCurrentProcessId());
  metadata.secure = secure;
  metadata.partial = true;
  return metadata;
}

void SubmitCleanupLocked(BridgeState& state) {
  if (state.bridge == nullptr) {
    return;
  }
  SurroundingTextSnapshot snapshot;
  snapshot.result = E_ACCESSDENIED;
  state.bridge->Submit(std::move(snapshot), Metadata(true));
}

class ContextCaptureSession final : public ITfEditSession {
 public:
  explicit ContextCaptureSession(ITfContext* context) : context_(context) {
    if (context_ != nullptr) {
      context_->AddRef();
    }
  }

  HRESULT STDMETHODCALLTYPE QueryInterface(
      REFIID iid, void** object) override {
    if (object == nullptr) {
      return E_INVALIDARG;
    }
    *object = nullptr;
    if (IsEqualIID(iid, IID_IUnknown) ||
        IsEqualIID(iid, IID_ITfEditSession)) {
      *object = static_cast<ITfEditSession*>(this);
      AddRef();
      return S_OK;
    }
    return E_NOINTERFACE;
  }

  ULONG STDMETHODCALLTYPE AddRef() override {
    return ++references_;
  }

  ULONG STDMETHODCALLTYPE Release() override {
    const ULONG remaining = --references_;
    if (remaining == 0) {
      SafeRelease(context_);
      delete this;
    }
    return remaining;
  }

  HRESULT STDMETHODCALLTYPE DoEditSession(
      TfEditCookie edit_cookie) override {
    const InputScopePolicyResult policy =
        ReadInputScopePolicy(context_, edit_cookie);

    // Explicit protected scopes are a hard deny before any surrounding-text
    // read. The text-free cleanup also invalidates the prior source lifetime.
    if (policy.state == InputScopeState::kPassword || !policy.allow_capture ||
        IsBlacklistedProcess() || !IsInputDesktop()) {
      auto& state = State();
      std::lock_guard lock(state.mutex);
      state.identity.EndFocus();
      SubmitCleanupLocked(state);
      return S_OK;
    }

    auto& state = State();
    std::lock_guard lock(state.mutex);
    if (state.bridge == nullptr) {
      return S_OK;
    }
    if (!state.identity.active() && !state.identity.BeginFocus()) {
      return S_OK;
    }

    auto captured = context::CaptureWithPolicy(
        policy, state.identity, [&]() {
          return CaptureSurroundingText(
              context_, edit_cookie, {8192, 4096},
              {true, CaptureDenyReason::kNone});
        });
    if (!captured) {
      return S_OK;
    }

    const context::SourceContextStamp stamp{
        captured->metadata.source_capability,
        captured->metadata.revision,
    };
    if (!state.identity.IsCurrent(stamp)) {
      return S_OK;
    }

    // PRIVATE is prediction-only. The current bridge has no persistence sink;
    // keep raw text solely in the ephemeral snapshot and never copy it into
    // capture metadata.
    context::ContextUpdateMetadata metadata = Metadata(false);
    metadata.partial = captured->snapshot.partial;
    const HRESULT capture_result = captured->snapshot.result;
    state.bridge->Submit(std::move(captured->snapshot), std::move(metadata));
    return capture_result;
  }

 private:
  ~ContextCaptureSession() = default;

  std::atomic<ULONG> references_{1};
  ITfContext* context_ = nullptr;
};

}  // namespace

void StartWeaselContext() {
  auto& state = State();
  std::lock_guard lock(state.mutex);
  if (state.bridge == nullptr) {
    state.bridge = std::make_unique<context::ContextUpdateBridge>(
        std::make_unique<context::NamedPipeContextUpdateTransport>());
  }
}

void StopWeaselContext() noexcept {
  std::unique_ptr<context::ContextUpdateBridge> bridge;
  {
    auto& state = State();
    std::lock_guard lock(state.mutex);
    state.identity.EndFocus();
    bridge = std::move(state.bridge);
  }
  bridge.reset();
}

HRESULT CaptureWeaselContext(ITfContext* context, TfClientId client_id) {
  if (context == nullptr || client_id == TF_CLIENTID_NULL) {
    return E_INVALIDARG;
  }
  auto* session = new ContextCaptureSession(context);
  HRESULT edit_result = E_FAIL;
  const HRESULT result = context->RequestEditSession(
      client_id, session, TF_ES_ASYNCDONTCARE | TF_ES_READ, &edit_result);
  session->Release();
  return result;
}

void ClearWeaselContext() noexcept {
  try {
    auto& state = State();
    std::lock_guard lock(state.mutex);
    state.identity.EndFocus();
    SubmitCleanupLocked(state);
  } catch (...) {
    // Focus transitions and host shutdown must never cross a TSF exception.
  }
}

}  // namespace neural_weasel::tsf
