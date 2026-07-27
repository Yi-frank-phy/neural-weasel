#pragma once

#include <Windows.h>

#include <string>
#include <vector>

#include "tsf/input_processor_profiles.h"

namespace neural_weasel::tsf {

struct ExperimentalProfileSpec {
  ProfileIdentity identity;
  std::wstring display_name;
  std::wstring icon_path;
  ULONG icon_index = 0;
};

enum class ProfilePlanAction {
  kNoOp,
  kRegisterExperimentalProfile,
  kUnregisterExperimentalProfile,
  kConflict,
};

struct ProfileMutationPlan {
  ProfilePlanAction action = ProfilePlanAction::kConflict;
  HRESULT result = E_UNEXPECTED;
  std::wstring explanation;
};

ExperimentalProfileSpec DefaultExperimentalProfileSpec();

// These functions are intentionally read-only. They make registration and
// unregistration decisions idempotent, but this skeleton contains no executor
// that calls RegisterProfile, UnregisterProfile or edits COM registry keys.
ProfileMutationPlan PlanExperimentalProfileRegistration(
    const std::vector<InputProcessorProfile>& installed_profiles,
    const ExperimentalProfileSpec& desired);

ProfileMutationPlan PlanExperimentalProfileUnregistration(
    const std::vector<InputProcessorProfile>& installed_profiles,
    const ExperimentalProfileSpec& desired);

}  // namespace neural_weasel::tsf

