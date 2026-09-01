#include "rime/bilingual_key_semantics.h"

namespace neural_weasel::rime_plugin {

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
