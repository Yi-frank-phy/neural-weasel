#pragma once

#include <guiddef.h>

namespace neural_weasel::tsf {

// Reserved only for the experimental Neural Weasel profile.
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

inline constexpr wchar_t kNeuralWeaselTextServiceClsidString[] =
    L"{8AA66261-ED5F-46B0-895D-339B42C3AE1B}";
inline constexpr wchar_t kNeuralWeaselZhCnProfileGuidString[] =
    L"{C9B3984E-A16C-4779-80E8-ACD988C57B0D}";
inline constexpr wchar_t kNeuralWeaselDisplayName[] =
    L"\x795e\x7ecf\x5c0f\x72fc\x6beb\xff08\x5b9e\x9a8c\xff09";
inline constexpr wchar_t kNeuralWeaselTsfFileName[] =
    L"NeuralWeaselExperimentalTSF.dll";
inline constexpr wchar_t kNeuralWeaselServerFileName[] =
    L"NeuralWeaselServer.exe";
inline constexpr wchar_t kNeuralWeaselProfileToolFileName[] =
    L"NeuralWeaselProfileTool.exe";
inline constexpr wchar_t kNeuralWeaselIpcPipeStem[] =
    L"NeuralWeaselExperimentalIPC";
inline constexpr wchar_t kNeuralModelPipeStem[] = L"NeuralWeasel-v1-";
inline constexpr wchar_t kNeuralWeaselRegistryRoot[] =
    L"Software\\NeuralWeasel\\Experimental";

}  // namespace neural_weasel::tsf

