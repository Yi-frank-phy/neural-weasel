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

}  // namespace neural_weasel::rime_plugin
