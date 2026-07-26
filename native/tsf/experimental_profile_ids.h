#pragma once

#include <guiddef.h>

namespace neural_weasel::tsf {

// Reserved only for the experimental Neural Weasel profile. These identifiers
// must never replace Weasel's official CLSID/profile GUIDs in an installed
// system. Registration remains intentionally outside this skeleton.
inline constexpr GUID kNeuralWeaselTextServiceClsid = {
    0x8aa66261,
    0xed5f,
    0x46b0,
    {0x89, 0x5d, 0x33, 0x9b, 0x42, 0xc3, 0xae, 0x1b}};

inline constexpr GUID kNeuralWeaselZhCnProfileGuid = {
    0xc9b3984e,
    0xa16c,
    0x4779,
    {0x80, 0xe8, 0xac, 0xd9, 0x88, 0xc5, 0x7b, 0x0d}};

}  // namespace neural_weasel::tsf

