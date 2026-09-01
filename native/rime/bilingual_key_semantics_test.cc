#include "rime/bilingual_key_semantics.h"

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#ifndef NEURAL_WEASEL_KEY_FIXTURE_PATH
#error "NEURAL_WEASEL_KEY_FIXTURE_PATH must point to the shared TSV fixture"
#endif

namespace {

using neural_weasel::rime_plugin::KeyIntent;
using neural_weasel::rime_plugin::KeyOutcome;
using neural_weasel::rime_plugin::NeuralLanguageMode;

std::vector<std::string> Split(const std::string& line) {
  std::vector<std::string> fields;
  std::istringstream stream(line);
  std::string field;
  while (std::getline(stream, field, '\t')) {
    fields.push_back(field);
  }
  return fields;
}

NeuralLanguageMode ParseMode(const std::string& value) {
  return value == "latin_first" ? NeuralLanguageMode::kLatinFirst
                                : NeuralLanguageMode::kChineseFirst;
}

KeyIntent ParseIntent(const std::string& value) {
  if (value == "space")
    return KeyIntent::kSpace;
  if (value == "tab")
    return KeyIntent::kTab;
  if (value == "escape")
    return KeyIntent::kEscape;
  if (value == "enter")
    return KeyIntent::kEnter;
  if (value == "backspace")
    return KeyIntent::kBackspace;
  if (value == "numbered_selection")
    return KeyIntent::kNumberedSelection;
  if (value == "page_next")
    return KeyIntent::kPageNext;
  if (value == "page_previous")
    return KeyIntent::kPagePrevious;
  return KeyIntent::kOther;
}

std::string ObservableOutcome(NeuralLanguageMode mode,
                              KeyIntent intent,
                              bool effective_completion,
                              KeyOutcome outcome) {
  switch (outcome) {
    case KeyOutcome::kCommitLiteralSpace:
      return "commit_literal_space";
    case KeyOutcome::kAcceptCompletion:
      return "accept_completion";
    case KeyOutcome::kCancelComposition:
      return "cancel";
    case KeyOutcome::kCommitLiteral:
      return "commit_literal";
    case KeyOutcome::kKeepLiteral:
      return "keep_literal";
    case KeyOutcome::kRequestNextPage:
      return "page_next";
    case KeyOutcome::kRequestPreviousPage:
      return "page_previous";
    case KeyOutcome::kUseRimeDefault:
      if (mode == NeuralLanguageMode::kChineseFirst &&
          intent == KeyIntent::kSpace)
        return "commit_selected";
      if (intent == KeyIntent::kBackspace)
        return "update_literal";
      if (intent == KeyIntent::kNumberedSelection)
        return "commit_numbered";
      return effective_completion ? "use_rime_default" : "keep_literal";
  }
  return "unknown";
}

}  // namespace

int main() {
  std::ifstream fixture(NEURAL_WEASEL_KEY_FIXTURE_PATH);
  if (!fixture) {
    std::cerr << "shared key fixture is unavailable\n";
    return 1;
  }

  std::string line;
  int checked = 0;
  while (std::getline(fixture, line)) {
    if (line.empty() || line.front() == '#')
      continue;
    const auto fields = Split(line);
    if (fields.size() != 8) {
      std::cerr << "malformed key fixture row: " << line << "\n";
      return 1;
    }
    const auto mode = ParseMode(fields[1]);
    const auto intent = ParseIntent(fields[2]);
    const bool has_completion = fields[3] == "1" && fields[4] == "1";
    const bool candidate_fresh = fields[5] == "1";
    const bool service_available = fields[6] == "1";
    const bool effective_completion =
        has_completion && candidate_fresh && service_available;
    const auto outcome = neural_weasel::rime_plugin::ResolveKeyOutcome(
        mode, intent, has_completion, candidate_fresh, service_available);
    const auto observed =
        ObservableOutcome(mode, intent, effective_completion, outcome);
    if (observed != fields[7]) {
      std::cerr << fields[0] << ": expected " << fields[7] << ", got "
                << observed << "\n";
      return 1;
    }
    ++checked;
  }
  if (checked < 16) {
    std::cerr << "shared key fixture did not cover all required vectors\n";
    return 1;
  }
  return 0;
}
