#include "tsf/input_scope_policy.h"

#include <inputscope.h>

#include <iostream>

int main() {
  using neural_weasel::tsf::ClassifyInputScopes;
  using neural_weasel::tsf::InputScopeState;

  const InputScope password_scope = IS_PASSWORD;
  const auto password = ClassifyInputScopes(&password_scope, 1);
  if (password.state != InputScopeState::kPassword ||
      password.allow_prediction || password.allow_persistence ||
      password.allow_capture) {
    std::cerr << "password scope was not denied\n";
    return 1;
  }

  const InputScope private_scope_value = IS_PRIVATE;
  const auto private_scope = ClassifyInputScopes(&private_scope_value, 1);
  if (private_scope.state != InputScopeState::kPrivate ||
      !private_scope.allow_prediction || private_scope.allow_persistence ||
      !private_scope.allow_capture) {
    std::cerr << "private scope classification failed\n";
    return 1;
  }

  const auto empty = ClassifyInputScopes(nullptr, 0);
  if (empty.state != InputScopeState::kNormal || !empty.allow_prediction ||
      !empty.allow_persistence || !empty.allow_capture) {
    std::cerr << "empty scope was not normal\n";
    return 1;
  }

  const InputScope unknown_scope = static_cast<InputScope>(0x7fffffff);
  const auto unknown = ClassifyInputScopes(&unknown_scope, 1);
  if (unknown.state != InputScopeState::kNormal || !unknown.allow_capture) {
    std::cerr << "unknown scope defaulted to deny\n";
    return 1;
  }

  return 0;
}
