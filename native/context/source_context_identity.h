#pragma once

#include <array>
#include <cstdint>
#include <optional>

namespace neural_weasel::context {

using SourceContextCapability = std::array<std::uint8_t, 16>;

struct SourceContextStamp {
  SourceContextCapability capability{};
  std::uint64_t revision = 0;
};

class SourceContextIdentity final {
 public:
  // Starts a new editor-source lifetime. The old lifetime is invalidated before
  // a new random capability is generated. On RNG failure the identity remains
  // inactive.
  bool BeginFocus() noexcept;

  // Advances the active source revision and returns the only stamp that is
  // current after this capture. An inactive source cannot be captured.
  std::optional<SourceContextStamp> Capture() noexcept;

  // Invalidates all stamps from the current source and prevents Capture from
  // making them live again.
  void EndFocus() noexcept;

  // A document transition is a source-lifetime boundary. Old captures stay
  // invalid until a new source lifetime is explicitly activated.
  void OnDocumentChange() noexcept;

  // Deactivation is also a hard invalidation boundary.
  void Deactivate() noexcept;

  // Reactivation always starts a fresh source lifetime and capability.
  bool Reactivate() noexcept;

  bool IsCurrent(const SourceContextStamp& stamp) const noexcept;

  bool active() const noexcept { return active_; }
  std::uint64_t revision() const noexcept { return revision_; }

 private:
  SourceContextCapability capability_{};
  std::uint64_t revision_ = 0;
  bool active_ = false;
};

}  // namespace neural_weasel::context
