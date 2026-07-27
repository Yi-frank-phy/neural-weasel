#pragma once

#include <Windows.h>
#include <msctf.h>

#include <string>
#include <vector>

namespace neural_weasel::tsf {

struct ProfileIdentity {
  DWORD profile_type = TF_PROFILETYPE_INPUTPROCESSOR;
  LANGID language_id = 0;
  CLSID text_service_clsid = CLSID_NULL;
  GUID profile_guid = GUID_NULL;
  HKL keyboard_layout = nullptr;
};

struct InputProcessorProfile {
  ProfileIdentity identity;
  GUID category = GUID_NULL;
  DWORD capabilities = 0;
  DWORD flags = 0;
  std::wstring description;

  bool enabled() const noexcept {
    return (flags & TF_IPP_FLAG_ENABLED) != 0;
  }
  bool active() const noexcept {
    return (flags & TF_IPP_FLAG_ACTIVE) != 0;
  }
};

bool SameProfile(const ProfileIdentity& left,
                 const ProfileIdentity& right) noexcept;

// The caller must initialize COM on the current thread. The returned manager
// belongs to the caller and must be released there.
HRESULT CreateInputProcessorProfileManager(
    ITfInputProcessorProfileMgr** manager);

// langid == 0 enumerates every installed profile.
HRESULT EnumerateInputProcessorProfiles(
    ITfInputProcessorProfileMgr* manager,
    LANGID language_id,
    std::vector<InputProcessorProfile>* profiles);

// Resolves a previously user-approved identity. This does not guess or persist
// a fallback choice.
HRESULT FindConfiguredProfile(
    const std::vector<InputProcessorProfile>& profiles,
    const ProfileIdentity& configured,
    InputProcessorProfile* resolved);

// Returns enabled zh-CN keyboard TIPs whose localized display name identifies
// Microsoft Pinyin. The caller must require an explicit user choice if the
// result is empty or ambiguous.
std::vector<InputProcessorProfile> DiscoverMicrosoftPinyinCandidates(
    const std::vector<InputProcessorProfile>& profiles);

// Activates an already-enabled text-service profile for the current desktop
// session. It deliberately does not pass TF_IPPMF_ENABLEPROFILE and rejects the
// experimental Neural Weasel profile as a fallback target.
HRESULT ActivateEnabledInputProcessorProfile(
    ITfInputProcessorProfileMgr* manager,
    const InputProcessorProfile& profile);

}  // namespace neural_weasel::tsf

