#include "rime/bilingual_key_processor.h"

#include <rime/candidate.h>
#include <rime/context.h>
#include <rime/engine.h>
#include <rime/key_event.h>
#include <rime/key_table.h>

#include "rime/bilingual_key_semantics.h"

namespace neural_weasel::rime_plugin {
namespace {

InputMode CurrentInputMode(::rime::Context* context) {
  const auto value = context->get_property("neural_input_mode");
  if (value == "english") {
    return InputMode::kEnglish;
  }
  if (value == "chinese") {
    return InputMode::kChinese;
  }
  return InputMode::kAmbiguous;
}

KeyIntent IntentFor(const ::rime::KeyEvent& event) {
  switch (event.keycode()) {
    case XK_space:
      return KeyIntent::kSpace;
    case XK_Tab:
      return KeyIntent::kTab;
    case XK_Escape:
      return KeyIntent::kEscape;
    case XK_Return:
    case XK_KP_Enter:
      return KeyIntent::kEnter;
    case XK_BackSpace:
      return KeyIntent::kBackspace;
    default:
      if (event.keycode() >= XK_1 && event.keycode() <= XK_9) {
        return KeyIntent::kNumberedSelection;
      }
      return KeyIntent::kOther;
  }
}

bool IsCompletion(const ::rime::an<::rime::Candidate>& candidate) {
  return candidate && candidate->type() == "neural_latin";
}

bool IsNeuralCandidate(const ::rime::an<::rime::Candidate>& candidate) {
  return candidate && candidate->type().rfind("neural_", 0) == 0;
}

}  // namespace

::rime::ProcessResult BilingualKeyProcessor::ProcessKeyEvent(
    const ::rime::KeyEvent& key_event) {
  if (key_event.release() || key_event.ctrl() || key_event.alt() ||
      key_event.super()) {
    return ::rime::kNoop;
  }
  auto* context = engine_->context();
  if (!context || !context->IsComposing()) {
    return ::rime::kNoop;
  }

  if (key_event.keycode() == XK_BackSpace ||
      (key_event.keycode() >= 0x20 && key_event.keycode() < 0x7f)) {
    context->set_option("_neural_completion_suppressed", false);
  }

  auto selected = context->GetSelectedCandidate();
  const auto intent = IntentFor(key_event);
  const bool candidate_fresh =
      context->get_property("neural_candidate_fresh") == "1";
  const auto mode = CurrentInputMode(context);
  if (!candidate_fresh &&
      (intent == KeyIntent::kTab ||
       intent == KeyIntent::kNumberedSelection)) {
    return ::rime::kAccepted;
  }
  if (!candidate_fresh && IsNeuralCandidate(selected)) {
    if (intent == KeyIntent::kSpace) {
      const std::string suffix =
          mode == InputMode::kEnglish ? " " : "";
      engine_->CommitText(context->input() + suffix);
      context->Clear();
      return ::rime::kAccepted;
    }
  }
  const auto outcome = ResolveKeyOutcome(
      mode, intent, IsCompletion(selected), candidate_fresh,
      candidate_fresh);
  switch (outcome) {
    case KeyOutcome::kCommitLiteralSpace:
      engine_->CommitText(context->input() + " ");
      context->Clear();
      return ::rime::kAccepted;
    case KeyOutcome::kAcceptCompletion:
      engine_->CommitText(selected->text());
      context->Clear();
      return ::rime::kAccepted;
    case KeyOutcome::kDismissCompletion:
      context->set_option("_neural_completion_suppressed", true);
      context->RefreshNonConfirmedComposition();
      return ::rime::kAccepted;
    case KeyOutcome::kCommitLiteralAndForwardEnter:
      engine_->CommitText(context->input());
      context->Clear();
      return ::rime::kRejected;
    case KeyOutcome::kKeepLiteral:
      return ::rime::kAccepted;
    case KeyOutcome::kUseRimeDefault:
      return ::rime::kNoop;
  }
  return ::rime::kNoop;
}

}  // namespace neural_weasel::rime_plugin
