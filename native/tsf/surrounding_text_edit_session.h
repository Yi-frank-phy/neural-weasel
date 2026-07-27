#pragma once

#include <Windows.h>
#include <msctf.h>

#include <atomic>
#include <cstddef>
#include <functional>
#include <string>

namespace neural_weasel::tsf {

enum class CaptureDenyReason {
  kNone,
  kSensitiveInputScope,
  kSecureDesktop,
  kBlacklistedProcess,
  kPolicyUnavailable,
};

struct CapturePolicyDecision {
  bool allowed = false;
  CaptureDenyReason reason = CaptureDenyReason::kPolicyUnavailable;
};

struct SurroundingTextLimits {
  LONG before_code_units = 8192;
  LONG after_code_units = 4096;
};

struct SurroundingTextSnapshot {
  std::wstring before;
  std::wstring after;
  bool before_reached_region_boundary = false;
  bool after_reached_region_boundary = false;
  bool partial = true;
  HRESULT result = E_PENDING;
};

using SnapshotCallback = std::function<void(SurroundingTextSnapshot)>;

SurroundingTextSnapshot CaptureSurroundingText(
    ITfContext* context,
    TfEditCookie edit_cookie,
    SurroundingTextLimits limits,
    CapturePolicyDecision policy);

// A self-contained read-only edit session suitable for adapting into Weasel's
// CEditSession hierarchy. The caller must complete fail-closed sensitive-field
// classification before constructing an allowed session.
class SurroundingTextEditSession final : public ITfEditSession {
 public:
  SurroundingTextEditSession(ITfContext* context,
                             SurroundingTextLimits limits,
                             CapturePolicyDecision policy,
                             SnapshotCallback callback);

  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override;
  ULONG STDMETHODCALLTYPE AddRef() override;
  ULONG STDMETHODCALLTYPE Release() override;
 HRESULT STDMETHODCALLTYPE DoEditSession(TfEditCookie edit_cookie) override;

 private:
  ~SurroundingTextEditSession() = default;

  void Deliver(SurroundingTextSnapshot snapshot) noexcept;

  std::atomic<ULONG> references_{1};
  ITfContext* context_ = nullptr;
  SurroundingTextLimits limits_;
  CapturePolicyDecision policy_;
  SnapshotCallback callback_;
};

// Convenience wrapper using the same asynchronous read-lock pattern as
// Weasel 0.17.4 composition edit sessions.
HRESULT RequestSurroundingText(ITfContext* context,
                               TfClientId client_id,
                               SurroundingTextLimits limits,
                               CapturePolicyDecision policy,
                               SnapshotCallback callback);

}  // namespace neural_weasel::tsf
