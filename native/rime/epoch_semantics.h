#pragma once

#include <cstdint>

namespace neural_weasel::rime_plugin {

constexpr bool IsResponseEpochAcceptable(
    std::uint64_t requested_epoch,
    std::uint64_t response_epoch) noexcept {
  return requested_epoch == 0 || requested_epoch == response_epoch;
}

}  // namespace neural_weasel::rime_plugin
