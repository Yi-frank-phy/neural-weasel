#pragma once

#include <cstddef>

#include <inputscope.h>

namespace neural_weasel::tsf {

enum class InputScopeState {
  kNormal,
  kPrivate,
  kPassword,
};

struct InputScopePolicyResult {
  InputScopeState state = InputScopeState::kNormal;
  bool allow_prediction = true;
  bool allow_persistence = true;
  bool allow_capture = true;
};

InputScopePolicyResult ClassifyInputScopes(const InputScope* input_scopes,
                                           std::size_t input_scope_count);

}  // namespace neural_weasel::tsf
