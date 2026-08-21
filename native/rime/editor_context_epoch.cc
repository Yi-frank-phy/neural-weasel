#include "rime/editor_context_epoch.h"

#include <utility>

namespace neural_weasel::rime_plugin {

EditorContextEpoch& EditorContextEpoch::Instance() {
  static EditorContextEpoch instance;
  return instance;
}

std::uint64_t EditorContextEpoch::Load() const noexcept {
  std::lock_guard lock(mutex_);
  return accepted_.model_epoch;
}

AcceptedEditorContext EditorContextEpoch::LoadAccepted() const noexcept {
  std::lock_guard lock(mutex_);
  return accepted_;
}

void EditorContextEpoch::Publish(std::uint64_t epoch) noexcept {
  std::lock_guard lock(mutex_);
  accepted_ = AcceptedEditorContext{epoch, {}, 0};
}

void EditorContextEpoch::Publish(AcceptedEditorContext accepted) noexcept {
  std::lock_guard lock(mutex_);
  accepted_ = std::move(accepted);
}

void EditorContextEpoch::Reset() noexcept {
  std::lock_guard lock(mutex_);
  accepted_ = {};
}

}  // namespace neural_weasel::rime_plugin
