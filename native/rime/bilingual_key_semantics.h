#pragma once

namespace neural_weasel::rime_plugin {

enum class InputMode {
  kChinese,
  kEnglish,
  kAmbiguous,
};

enum class KeyIntent {
  kOther,
  kSpace,
  kTab,
  kEscape,
  kEnter,
  kBackspace,
  kNumberedSelection,
};

enum class KeyOutcome {
  kUseRimeDefault,
  kCommitLiteralSpace,
  kAcceptCompletion,
  kDismissCompletion,
  kCommitLiteralAndForwardEnter,
  kKeepLiteral,
};

KeyOutcome ResolveKeyOutcome(InputMode mode,
                            KeyIntent intent,
                            bool has_completion,
                            bool candidate_fresh = true,
                            bool service_available = true) noexcept;

}  // namespace neural_weasel::rime_plugin
