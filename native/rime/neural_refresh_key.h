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
inline constexpr char kNeuralPresentationLockedProperty[] =
    "neural_presentation_locked";

// The initial key event has already spent at most 50 ms in the pipe. A
// retryable empty first page therefore gets a prompt, bounded pull sequence;
// published pages never enter this policy.
inline constexpr std::uint32_t kNeuralFirstPageRetryDelayMs = 25;
inline constexpr std::uint32_t kNeuralFirstPageRetryIntervalMs = 50;
inline constexpr std::uint32_t kNeuralFirstPageRetryMaxIntervalMs = 200;
inline constexpr std::uint32_t kNeuralFirstPageRetryBudgetMs = 2500;

inline constexpr bool ShouldRetryFirstPage(bool presentation_ready,
                                           std::uint64_t elapsed_ms) noexcept {
  return !presentation_ready && elapsed_ms < kNeuralFirstPageRetryBudgetMs;
}

inline constexpr std::uint32_t FirstPageRetryDelayMs(
    unsigned int attempts) noexcept {
  if (attempts == 0) {
    return kNeuralFirstPageRetryDelayMs;
  }
  const std::uint64_t backed_off =
      static_cast<std::uint64_t>(kNeuralFirstPageRetryIntervalMs) * attempts;
  return backed_off < kNeuralFirstPageRetryMaxIntervalMs
             ? static_cast<std::uint32_t>(backed_off)
             : kNeuralFirstPageRetryMaxIntervalMs;
}

}  // namespace neural_weasel::rime_plugin
