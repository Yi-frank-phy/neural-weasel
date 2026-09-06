#pragma once

#include <cstdint>

namespace neural_weasel::rime_plugin {

// Unicode permanently reserves U+FDD0 as a noncharacter. It therefore cannot
// collide with an assignable text key while still fitting Weasel's 16-bit IPC
// keycode field.
inline constexpr std::uint16_t kNeuralRefreshKeycode = 0xFDD0;
inline constexpr char kNeuralForceRefreshProperty[] = "neural_force_refresh";

}  // namespace neural_weasel::rime_plugin
