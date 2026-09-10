#pragma once

#include <cstddef>

#include <inputscope.h>

namespace neural_weasel::tsf {

enum class InputScopeState {
  kUnknown,
  kNormal,
  kPrivate,
  kPassword,
};

struct InputScopePolicyResult {
  InputScopeState state = InputScopeState::kUnknown;
  bool allow_prediction = false;
  bool allow_persistence = false;
  bool allow_capture = false;
};

InputScopePolicyResult UnknownInputScopePolicy() noexcept;

InputScopePolicyResult ClassifyInputScopes(const InputScope* input_scopes,
                                           std::size_t input_scope_count);

}  // namespace neural_weasel::tsf
