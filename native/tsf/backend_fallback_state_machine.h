#pragma once

#include <Windows.h>
#include <msctf.h>

#include <chrono>
#include <functional>

#include "tsf/input_processor_profiles.h"

namespace neural_weasel::tsf {

class BackendFallbackStateMachine {
 public:
  using Clock = std::chrono::steady_clock;

  enum class State {
    kUnarmed,
    kMonitoring,
    kCompositionCancellationRequired,
    kProfileActivationRequired,
    kFallbackActive,
    kFallbackFailed,
  };

  enum class Action {
    kNone,
    kCancelComposition,
    kActivateFallbackProfile,
  };

  explicit BackendFallbackStateMachine(
      std::chrono::milliseconds heartbeat_timeout =
          std::chrono::milliseconds(2000));

  void Arm(Clock::time_point now) noexcept;
  void OnHeartbeat(Clock::time_point now) noexcept;
  void LatchHardFailure(bool composition_active) noexcept;

  // A hard failure is latched only when elapsed time is strictly greater than
  // the timeout. Once latched, heartbeat recovery cannot switch the IME back.
  Action Evaluate(Clock::time_point now, bool composition_active) noexcept;

  void OnCompositionCancellationCompleted(HRESULT result) noexcept;
  void OnProfileActivationCompleted(HRESULT result) noexcept;

  State state() const noexcept { return state_; }
  bool hard_failure_latched() const noexcept {
    return hard_failure_latched_;
  }

 private:
  std::chrono::milliseconds heartbeat_timeout_;
  Clock::time_point last_heartbeat_{};
  State state_ = State::kUnarmed;
  bool hard_failure_latched_ = false;
};

using CancelCompositionCallback = std::function<HRESULT()>;

// Drives at most cancellation followed by activation. Call this on the TSF
// owner thread: both composition state and profile-manager COM objects are
// thread-affine. No automatic re-arm or switch-back path exists.
HRESULT DriveFallbackOnce(
    BackendFallbackStateMachine* state_machine,
    BackendFallbackStateMachine::Clock::time_point now,
    bool composition_active,
    const CancelCompositionCallback& cancel_composition,
    ITfInputProcessorProfileMgr* profile_manager,
    const InputProcessorProfile& fallback_profile);

}  // namespace neural_weasel::tsf
