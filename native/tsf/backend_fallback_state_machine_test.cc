#include "tsf/backend_fallback_state_machine.h"

#include <chrono>
#include <iostream>

int main() {
  using neural_weasel::tsf::BackendFallbackStateMachine;

  BackendFallbackStateMachine machine;
  const auto now = BackendFallbackStateMachine::Clock::now();
  machine.Arm(now);
  machine.LatchHardFailure(false);

  if (machine.Evaluate(now, true) !=
      BackendFallbackStateMachine::Action::kCancelComposition) {
    std::cerr << "active composition was not cancelled before fallback\n";
    return 1;
  }
  machine.OnCompositionCancellationCompleted(S_OK);
  if (machine.Evaluate(now, false) !=
      BackendFallbackStateMachine::Action::kActivateFallbackProfile) {
    std::cerr << "fallback profile was not activated after cancellation\n";
    return 1;
  }
  return 0;
}

