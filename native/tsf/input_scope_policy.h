#pragma once

namespace neural_weasel::tsf {

enum class InputScopeState {
  kNormal,
  kPrivate,
  kPassword,
};

struct InputScopePolicyResult {
  InputScopeState state;
  bool allow_prediction;
  bool allow_persistence;
  bool allow_capture;
};

InputScopePolicyResult ClassifyInputScope(unsigned long input_scope);

}  // namespace neural_weasel::tsf
