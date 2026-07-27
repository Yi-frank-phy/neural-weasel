#include "tsf/input_processor_profiles.h"

#include <oleauto.h>

#include <algorithm>
#include <cwctype>
#include <utility>

#include "tsf/experimental_profile_ids.h"

namespace neural_weasel::tsf {
namespace {

template <typename T>
class ScopedComPtr {
 public:
  ScopedComPtr() = default;
  ScopedComPtr(const ScopedComPtr&) = delete;
  ScopedComPtr& operator=(const ScopedComPtr&) = delete;
  ~ScopedComPtr() {
    if (value_ != nullptr) {
      value_->Release();
    }
  }

  T* get() const noexcept { return value_; }
  T** put() noexcept { return &value_; }

 private:
  T* value_ = nullptr;
};

std::wstring Lowercase(std::wstring value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](wchar_t character) {
                   return static_cast<wchar_t>(std::towlower(character));
                 });
  return value;
}

bool IsMicrosoftPinyinDescription(const std::wstring& description) {
  const std::wstring lowered = Lowercase(description);
  return lowered.find(L"microsoft pinyin") != std::wstring::npos ||
         lowered.find(L"\u5fae\u8f6f\u62fc\u97f3") != std::wstring::npos;
}

HRESULT ReadDescription(ITfInputProcessorProfiles* legacy_profiles,
                        const TF_INPUTPROCESSORPROFILE& profile,
                        std::wstring* description) {
  description->clear();
  if (legacy_profiles == nullptr ||
      profile.dwProfileType != TF_PROFILETYPE_INPUTPROCESSOR) {
    return S_FALSE;
  }

  BSTR value = nullptr;
  const HRESULT result = legacy_profiles->GetLanguageProfileDescription(
      profile.clsid, profile.langid, profile.guidProfile, &value);
  if (SUCCEEDED(result) && value != nullptr) {
    description->assign(value, SysStringLen(value));
  }
  SysFreeString(value);
  return result;
}

}  // namespace

bool SameProfile(const ProfileIdentity& left,
                 const ProfileIdentity& right) noexcept {
  if (left.profile_type != right.profile_type ||
      left.language_id != right.language_id) {
    return false;
  }
  if (left.profile_type == TF_PROFILETYPE_KEYBOARDLAYOUT) {
    return left.keyboard_layout == right.keyboard_layout;
  }
  return IsEqualGUID(left.text_service_clsid, right.text_service_clsid) &&
         IsEqualGUID(left.profile_guid, right.profile_guid);
}

HRESULT CreateInputProcessorProfileManager(
    ITfInputProcessorProfileMgr** manager) {
  if (manager == nullptr) {
    return E_INVALIDARG;
  }
  *manager = nullptr;
  return CoCreateInstance(CLSID_TF_InputProcessorProfiles, nullptr,
                          CLSCTX_INPROC_SERVER,
                          IID_ITfInputProcessorProfileMgr,
                          reinterpret_cast<void**>(manager));
}

HRESULT EnumerateInputProcessorProfiles(
    ITfInputProcessorProfileMgr* manager,
    LANGID language_id,
    std::vector<InputProcessorProfile>* profiles) {
  if (manager == nullptr || profiles == nullptr) {
    return E_INVALIDARG;
  }
  profiles->clear();

  ScopedComPtr<IEnumTfInputProcessorProfiles> enumerator;
  HRESULT result = manager->EnumProfiles(language_id, enumerator.put());
  if (FAILED(result)) {
    return result;
  }

  ScopedComPtr<ITfInputProcessorProfiles> legacy_profiles;
  manager->QueryInterface(IID_ITfInputProcessorProfiles,
                          reinterpret_cast<void**>(legacy_profiles.put()));

  while (true) {
    TF_INPUTPROCESSORPROFILE native_profile{};
    ULONG fetched = 0;
    result = enumerator.get()->Next(1, &native_profile, &fetched);
    if (result == S_FALSE || fetched == 0) {
      return S_OK;
    }
    if (FAILED(result)) {
      profiles->clear();
      return result;
    }

    InputProcessorProfile profile;
    profile.identity.profile_type = native_profile.dwProfileType;
    profile.identity.language_id = native_profile.langid;
    profile.identity.text_service_clsid = native_profile.clsid;
    profile.identity.profile_guid = native_profile.guidProfile;
    profile.identity.keyboard_layout = native_profile.hkl;
    profile.category = native_profile.catid;
    profile.capabilities = native_profile.dwCaps;
    profile.flags = native_profile.dwFlags;
    ReadDescription(legacy_profiles.get(), native_profile,
                    &profile.description);
    profiles->push_back(std::move(profile));
  }
}

HRESULT FindConfiguredProfile(
    const std::vector<InputProcessorProfile>& profiles,
    const ProfileIdentity& configured,
    InputProcessorProfile* resolved) {
  if (resolved == nullptr) {
    return E_INVALIDARG;
  }
  const auto match = std::find_if(
      profiles.begin(), profiles.end(),
      [&configured](const InputProcessorProfile& candidate) {
        return SameProfile(candidate.identity, configured);
      });
  if (match == profiles.end()) {
    return HRESULT_FROM_WIN32(ERROR_NOT_FOUND);
  }
  *resolved = *match;
  return S_OK;
}

std::vector<InputProcessorProfile> DiscoverMicrosoftPinyinCandidates(
    const std::vector<InputProcessorProfile>& profiles) {
  std::vector<InputProcessorProfile> candidates;
  const LANGID simplified_chinese =
      MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);
  for (const auto& profile : profiles) {
    if (profile.identity.profile_type != TF_PROFILETYPE_INPUTPROCESSOR ||
        profile.identity.language_id != simplified_chinese ||
        !profile.enabled() ||
        !IsEqualGUID(profile.category, GUID_TFCAT_TIP_KEYBOARD) ||
        !IsMicrosoftPinyinDescription(profile.description) ||
        IsEqualGUID(profile.identity.text_service_clsid,
                    kNeuralWeaselTextServiceClsid)) {
      continue;
    }
    candidates.push_back(profile);
  }
  return candidates;
}

HRESULT ActivateEnabledInputProcessorProfile(
    ITfInputProcessorProfileMgr* manager,
    const InputProcessorProfile& profile) {
  if (manager == nullptr) {
    return E_INVALIDARG;
  }
  if (profile.identity.profile_type != TF_PROFILETYPE_INPUTPROCESSOR ||
      !profile.enabled() ||
      IsEqualGUID(profile.identity.text_service_clsid,
                  kNeuralWeaselTextServiceClsid)) {
    return E_INVALIDARG;
  }

  return manager->ActivateProfile(
      TF_PROFILETYPE_INPUTPROCESSOR, profile.identity.language_id,
      profile.identity.text_service_clsid, profile.identity.profile_guid,
      nullptr, TF_IPPMF_FORSESSION);
}

}  // namespace neural_weasel::tsf
