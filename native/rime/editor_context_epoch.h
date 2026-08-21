#pragma once

#include <cstdint>
#include <mutex>
#include <string>

namespace neural_weasel::rime_plugin {

struct AcceptedEditorContext {
  std::uint64_t model_epoch = 0;
  std::string source_capability;
  std::uint64_t source_revision = 0;

  bool valid() const noexcept {
    return model_epoch > 0 && source_capability.size() == 32U &&
           source_revision > 0;
  }
};

class EditorContextEpoch final {
 public:
  static EditorContextEpoch& Instance();

  // Compatibility accessor used by older boundary tests.
  std::uint64_t Load() const noexcept;
  AcceptedEditorContext LoadAccepted() const noexcept;

  // Compatibility publication for context-free/legacy tests. It intentionally
  // does not create a valid contextual identity.
  void Publish(std::uint64_t epoch) noexcept;
  void Publish(AcceptedEditorContext accepted) noexcept;
  void Reset() noexcept;

 private:
  mutable std::mutex mutex_;
  AcceptedEditorContext accepted_;
};

}  // namespace neural_weasel::rime_plugin
