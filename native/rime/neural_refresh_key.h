#pragma once

#include <cstdint>

namespace neural_weasel::rime_plugin {

// Unicode permanently reserves U+FDD0 as a noncharacter. It therefore cannot
// collide with an assignable text key while still fitting Weasel's 16-bit IPC
// keycode field.
inline constexpr std::uint16_t kNeuralRefreshKeycode = 0xFDD0;
inline constexpr char kNeuralPresentationRefreshProperty[] =
    "neural_presentation_refresh";
inline constexpr char kNeuralCandidatePendingProperty[] =
    "neural_candidate_pending";

// The initial key event has already spent at most 50 ms in the pipe. A
// retryable empty first page therefore gets a prompt, bounded pull sequence;
// published pages never enter this policy.
inline constexpr std::uint32_t kNeuralFirstPageRetryDelayMs = 25;
inline constexpr std::uint32_t kNeuralFirstPageRetryIntervalMs = 50;
inline constexpr unsigned int kNeuralFirstPageRetryMaxAttempts = 4;

inline constexpr bool ShouldRetryFirstPage(bool presentation_ready,
                                           unsigned int attempts) noexcept {
  return !presentation_ready && attempts < kNeuralFirstPageRetryMaxAttempts;
}

inline constexpr std::uint32_t FirstPageRetryDelayMs(
    unsigned int attempts) noexcept {
  return attempts == 0 ? kNeuralFirstPageRetryDelayMs
                       : kNeuralFirstPageRetryIntervalMs;
}

}  // namespace neural_weasel::rime_plugin
