#include "rime/bilingual_key_semantics.h"

namespace neural_weasel::rime_plugin {

KeyOutcome ResolveKeyOutcome(InputMode mode,
                            KeyIntent intent,
                            bool has_completion) noexcept {
  if (mode != InputMode::kEnglish) {
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
    case KeyIntent::kOther:
      return KeyOutcome::kUseRimeDefault;
  }
  return KeyOutcome::kUseRimeDefault;
}

}  // namespace neural_weasel::rime_plugin
