#include "rime/bilingual_key_semantics.h"

#include <iostream>

int main() {
  using neural_weasel::rime_plugin::InputMode;
  using neural_weasel::rime_plugin::KeyIntent;
  using neural_weasel::rime_plugin::KeyOutcome;
  using neural_weasel::rime_plugin::ResolveKeyOutcome;

  if (ResolveKeyOutcome(InputMode::kEnglish, KeyIntent::kSpace, true) !=
      KeyOutcome::kCommitLiteralSpace) {
    std::cerr << "English Space did not preserve the literal prefix\n";
    return 1;
  }
  if (ResolveKeyOutcome(InputMode::kEnglish, KeyIntent::kTab, true) !=
      KeyOutcome::kAcceptCompletion) {
    std::cerr << "English Tab did not accept completion\n";
    return 1;
  }
  if (ResolveKeyOutcome(InputMode::kEnglish, KeyIntent::kEscape, true) !=
      KeyOutcome::kDismissCompletion) {
    std::cerr << "English Escape replaced or removed literal input\n";
    return 1;
  }
  if (ResolveKeyOutcome(InputMode::kEnglish, KeyIntent::kEnter, true) !=
      KeyOutcome::kCommitLiteralAndForwardEnter) {
    std::cerr << "English Enter did not preserve editor Enter behavior\n";
    return 1;
  }
  if (ResolveKeyOutcome(InputMode::kChinese, KeyIntent::kSpace, true) !=
      KeyOutcome::kUseRimeDefault) {
    std::cerr << "Chinese Space bypassed conventional Rime selection\n";
    return 1;
  }
  if (ResolveKeyOutcome(InputMode::kEnglish, KeyIntent::kTab, false) !=
      KeyOutcome::kKeepLiteral) {
    std::cerr << "Tab without a completion did not keep literal text\n";
    return 1;
  }
  return 0;
}
