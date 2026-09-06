#pragma once

#include <string>
#include <string_view>

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

enum class LatinPunctuationAction {
  kNone,
  kExtendComposition,
  kCommitLiteral,
};

bool ShouldToggleLanguageMode(bool shift_pressed,
                              bool shift_used_as_modifier,
                              bool started_while_idle,
                              bool composing_on_release) noexcept;

LatinPunctuationAction ResolveLatinPunctuationAction(
    NeuralLanguageMode mode,
    char character,
    bool shifted = false) noexcept;

std::string AppendLiteralCharacter(std::string_view input, char character);

KeyOutcome ResolveKeyOutcome(NeuralLanguageMode mode,
                            KeyIntent intent,
                            bool has_completion,
                            bool candidate_fresh = true,
                            bool service_available = true) noexcept;

}  // namespace neural_weasel::rime_plugin
