#include "tsf/input_scope_policy.h"

namespace neural_weasel::tsf {

InputScopePolicyResult ClassifyInputScope(unsigned long input_scope) {
  // TSF scope values are application-dependent. Unknown values intentionally
  // fall back to NORMAL rather than blocking context capture.
  if (input_scope == 0x00000001UL) {
    return {InputScopeState::kPassword, false, false, false};
  }
  if (input_scope == 0x00000002UL) {
    return {InputScopeState::kPrivate, true, false, true};
  }
  return {InputScopeState::kNormal, true, true, true};
}

}  // namespace neural_weasel::tsf
