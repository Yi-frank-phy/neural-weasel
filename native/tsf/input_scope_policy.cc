#include "tsf/input_scope_policy.h"

namespace neural_weasel::tsf {
namespace {

constexpr InputScopePolicyResult kNormalPolicy{
    InputScopeState::kNormal, true, true, true};
constexpr InputScopePolicyResult kPrivatePolicy{
    InputScopeState::kPrivate, true, false, true};
constexpr InputScopePolicyResult kPasswordPolicy{
    InputScopeState::kPassword, false, false, false};
constexpr InputScopePolicyResult kUnknownPolicy{
    InputScopeState::kUnknown, false, false, false};

}  // namespace

InputScopePolicyResult UnknownInputScopePolicy() noexcept {
  return kUnknownPolicy;
}

InputScopePolicyResult ClassifyInputScopes(const InputScope* input_scopes,
                                           std::size_t input_scope_count) {
  if (input_scopes == nullptr || input_scope_count == 0) {
    return kNormalPolicy;
  }

  bool saw_private = false;
  for (std::size_t i = 0; i < input_scope_count; ++i) {
    const int value = static_cast<int>(input_scopes[i]);
    if (value < static_cast<int>(IS_ENUMSTRING) ||
        value > static_cast<int>(IS_CHAT_WITHOUT_EMOJI)) {
      return kUnknownPolicy;
    }
    switch (input_scopes[i]) {
      case IS_PASSWORD:
      case IS_NUMERIC_PASSWORD:
      case IS_NUMERIC_PIN:
      case IS_ALPHANUMERIC_PIN:
      case IS_ALPHANUMERIC_PIN_SET:
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
