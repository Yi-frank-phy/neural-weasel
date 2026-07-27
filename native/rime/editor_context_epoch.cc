#include "rime/editor_context_epoch.h"

namespace neural_weasel::rime_plugin {

EditorContextEpoch& EditorContextEpoch::Instance() {
  static EditorContextEpoch instance;
  return instance;
}

std::uint64_t EditorContextEpoch::Load() const noexcept {
  return epoch_.load(std::memory_order_acquire);
}

void EditorContextEpoch::Publish(std::uint64_t epoch) noexcept {
  epoch_.store(epoch, std::memory_order_release);
}

void EditorContextEpoch::Reset() noexcept {
  epoch_.store(0, std::memory_order_release);
}

}  // namespace neural_weasel::rime_plugin

