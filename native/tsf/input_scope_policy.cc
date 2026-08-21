#include "tsf/input_scope_policy.h"

namespace neural_weasel::tsf {
namespace {

constexpr InputScopePolicyResult kNormalPolicy{
    InputScopeState::kNormal, true, true, true};
constexpr InputScopePolicyResult kPrivatePolicy{
    InputScopeState::kPrivate, true, false, true};
constexpr InputScopePolicyResult kPasswordPolicy{
    InputScopeState::kPassword, false, false, false};

}  // namespace

InputScopePolicyResult ClassifyInputScopes(const InputScope* input_scopes,
                                           std::size_t input_scope_count) {
  if (input_scopes == nullptr || input_scope_count == 0) {
    return kNormalPolicy;
  }

  bool saw_private = false;
  for (std::size_t i = 0; i < input_scope_count; ++i) {
    switch (input_scopes[i]) {
      case IS_PASSWORD:
        return kPasswordPolicy;
      case IS_PRIVATE:
        saw_private = true;
        break;
      default:
        break;
    }
  }

  return saw_private ? kPrivatePolicy : kNormalPolicy;
}

}  // namespace neural_weasel::tsf
