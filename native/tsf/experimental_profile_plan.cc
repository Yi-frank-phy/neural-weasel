#include "tsf/experimental_profile_plan.h"

#include <algorithm>

#include "tsf/experimental_profile_ids.h"

namespace neural_weasel::tsf {
namespace {

bool HasIdentifierConflict(const InputProcessorProfile& installed,
                           const ProfileIdentity& desired) {
  if (installed.identity.profile_type != TF_PROFILETYPE_INPUTPROCESSOR) {
    return false;
  }
  const bool same_clsid =
      IsEqualGUID(installed.identity.text_service_clsid,
                  desired.text_service_clsid);
  const bool same_profile_guid =
      IsEqualGUID(installed.identity.profile_guid, desired.profile_guid);
  return same_clsid != same_profile_guid;
}

ProfileMutationPlan InvalidSpecPlan() {
  return {ProfilePlanAction::kConflict, E_INVALIDARG,
          L"Experimental profile spec does not use the reserved Neural "
          L"Weasel CLSID/profile GUID."};
}

bool IsReservedExperimentalSpec(const ExperimentalProfileSpec& spec) {
  return spec.identity.profile_type == TF_PROFILETYPE_INPUTPROCESSOR &&
         spec.identity.language_id ==
             MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED) &&
         IsEqualGUID(spec.identity.text_service_clsid,
                     kNeuralWeaselTextServiceClsid) &&
         IsEqualGUID(spec.identity.profile_guid,
                     kNeuralWeaselZhCnProfileGuid);
}

}  // namespace

ExperimentalProfileSpec DefaultExperimentalProfileSpec() {
  ExperimentalProfileSpec spec;
  spec.identity.profile_type = TF_PROFILETYPE_INPUTPROCESSOR;
  spec.identity.language_id =
      MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);
  spec.identity.text_service_clsid = kNeuralWeaselTextServiceClsid;
  spec.identity.profile_guid = kNeuralWeaselZhCnProfileGuid;
  spec.display_name =
      L"\u795e\u7ecf\u5c0f\u72fc\u6beb\uff08\u5b9e\u9a8c\uff09";
  return spec;
}

ProfileMutationPlan PlanExperimentalProfileRegistration(
    const std::vector<InputProcessorProfile>& installed_profiles,
    const ExperimentalProfileSpec& desired) {
  if (!IsReservedExperimentalSpec(desired)) {
    return InvalidSpecPlan();
  }
  for (const auto& installed : installed_profiles) {
    if (SameProfile(installed.identity, desired.identity)) {
      return {ProfilePlanAction::kNoOp, S_FALSE,
              L"The reserved experimental profile is already registered."};
    }
    if (HasIdentifierConflict(installed, desired.identity)) {
      return {ProfilePlanAction::kConflict,
              HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS),
              L"An installed profile reuses only one reserved identifier; "
              L"registration must stop without changing either profile."};
    }
  }
  return {ProfilePlanAction::kRegisterExperimentalProfile, S_OK,
          L"Register only the reserved experimental CLSID/profile GUID; keep "
          L"the existing Weasel profile untouched and disabled by default."};
}

ProfileMutationPlan PlanExperimentalProfileUnregistration(
    const std::vector<InputProcessorProfile>& installed_profiles,
    const ExperimentalProfileSpec& desired) {
  if (!IsReservedExperimentalSpec(desired)) {
    return InvalidSpecPlan();
  }
  for (const auto& installed : installed_profiles) {
    if (SameProfile(installed.identity, desired.identity)) {
      return {ProfilePlanAction::kUnregisterExperimentalProfile, S_OK,
              L"Unregister exactly the reserved experimental profile."};
    }
    if (HasIdentifierConflict(installed, desired.identity)) {
      return {ProfilePlanAction::kConflict,
              HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS),
              L"Identifier conflict detected; do not unregister anything."};
    }
  }
  return {ProfilePlanAction::kNoOp, S_FALSE,
          L"The experimental profile is already absent."};
}

}  // namespace neural_weasel::tsf
