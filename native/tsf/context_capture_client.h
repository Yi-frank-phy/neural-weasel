#pragma once

#include <Windows.h>

#include <mutex>
#include <optional>
#include <vector>

#include "context/context_ipc_protocol.h"

namespace neural_weasel::tsf {

enum class ContextPushResult {
  kSent,
  kCoalesced,
  kDropped,
  kUnverified,
};

// Best-effort one-way sender used inside editor-hosted TSF processes. It owns
// no worker and never waits for the broker or model backend.
class ContextCaptureClient final {
 public:
  ContextCaptureClient() noexcept;
  ~ContextCaptureClient();

  ContextCaptureClient(const ContextCaptureClient&) = delete;
  ContextCaptureClient& operator=(const ContextCaptureClient&) = delete;

  ContextPushResult TryPush(context::ContextFrame frame) noexcept;
  void Close() noexcept;

 private:
  bool ConnectVerified() noexcept;
  bool VerifyServerIdentity(HANDLE pipe) noexcept;
  void ReapCompletedLocked() noexcept;
  ContextPushResult StartWriteLocked(std::vector<std::uint8_t> bytes) noexcept;
  void CloseLocked() noexcept;

  std::mutex mutex_;
  HANDLE pipe_ = INVALID_HANDLE_VALUE;
  OVERLAPPED overlapped_{};
  HANDLE event_ = nullptr;
  bool write_in_flight_ = false;
  std::vector<std::uint8_t> in_flight_;
  std::optional<std::vector<std::uint8_t>> pending_;
};

}  // namespace neural_weasel::tsf
