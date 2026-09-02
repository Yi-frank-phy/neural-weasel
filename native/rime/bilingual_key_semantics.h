#pragma once

namespace neural_weasel::rime_plugin {

enum class NeuralLanguageMode {
  kChineseFirst,
  kLatinFirst,
};

enum class KeyIntent {
  kOther,
  kSpace,
  kTab,
  kEscape,
  kEnter,
  kBackspace,
  kNumberedSelection,
  kPageNext,
  kPagePrevious,
};

enum class KeyOutcome {
  kUseRimeDefault,
  kCommitLiteralSpace,
  kAcceptCompletion,
  kCancelComposition,
  kCommitLiteral,
  kKeepLiteral,
  kRequestNextPage,
  kRequestPreviousPage,
};

KeyOutcome ResolveKeyOutcome(NeuralLanguageMode mode,
                            KeyIntent intent,
                            bool has_completion,
                            bool candidate_fresh = true,
                            bool service_available = true) noexcept;

}  // namespace neural_weasel::rime_plugin
