#include "rime/bilingual_key_semantics.h"

namespace neural_weasel::rime_plugin {

bool ShouldToggleLanguageMode(bool shift_pressed,
                              bool shift_used_as_modifier,
                              bool started_while_idle,
                              bool composing_on_release) noexcept {
  return shift_pressed && !shift_used_as_modifier && started_while_idle &&
         !composing_on_release;
}

char LatinLiteralCharacter(NeuralLanguageMode mode, int keycode) noexcept {
  if (mode != NeuralLanguageMode::kLatinFirst)
    return '\0';
  if (keycode == '-' || keycode == '=')
    return static_cast<char>(keycode);
  return '\0';
}

KeyOutcome ResolveKeyOutcome(NeuralLanguageMode mode,
                            KeyIntent intent,
                            bool has_completion,
                            bool candidate_fresh,
                            bool service_available) noexcept {
  const bool effective_completion =
      has_completion && candidate_fresh && service_available;
  if (intent == KeyIntent::kPageNext) {
    return KeyOutcome::kRequestNextPage;
  }
  if (intent == KeyIntent::kPagePrevious) {
    return KeyOutcome::kRequestPreviousPage;
  }
  if (intent == KeyIntent::kEscape) {
    return KeyOutcome::kCancelComposition;
  }
  if (intent == KeyIntent::kEnter) {
    return KeyOutcome::kCommitLiteral;
  }

  if (mode == NeuralLanguageMode::kLatinFirst) {
    switch (intent) {
      case KeyIntent::kSpace:
        return KeyOutcome::kCommitLiteralSpace;
      case KeyIntent::kTab:
        return effective_completion ? KeyOutcome::kAcceptCompletion
                                    : KeyOutcome::kKeepLiteral;
      case KeyIntent::kNumberedSelection:
        return KeyOutcome::kKeepLiteral;
      case KeyIntent::kBackspace:
      case KeyIntent::kOther:
        return KeyOutcome::kUseRimeDefault;
      case KeyIntent::kEscape:
      case KeyIntent::kEnter:
      case KeyIntent::kPageNext:
      case KeyIntent::kPagePrevious:
        break;
    }
  }
  return KeyOutcome::kUseRimeDefault;
}

}  // namespace neural_weasel::rime_plugin
