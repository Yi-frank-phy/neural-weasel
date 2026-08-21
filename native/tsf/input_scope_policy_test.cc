#include "tsf/input_scope_policy.h"

#include <iostream>

int main() {
  using neural_weasel::tsf::ClassifyInputScope;
  using neural_weasel::tsf::InputScopeState;

  auto password = ClassifyInputScope(0x00000001UL);
  if (password.state != InputScopeState::kPassword || password.allow_capture) {
    std::cerr << "password scope was not denied\n";
    return 1;
  }

  auto private_scope = ClassifyInputScope(0x00000002UL);
  if (private_scope.state != InputScopeState::kPrivate ||
      !private_scope.allow_prediction || private_scope.allow_persistence) {
    std::cerr << "private scope classification failed\n";
    return 1;
  }

  auto empty = ClassifyInputScope(0);
  if (empty.state != InputScopeState::kNormal) {
    std::cerr << "empty scope was not normal\n";
    return 1;
  }

  return 0;
}
