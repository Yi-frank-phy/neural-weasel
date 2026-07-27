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
                            bool has_completion) noexcept;

}  // namespace neural_weasel::rime_plugin

