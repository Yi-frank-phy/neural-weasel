#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace neural_weasel::context {

constexpr std::size_t kMaxContextPayloadBytes = 4096;

struct ContextFrame {
  std::string source_capability;
  std::uint64_t revision = 0;
  std::string scope_label;
  std::uint32_t before_length = 0;
  std::uint32_t after_length = 0;
  std::string payload;
};

class ContextFrameReceiver {
 public:
  bool Accept(ContextFrame frame);
  const ContextFrame& last_frame() const noexcept { return last_frame_; }

 private:
  ContextFrame last_frame_;
  std::uint64_t latest_revision_ = 0;
};

bool ValidateContextFrame(const ContextFrame& frame);

}  // namespace neural_weasel::context
