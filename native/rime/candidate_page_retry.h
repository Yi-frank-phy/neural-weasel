#pragma once

#include <cstdint>
#include <string_view>

namespace neural_weasel::rime_plugin {

constexpr bool ShouldKeepCandidatePagePending(
    std::uint32_t requested_page,
    std::string_view error_code,
    bool retryable) noexcept {
  return requested_page == 0 && retryable &&
         error_code == "candidate_page_timeout";
}

}  // namespace neural_weasel::rime_plugin
