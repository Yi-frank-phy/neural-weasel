#include "tsf/surrounding_text_edit_session.h"

#include <algorithm>
#include <utility>
#include <vector>

namespace neural_weasel::tsf {
namespace {

template <typename T>
void SafeRelease(T*& value) {
  if (value != nullptr) {
    value->Release();
    value = nullptr;
  }
}

HRESULT ReadRange(ITfRange* range,
                  TfEditCookie edit_cookie,
                  LONG maximum_code_units,
                  std::wstring* output) {
  output->clear();
  if (maximum_code_units == 0) {
    return S_OK;
  }
  if (range == nullptr || maximum_code_units < 0) {
    return E_INVALIDARG;
  }

  std::vector<WCHAR> buffer(static_cast<std::size_t>(maximum_code_units));
  ULONG fetched = 0;
  const HRESULT result =
      range->GetText(edit_cookie, 0, buffer.data(), maximum_code_units, &fetched);
  if (SUCCEEDED(result)) {
    output->assign(buffer.data(), fetched);
  }
  return result;
}

}  // namespace

SurroundingTextEditSession::SurroundingTextEditSession(
    ITfContext* context,
    SurroundingTextLimits limits,
    CapturePolicyDecision policy,
    SnapshotCallback callback)
    : context_(context),
      limits_(limits),
      policy_(policy),
      callback_(std::move(callback)) {
  if (context_ != nullptr) {
    context_->AddRef();
  }
}

HRESULT SurroundingTextEditSession::QueryInterface(REFIID iid, void** object) {
  if (object == nullptr) {
    return E_INVALIDARG;
  }
  *object = nullptr;
  if (IsEqualIID(iid, IID_IUnknown) || IsEqualIID(iid, IID_ITfEditSession)) {
    *object = static_cast<ITfEditSession*>(this);
    AddRef();
    return S_OK;
  }
  return E_NOINTERFACE;
}

ULONG SurroundingTextEditSession::AddRef() {
  return ++references_;
}

ULONG SurroundingTextEditSession::Release() {
  const ULONG remaining = --references_;
  if (remaining == 0) {
    SafeRelease(context_);
    delete this;
  }
  return remaining;
}

HRESULT SurroundingTextEditSession::DoEditSession(TfEditCookie edit_cookie) {
  auto snapshot = CaptureSurroundingText(
      context_, edit_cookie, limits_, policy_);
  Deliver(snapshot);
  return snapshot.result;
}

SurroundingTextSnapshot CaptureSurroundingText(
    ITfContext* context,
    TfEditCookie edit_cookie,
    SurroundingTextLimits limits,
    CapturePolicyDecision policy) {
  SurroundingTextSnapshot snapshot;
  if (!policy.allowed) {
    snapshot.result = E_ACCESSDENIED;
    return snapshot;
  }
  if (context == nullptr || limits.before_code_units < 0 ||
      limits.after_code_units < 0) {
    snapshot.result = E_INVALIDARG;
    return snapshot;
  }

  TF_SELECTION selection{};
  ULONG fetched = 0;
  HRESULT result = context->GetSelection(
      edit_cookie, TF_DEFAULT_SELECTION, 1, &selection, &fetched);
  if (FAILED(result) || fetched != 1 || selection.range == nullptr) {
    const HRESULT failure = FAILED(result) ? result : E_FAIL;
    snapshot.result = failure;
    return snapshot;
  }

  ITfRange* caret = nullptr;
  ITfRange* before = nullptr;
  ITfRange* after = nullptr;
  result = selection.range->Clone(&caret);
  if (SUCCEEDED(result)) {
    const TfAnchor active_anchor =
        selection.style.ase == TF_AE_START ? TF_ANCHOR_START : TF_ANCHOR_END;
    result = caret->Collapse(edit_cookie, active_anchor);
  }
  if (SUCCEEDED(result)) {
    result = caret->Clone(&before);
  }
  if (SUCCEEDED(result)) {
    result = caret->Clone(&after);
  }

  LONG before_shift = 0;
  LONG after_shift = 0;
  if (SUCCEEDED(result) && limits.before_code_units > 0) {
    result = before->ShiftStart(edit_cookie, -limits.before_code_units,
                                &before_shift, nullptr);
  }
  if (SUCCEEDED(result) && limits.after_code_units > 0) {
    result = after->ShiftEnd(edit_cookie, limits.after_code_units,
                             &after_shift, nullptr);
  }
  if (SUCCEEDED(result)) {
    result = ReadRange(before, edit_cookie, limits.before_code_units,
                       &snapshot.before);
  }
  if (SUCCEEDED(result)) {
    result = ReadRange(after, edit_cookie, limits.after_code_units,
                       &snapshot.after);
  }

  snapshot.before_reached_region_boundary =
      limits.before_code_units == 0 ||
      -before_shift < limits.before_code_units;
  snapshot.after_reached_region_boundary =
      limits.after_code_units == 0 ||
      after_shift < limits.after_code_units;
  snapshot.partial = !(snapshot.before_reached_region_boundary &&
                       snapshot.after_reached_region_boundary);
  snapshot.result = result;

  SafeRelease(after);
  SafeRelease(before);
  SafeRelease(caret);
  SafeRelease(selection.range);

  return snapshot;
}

void SurroundingTextEditSession::Deliver(
    SurroundingTextSnapshot snapshot) noexcept {
  if (!callback_) {
    return;
  }
  try {
    callback_(std::move(snapshot));
  } catch (...) {
    // Exceptions must never cross a COM/TSF ABI boundary.
  }
}

HRESULT RequestSurroundingText(ITfContext* context,
                               TfClientId client_id,
                               SurroundingTextLimits limits,
                               CapturePolicyDecision policy,
                               SnapshotCallback callback) {
  if (context == nullptr || !callback) {
    return E_INVALIDARG;
  }

  auto* session = new SurroundingTextEditSession(
      context, limits, policy, std::move(callback));
  HRESULT edit_session_result = E_FAIL;
  const HRESULT request_result = context->RequestEditSession(
      client_id, session, TF_ES_ASYNCDONTCARE | TF_ES_READ,
      &edit_session_result);
  session->Release();
  // For asynchronous requests edit_session_result is not available until the
  // edit session runs. Completion is reported through callback.
  return request_result;
}

}  // namespace neural_weasel::tsf
