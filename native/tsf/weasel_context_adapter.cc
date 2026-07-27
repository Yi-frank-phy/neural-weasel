#include "tsf/weasel_context_adapter.h"

#include <inputscope.h>
#include <propvarutil.h>

#include <algorithm>
#include <atomic>
#include <cwctype>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>
#include <utility>

#include "context/context_update_bridge.h"
#include "tsf/surrounding_text_edit_session.h"

namespace neural_weasel::tsf {
namespace {

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

CapturePolicyDecision ClassifyInputScope(ITfContext* context,
                                         TfEditCookie edit_cookie) {
  if (context == nullptr || IsBlacklistedProcess() || !IsInputDesktop()) {
    return {false, CaptureDenyReason::kSecureDesktop};
  }

  TF_SELECTION selection{};
  ULONG fetched = 0;
  if (FAILED(context->GetSelection(
          edit_cookie, TF_DEFAULT_SELECTION, 1, &selection, &fetched)) ||
      fetched != 1 || selection.range == nullptr) {
    return {false, CaptureDenyReason::kPolicyUnavailable};
  }

  ITfProperty* property = nullptr;
  PROPVARIANT value;
  PropVariantInit(&value);
  const HRESULT property_result =
      context->GetProperty(GUID_PROP_INPUTSCOPE, &property);
  HRESULT value_result = E_FAIL;
  if (SUCCEEDED(property_result) && property != nullptr) {
    value_result = property->GetValue(edit_cookie, selection.range, &value);
  }

  bool positively_classified = false;
  bool sensitive = false;
  if (SUCCEEDED(value_result) && value.vt == VT_UNKNOWN &&
      value.punkVal != nullptr) {
    ITfInputScope* input_scope = nullptr;
    if (SUCCEEDED(value.punkVal->QueryInterface(
            IID_ITfInputScope,
            reinterpret_cast<void**>(&input_scope))) &&
        input_scope != nullptr) {
      InputScope* scopes = nullptr;
      UINT count = 0;
      if (SUCCEEDED(input_scope->GetInputScopes(&scopes, &count)) &&
          scopes != nullptr && count > 0) {
        positively_classified = true;
        for (UINT index = 0; index < count; ++index) {
          sensitive =
              sensitive || scopes[index] == IS_PASSWORD ||
              scopes[index] == IS_PIN;
        }
      }
      CoTaskMemFree(scopes);
      input_scope->Release();
    }
  }

  PropVariantClear(&value);
  SafeRelease(property);
  SafeRelease(selection.range);
  if (sensitive) {
    return {false, CaptureDenyReason::kSensitiveInputScope};
  }
  if (!positively_classified) {
    return {false, CaptureDenyReason::kPolicyUnavailable};
  }
  return {true, CaptureDenyReason::kNone};
}

struct BridgeState {
  std::mutex mutex;
  std::unique_ptr<context::ContextUpdateBridge> bridge;
};

BridgeState& State() {
  // The inert mutex holder intentionally outlives DLL statics. The owned
  // worker is always stopped from WeaselTSF::Deactivate before DLL unload.
  static auto* state = new BridgeState;
  return *state;
}

void Submit(SurroundingTextSnapshot snapshot,
            context::ContextUpdateMetadata metadata) {
  auto& state = State();
  std::lock_guard lock(state.mutex);
  if (state.bridge != nullptr) {
    state.bridge->Submit(std::move(snapshot), std::move(metadata));
  }
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
    const CapturePolicyDecision policy =
        ClassifyInputScope(context_, edit_cookie);
    const SurroundingTextSnapshot snapshot = CaptureSurroundingText(
        context_, edit_cookie, {8192, 4096}, policy);
    Submit(snapshot, Metadata(!policy.allowed));
    return snapshot.result;
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
  SurroundingTextSnapshot snapshot;
  snapshot.result = E_ACCESSDENIED;
  try {
    Submit(snapshot, Metadata(true));
  } catch (...) {
    // Focus transitions and host shutdown must never cross a TSF exception.
  }
}

}  // namespace neural_weasel::tsf
