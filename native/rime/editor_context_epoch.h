#pragma once

#include <atomic>
#include <cstdint>

namespace neural_weasel::rime_plugin {

// WeaselServer's extended IPC handler publishes the epoch only after the
// context_update has been accepted by the model service. The translator reads
// an immutable scalar and never touches editor text.
class EditorContextEpoch final {
 public:
  static EditorContextEpoch& Instance();

  std::uint64_t Load() const noexcept;
  void Publish(std::uint64_t epoch) noexcept;
  void Reset() noexcept;

 private:
  std::atomic<std::uint64_t> epoch_{0};
};

}  // namespace neural_weasel::rime_plugin

