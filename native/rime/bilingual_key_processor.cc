#include "rime/bilingual_key_processor.h"

#include <algorithm>
#include <cstdint>
#include <string>

#include <rime/candidate.h>
#include <rime/context.h>
#include <rime/engine.h>
#include <rime/key_event.h>
#include <rime/key_table.h>

#include "rime/bilingual_key_semantics.h"

namespace neural_weasel::rime_plugin {
namespace {

NeuralLanguageMode CurrentLanguageMode(::rime::Context* context) {
  if (context->get_property("neural_language_mode") == "latin_first") {
    return NeuralLanguageMode::kLatinFirst;
  }
  context->set_property("neural_language_mode", "chinese_first");
  return NeuralLanguageMode::kChineseFirst;
}

void SetLanguageMode(::rime::Context* context, NeuralLanguageMode mode) {
  context->set_property(
      "neural_language_mode",
      mode == NeuralLanguageMode::kLatinFirst ? "latin_first" :
                                                "chinese_first");
}

std::uint32_t CurrentPage(::rime::Context* context) {
  try {
    const std::string raw = context->get_property("neural_page_index");
    return raw.empty() ? 0U : static_cast<std::uint32_t>(std::stoul(raw));
  } catch (...) {
    return 0;
  }
}

bool IsShiftKey(const ::rime::KeyEvent& event) {
  return event.keycode() == XK_Shift_L || event.keycode() == XK_Shift_R;
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
    case XK_Page_Down:
    case XK_equal:
      return KeyIntent::kPageNext;
    case XK_Page_Up:
    case XK_minus:
      return KeyIntent::kPagePrevious;
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

std::size_t SelectedIndex(::rime::Context* context) {
  const auto& composition = context->composition();
  return composition.empty() ? 0 : composition.back().selected_index;
}

void RefreshPage(::rime::Context* context,
                 std::uint32_t target_page,
                 std::size_t selected_index) {
  context->set_property("neural_requested_page", std::to_string(target_page));
  context->RefreshNonConfirmedComposition();
  if (context->IsComposing()) {
    context->Highlight(selected_index);
  }
}

}  // namespace

::rime::ProcessResult BilingualKeyProcessor::ProcessKeyEvent(
    const ::rime::KeyEvent& key_event) {
  auto* context = engine_->context();
  if (!context) {
    return ::rime::kNoop;
  }

  if (IsShiftKey(key_event)) {
    if (key_event.ctrl() || key_event.alt() || key_event.super()) {
      shift_pressed_ = false;
      shift_used_as_modifier_ = false;
      return ::rime::kNoop;
    }
    if (!key_event.release()) {
      shift_pressed_ = true;
      shift_used_as_modifier_ = false;
      return ::rime::kAccepted;
    }
    const bool toggle = shift_pressed_ && !shift_used_as_modifier_;
    shift_pressed_ = false;
    shift_used_as_modifier_ = false;
    if (!toggle) {
      return ::rime::kAccepted;
    }
    const auto current = CurrentLanguageMode(context);
    SetLanguageMode(
        context, current == NeuralLanguageMode::kChineseFirst
                     ? NeuralLanguageMode::kLatinFirst
                     : NeuralLanguageMode::kChineseFirst);
    context->set_property("neural_requested_page", "0");
    context->set_property("neural_page_index", "0");
    context->set_property("neural_has_more", "0");
    context->set_property("neural_candidate_fresh", "0");
    if (context->IsComposing()) {
      context->RefreshNonConfirmedComposition();
    }
    return ::rime::kAccepted;
  }

  if (key_event.release()) {
    return ::rime::kNoop;
  }
  if (shift_pressed_) {
    shift_used_as_modifier_ = true;
  }
  if (key_event.ctrl() || key_event.alt() || key_event.super()) {
    return ::rime::kNoop;
  }
  if (!context->IsComposing()) {
    return ::rime::kNoop;
  }

  const auto intent = IntentFor(key_event);
  if (intent == KeyIntent::kBackspace) {
    context->set_property("neural_requested_page", "0");
    context->set_property("neural_page_index", "0");
    context->set_property("neural_has_more", "0");
    context->set_property("neural_candidate_fresh", "0");
    return ::rime::kNoop;
  }

  const auto mode = CurrentLanguageMode(context);
  auto selected = context->GetSelectedCandidate();
  const bool candidate_fresh =
      context->get_property("neural_candidate_fresh") == "1";
  const auto outcome = ResolveKeyOutcome(
      mode, intent, IsCompletion(selected), candidate_fresh, candidate_fresh);
  switch (outcome) {
    case KeyOutcome::kCommitLiteralSpace:
      engine_->CommitText(context->input() + " ");
      context->Clear();
      return ::rime::kAccepted;
    case KeyOutcome::kAcceptCompletion:
      if (selected && candidate_fresh) {
        engine_->CommitText(selected->text());
        context->Clear();
      }
      return ::rime::kAccepted;
    case KeyOutcome::kCancelComposition:
      context->Clear();
      return ::rime::kAccepted;
    case KeyOutcome::kCommitLiteral:
      engine_->CommitText(context->input());
      context->Clear();
      return ::rime::kAccepted;
    case KeyOutcome::kKeepLiteral:
      if (intent == KeyIntent::kNumberedSelection &&
          key_event.keycode() >= XK_0 && key_event.keycode() <= XK_9) {
        context->PushInput(static_cast<char>(key_event.keycode()));
        context->BeginEditing();
      }
      return ::rime::kAccepted;
    case KeyOutcome::kRequestNextPage: {
      if (context->get_property("neural_has_more") != "1") {
        return ::rime::kAccepted;
      }
      const std::uint32_t current = CurrentPage(context);
      RefreshPage(context, current + 1U, SelectedIndex(context));
      return ::rime::kAccepted;
    }
    case KeyOutcome::kRequestPreviousPage: {
      const std::uint32_t current = CurrentPage(context);
      if (current == 0) {
        return ::rime::kAccepted;
      }
      RefreshPage(context, current - 1U, SelectedIndex(context));
      return ::rime::kAccepted;
    }
    case KeyOutcome::kUseRimeDefault:
      return ::rime::kNoop;
  }
  return ::rime::kNoop;
}

}  // namespace neural_weasel::rime_plugin
