#pragma once

#include <cstddef>
#include <string>

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
  kPunctuation,
  kPageNext,
  kPagePrevious,
};

enum class KeyOutcome {
  kUseRimeDefault,
  kCommitLiteralSpace,
  kAcceptCompletionSpace,
  kAcceptCompletion,
  kCancelComposition,
  kCommitLiteral,
  kKeepLiteral,
  kRequestNextPage,
  kRequestPreviousPage,
  kCommitBoundary,
};

bool ShouldToggleLanguageMode(bool shift_pressed,
                              bool shift_used_as_modifier,
                              bool started_while_idle,
                              bool composing_on_release) noexcept;

char LatinLiteralCharacter(NeuralLanguageMode mode, int keycode) noexcept;

char BoundaryPunctuationCharacter(int keycode) noexcept;

bool PresentationMayRefresh(bool pending,
                            bool presentation_locked,
                            std::size_t selected_index) noexcept;

std::string BoundaryCommitText(const std::string& raw_input,
                               const std::string& selected_text,
                               std::size_t selected_start,
                               std::size_t selected_end,
                               bool candidate_fresh,
                               char punctuation);

KeyOutcome ResolveKeyOutcome(NeuralLanguageMode mode,
                            KeyIntent intent,
                            bool has_completion,
                            bool candidate_fresh = true,
                            bool service_available = true,
                            bool completion_explicitly_selected = false) noexcept;

}  // namespace neural_weasel::rime_plugin
