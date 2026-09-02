#include "tsf/weasel_context_adapter.h"

#include <inputscope.h>
#include <propvarutil.h>

#include <algorithm>
#include <atomic>
#include <cwctype>
#include <mutex>
#include <optional>
#include <string>
#include <utility>

#include "context/context_ipc_protocol.h"
#include "context/metadata_trace.h"
#include "context/source_context_identity.h"
#include "tsf/context_capture_client.h"
#include "tsf/input_scope_policy.h"
#include "tsf/surrounding_text_edit_session.h"

namespace neural_weasel::tsf {
namespace {

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

std::wstring ProcessName() noexcept {
  std::wstring path(32768, L'\0');
  DWORD size = static_cast<DWORD>(path.size());
  if (!QueryFullProcessImageNameW(
          GetCurrentProcess(), 0, path.data(), &size)) {
    return {};
  }
  path.resize(size);
  const std::size_t separator = path.find_last_of(L"\\/");
  return separator == std::wstring::npos ? path : path.substr(separator + 1U);
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

struct CaptureState final {
  std::mutex mutex;
  context::SourceContextIdentity identity;
  ContextCaptureClient client;
};

CaptureState& State() {
  // The process owns these handles for the lifetime of the loaded TSF module.
  // There is deliberately no worker thread or backend object to shut down.
  static auto* state = new CaptureState;
  return *state;
}

context::ContextScopeLabel ScopeLabel(InputScopeState state) noexcept {
  switch (state) {
    case InputScopeState::kPrivate:
      return context::ContextScopeLabel::kPrivate;
    case InputScopeState::kPassword:
      return context::ContextScopeLabel::kPassword;
    case InputScopeState::kNormal:
    default:
      return context::ContextScopeLabel::kNormal;
  }
}

std::u16string ToUtf16(std::wstring_view text) {
  std::u16string output;
  output.reserve(text.size());
  for (wchar_t unit : text) {
    output.push_back(static_cast<char16_t>(unit));
  }
  return output;
}

context::ContextFrame ClearFrame(
    const context::SourceContextStamp& stamp,
    context::ContextScopeLabel scope) {
  context::ContextFrame frame;
  frame.kind = context::ContextFrameKind::kClear;
  frame.scope_label = scope;
  frame.source_pid = GetCurrentProcessId();
  frame.revision = stamp.revision;
  frame.source_capability = stamp.capability;
  return frame;
}

void ClearReservedCapability(
    const context::SourceContextStamp& reserved,
    context::ContextScopeLabel scope) noexcept {
  try {
    auto& state = State();
    std::lock_guard lock(state.mutex);
    auto clear_stamp = state.identity.Capture();
    if (!clear_stamp || clear_stamp->capability != reserved.capability) {
      return;
    }
    state.identity.EndFocus();
    state.client.TryPush(ClearFrame(*clear_stamp, scope));
  } catch (...) {
  }
}

class ContextCaptureSession final : public ITfEditSession {
 public:
  ContextCaptureSession(
      ITfContext* context,
      context::SourceContextStamp reserved)
      : context_(context), reserved_(reserved) {
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
    try {
      const InputScopePolicyResult policy =
          ReadInputScopePolicy(context_, edit_cookie);
      neural_weasel::context::TraceContextPipeline(
          L"tsf-capture", L"event=edit-session policy=%d allow=%d",
          static_cast<int>(policy.state), policy.allow_capture ? 1 : 0);
      if (policy.state == InputScopeState::kPassword || !policy.allow_capture ||
          IsBlacklistedProcess() || !IsInputDesktop()) {
        neural_weasel::context::TraceContextPipeline(
            L"tsf-capture", L"event=edit-session result=denied");
        ClearReservedCapability(
            reserved_, context::ContextScopeLabel::kPassword);
        return S_OK;
      }

      SurroundingTextSnapshot snapshot = CaptureSurroundingText(
          context_, edit_cookie, {8192, 4096},
          {true, CaptureDenyReason::kNone});
      neural_weasel::context::TraceContextPipeline(
          L"tsf-capture",
          L"event=snapshot hr=%ld before-len=%llu after-len=%llu",
          static_cast<long>(snapshot.result),
          static_cast<unsigned long long>(snapshot.before.size()),
          static_cast<unsigned long long>(snapshot.after.size()));
      if (FAILED(snapshot.result)) {
        return snapshot.result;
      }

      context::ContextFrame frame;
      frame.kind = context::ContextFrameKind::kContext;
      frame.scope_label = ScopeLabel(policy.state);
      frame.source_pid = GetCurrentProcessId();
      frame.revision = reserved_.revision;
      frame.source_capability = reserved_.capability;
      frame.before = ToUtf16(snapshot.before);
      frame.after = ToUtf16(snapshot.after);

      auto& state = State();
      std::lock_guard lock(state.mutex);
      if (!state.identity.IsCurrent(reserved_)) {
        return S_OK;
      }
      const ContextPushResult push_result =
          state.client.TryPush(std::move(frame));
      neural_weasel::context::TraceContextPipeline(
          L"tsf-capture", L"event=push result=%d revision=%llu",
          static_cast<int>(push_result),
          static_cast<unsigned long long>(reserved_.revision));
      return S_OK;
    } catch (...) {
      return S_OK;
    }
  }

 private:
  ~ContextCaptureSession() = default;

  std::atomic<ULONG> references_{1};
  ITfContext* context_ = nullptr;
  context::SourceContextStamp reserved_{};
};

}  // namespace

void BeginWeaselContextFocus() noexcept {
  try {
    auto& state = State();
    std::lock_guard lock(state.mutex);
    const bool began = state.identity.BeginFocus();
    neural_weasel::context::TraceContextPipeline(
        L"tsf-capture", L"event=focus-begin result=%d", began ? 1 : 0);
  } catch (...) {
  }
}

HRESULT CaptureWeaselContext(
    ITfContext* context, TfClientId client_id) noexcept {
  if (context == nullptr || client_id == TF_CLIENTID_NULL) {
    return E_INVALIDARG;
  }
  try {
    context::SourceContextStamp reserved;
    {
      auto& state = State();
      std::lock_guard lock(state.mutex);
      if (!state.identity.active() && !state.identity.BeginFocus()) {
        return S_OK;
      }
      const auto stamp = state.identity.Capture();
      if (!stamp) {
        return S_OK;
      }
      reserved = *stamp;
    }

    auto* session = new ContextCaptureSession(context, reserved);
    HRESULT edit_result = E_FAIL;
    const HRESULT result = context->RequestEditSession(
        client_id, session, TF_ES_ASYNCDONTCARE | TF_ES_READ, &edit_result);
    session->Release();
    neural_weasel::context::TraceContextPipeline(
        L"tsf-capture",
        L"event=capture-request request-hr=%ld edit-hr=%ld revision=%llu",
        static_cast<long>(result), static_cast<long>(edit_result),
        static_cast<unsigned long long>(reserved.revision));
    return result;
  } catch (...) {
    return S_OK;
  }
}

void ClearWeaselContext() noexcept {
  try {
    auto& state = State();
    std::lock_guard lock(state.mutex);
    const auto clear_stamp = state.identity.Capture();
    if (!clear_stamp) {
      return;
    }
    state.identity.EndFocus();
    const ContextPushResult push_result = state.client.TryPush(
        ClearFrame(*clear_stamp, context::ContextScopeLabel::kNormal));
    neural_weasel::context::TraceContextPipeline(
        L"tsf-capture", L"event=focus-clear push-result=%d revision=%llu",
        static_cast<int>(push_result),
        static_cast<unsigned long long>(clear_stamp->revision));
  } catch (...) {
  }
}

}  // namespace neural_weasel::tsf
