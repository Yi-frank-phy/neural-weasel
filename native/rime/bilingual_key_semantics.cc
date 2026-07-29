#include "rime/bilingual_key_semantics.h"

namespace neural_weasel::rime_plugin {

KeyOutcome ResolveKeyOutcome(InputMode mode,
                            KeyIntent intent,
                            bool has_completion,
                            bool candidate_fresh,
                            bool service_available) noexcept {
  has_completion =
      has_completion && candidate_fresh && service_available;
  if (mode != InputMode::kEnglish && !has_completion) {
    return KeyOutcome::kUseRimeDefault;
  }
  switch (intent) {
    case KeyIntent::kSpace:
      return KeyOutcome::kCommitLiteralSpace;
    case KeyIntent::kTab:
      return has_completion ? KeyOutcome::kAcceptCompletion
                            : KeyOutcome::kKeepLiteral;
    case KeyIntent::kEscape:
      return KeyOutcome::kDismissCompletion;
    case KeyIntent::kEnter:
      return KeyOutcome::kCommitLiteralAndForwardEnter;
    case KeyIntent::kBackspace:
    case KeyIntent::kOther:
      return KeyOutcome::kUseRimeDefault;
    case KeyIntent::kNumberedSelection:
      return mode == InputMode::kEnglish ? KeyOutcome::kKeepLiteral
                                         : KeyOutcome::kUseRimeDefault;
  }
  return KeyOutcome::kUseRimeDefault;
}

}  // namespace neural_weasel::rime_plugin
