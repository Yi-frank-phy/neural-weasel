#include <Windows.h>

#include "tsf/experimental_profile_ids.h"

extern "C" __declspec(dllexport) const wchar_t*
NeuralWeaselExperimentalClsid() noexcept {
  return neural_weasel::tsf::kNeuralWeaselTextServiceClsidString;
}

extern "C" __declspec(dllexport) const wchar_t*
NeuralWeaselExperimentalProfileGuid() noexcept {
  return neural_weasel::tsf::kNeuralWeaselZhCnProfileGuidString;
}

extern "C" __declspec(dllexport) const wchar_t*
NeuralWeaselExperimentalDisplayName() noexcept {
  return neural_weasel::tsf::kNeuralWeaselDisplayName;
}
