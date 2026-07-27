#include "tsf/backend_fallback_state_machine.h"

namespace neural_weasel::tsf {

BackendFallbackStateMachine::BackendFallbackStateMachine(
    std::chrono::milliseconds heartbeat_timeout)
    : heartbeat_timeout_(heartbeat_timeout) {}

void BackendFallbackStateMachine::Arm(Clock::time_point now) noexcept {
  if (state_ != State::kUnarmed) {
    return;
  }
  last_heartbeat_ = now;
  state_ = State::kMonitoring;
}

void BackendFallbackStateMachine::OnHeartbeat(
    Clock::time_point now) noexcept {
  if (state_ != State::kMonitoring || now < last_heartbeat_) {
    return;
  }
  last_heartbeat_ = now;
}

void BackendFallbackStateMachine::LatchHardFailure(
    bool composition_active) noexcept {
  if (state_ != State::kMonitoring) {
    return;
  }
  hard_failure_latched_ = true;
  state_ = composition_active
               ? State::kCompositionCancellationRequired
               : State::kProfileActivationRequired;
}

BackendFallbackStateMachine::Action
BackendFallbackStateMachine::Evaluate(Clock::time_point now,
                                      bool composition_active) noexcept {
  if (state_ == State::kMonitoring &&
      now - last_heartbeat_ > heartbeat_timeout_) {
    LatchHardFailure(composition_active);
  }

  if (state_ == State::kCompositionCancellationRequired) {
    return Action::kCancelComposition;
  }
  if (state_ == State::kProfileActivationRequired) {
    // Composition state can change after the failure was latched but before
    // the TSF owner thread drives the transition. Never activate another
    // profile while a Neural Weasel composition is still active.
    if (composition_active) {
      state_ = State::kCompositionCancellationRequired;
      return Action::kCancelComposition;
    }
    return Action::kActivateFallbackProfile;
  }
  return Action::kNone;
}

void BackendFallbackStateMachine::OnCompositionCancellationCompleted(
    HRESULT result) noexcept {
  if (state_ != State::kCompositionCancellationRequired) {
    return;
  }
  state_ = result == S_OK ? State::kProfileActivationRequired
                          : State::kFallbackFailed;
}

void BackendFallbackStateMachine::OnProfileActivationCompleted(
    HRESULT result) noexcept {
  if (state_ != State::kProfileActivationRequired) {
    return;
  }
  // ActivateProfile uses S_FALSE to report an installed but disabled profile.
  state_ = result == S_OK ? State::kFallbackActive : State::kFallbackFailed;
}

HRESULT DriveFallbackOnce(
    BackendFallbackStateMachine* state_machine,
    BackendFallbackStateMachine::Clock::time_point now,
    bool composition_active,
    const CancelCompositionCallback& cancel_composition,
    ITfInputProcessorProfileMgr* profile_manager,
    const InputProcessorProfile& fallback_profile) {
  if (state_machine == nullptr || profile_manager == nullptr) {
    return E_INVALIDARG;
  }

  for (int step = 0; step < 2; ++step) {
    const auto action = state_machine->Evaluate(now, composition_active);
    if (action == BackendFallbackStateMachine::Action::kNone) {
      return S_FALSE;
    }
    if (action ==
        BackendFallbackStateMachine::Action::kCancelComposition) {
      if (!cancel_composition) {
        state_machine->OnCompositionCancellationCompleted(E_INVALIDARG);
        return E_INVALIDARG;
      }
      const HRESULT result = cancel_composition();
      state_machine->OnCompositionCancellationCompleted(result);
      if (result != S_OK) {
        return result;
      }
      composition_active = false;
      continue;
    }

    const HRESULT result =
        ActivateEnabledInputProcessorProfile(profile_manager, fallback_profile);
    state_machine->OnProfileActivationCompleted(result);
    return result;
  }
  return E_UNEXPECTED;
}

}  // namespace neural_weasel::tsf
