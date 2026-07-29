#include <Windows.h>
#include <msctf.h>
#include <objbase.h>

#include <filesystem>
#include <functional>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>
#include <cwchar>
#include <cwctype>

#include "tsf/experimental_profile_ids.h"

namespace {

using neural_weasel::tsf::kNeuralWeaselDisplayName;
using neural_weasel::tsf::kNeuralWeaselTextServiceClsid;
using neural_weasel::tsf::kNeuralWeaselTextServiceClsidString;
using neural_weasel::tsf::kNeuralWeaselTsfFileName;
using neural_weasel::tsf::kNeuralWeaselZhCnProfileGuid;
using neural_weasel::tsf::kNeuralWeaselZhCnProfileGuidString;

constexpr LANGID kZhCn =
    MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);
constexpr wchar_t kClassesRoot[] = L"Software\\Classes\\CLSID\\";
constexpr wchar_t kInprocServer[] = L"InprocServer32";

struct Options {
  std::wstring command;
  std::filesystem::path dll_path;
  std::wstring clsid;
  std::wstring profile_guid;
  bool dry_run = false;
  bool json = false;
};

struct ProfileState {
  bool exact = false;
  bool conflict = false;
};

std::wstring ToUpper(std::wstring value) {
  for (auto& character : value) {
    character = static_cast<wchar_t>(towupper(character));
  }
  return value;
}

std::optional<Options> ParseOptions(int argc, wchar_t** argv) {
  if (argc < 2) {
    return std::nullopt;
  }
  Options options;
  options.command = argv[1];
  for (int index = 2; index < argc; ++index) {
    const std::wstring_view argument = argv[index];
    if (argument == L"--dry-run") {
      options.dry_run = true;
    } else if (argument == L"--json") {
      options.json = true;
    } else if (argument == L"--dll" && index + 1 < argc) {
      options.dll_path = argv[++index];
    } else if (argument == L"--clsid" && index + 1 < argc) {
      options.clsid = argv[++index];
    } else if (argument == L"--profile-guid" && index + 1 < argc) {
      options.profile_guid = argv[++index];
    } else {
      return std::nullopt;
    }
  }
  return options;
}

bool IsExpectedIdentity(const Options& options) {
  return ToUpper(options.clsid) ==
             ToUpper(kNeuralWeaselTextServiceClsidString) &&
         ToUpper(options.profile_guid) ==
             ToUpper(kNeuralWeaselZhCnProfileGuidString);
}

std::wstring ClsidRegistryPath() {
  return std::wstring(kClassesRoot) +
         kNeuralWeaselTextServiceClsidString + L"\\" + kInprocServer;
}

std::wstring JsonEscape(std::wstring_view value) {
  std::wstring escaped;
  escaped.reserve(value.size());
  for (const wchar_t character : value) {
    if (character == L'\\' || character == L'"') {
      escaped.push_back(L'\\');
    }
    escaped.push_back(character);
  }
  return escaped;
}

std::optional<std::wstring> ReadRegisteredDll() {
  wchar_t value[32768] = {};
  DWORD bytes = sizeof(value);
  const LSTATUS status =
      RegGetValueW(HKEY_CURRENT_USER, ClsidRegistryPath().c_str(), nullptr,
                   RRF_RT_REG_SZ, nullptr, value, &bytes);
  if (status == ERROR_FILE_NOT_FOUND) {
    return std::nullopt;
  }
  if (status != ERROR_SUCCESS) {
    throw std::runtime_error("failed to read experimental COM registration");
  }
  return std::wstring(value);
}

std::filesystem::path CanonicalExistingPath(
    const std::filesystem::path& path) {
  return std::filesystem::weakly_canonical(std::filesystem::absolute(path));
}

bool SamePath(const std::filesystem::path& left,
              const std::filesystem::path& right) {
  return _wcsicmp(CanonicalExistingPath(left).c_str(),
                  CanonicalExistingPath(right).c_str()) == 0;
}

using IdentityFunction = const wchar_t* (*)();

void VerifyTsfIdentity(const std::filesystem::path& dll_path) {
  if (!std::filesystem::is_regular_file(dll_path) ||
      _wcsicmp(dll_path.filename().c_str(), kNeuralWeaselTsfFileName) != 0) {
    throw std::runtime_error("expected experimental TSF DLL is missing");
  }
  HMODULE module = LoadLibraryExW(
      CanonicalExistingPath(dll_path).c_str(), nullptr,
      LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
  if (!module) {
    throw std::runtime_error("failed to load experimental TSF DLL");
  }
  const auto clsid = reinterpret_cast<IdentityFunction>(
      GetProcAddress(module, "NeuralWeaselExperimentalClsid"));
  const auto profile = reinterpret_cast<IdentityFunction>(
      GetProcAddress(module, "NeuralWeaselExperimentalProfileGuid"));
  const auto display = reinterpret_cast<IdentityFunction>(
      GetProcAddress(module, "NeuralWeaselExperimentalDisplayName"));
  const bool valid =
      clsid && profile && display &&
      ToUpper(clsid()) == ToUpper(kNeuralWeaselTextServiceClsidString) &&
      ToUpper(profile()) == ToUpper(kNeuralWeaselZhCnProfileGuidString) &&
      std::wstring(display()) == kNeuralWeaselDisplayName;
  FreeLibrary(module);
  if (!valid) {
    throw std::runtime_error("TSF DLL identity export mismatch");
  }
}

void SetRegistryString(HKEY key,
                       const wchar_t* name,
                       const std::wstring& value) {
  const auto bytes =
      static_cast<DWORD>((value.size() + 1) * sizeof(wchar_t));
  if (RegSetValueExW(key, name, 0, REG_SZ,
                     reinterpret_cast<const BYTE*>(value.c_str()),
                     bytes) != ERROR_SUCCESS) {
    throw std::runtime_error("failed to write experimental COM registration");
  }
}

void RegisterComServer(const std::filesystem::path& dll_path) {
  HKEY key = nullptr;
  DWORD disposition = 0;
  if (RegCreateKeyExW(HKEY_CURRENT_USER, ClsidRegistryPath().c_str(), 0,
                      nullptr, REG_OPTION_NON_VOLATILE, KEY_READ | KEY_WRITE,
                      nullptr, &key, &disposition) != ERROR_SUCCESS) {
    throw std::runtime_error("failed to create experimental COM registration");
  }
  try {
    SetRegistryString(key, nullptr, CanonicalExistingPath(dll_path).wstring());
    SetRegistryString(key, L"ThreadingModel", L"Apartment");
  } catch (...) {
    RegCloseKey(key);
    throw;
  }
  RegCloseKey(key);
}

void RegisterCategories(ITfCategoryMgr* manager) {
  const GUID categories[] = {
      GUID_TFCAT_CATEGORY_OF_TIP,
      GUID_TFCAT_TIP_KEYBOARD,
      GUID_TFCAT_TIPCAP_SECUREMODE,
      GUID_TFCAT_TIPCAP_UIELEMENTENABLED,
      GUID_TFCAT_TIPCAP_INPUTMODECOMPARTMENT,
      GUID_TFCAT_TIPCAP_COMLESS,
      GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,
  };
  for (const auto& category : categories) {
    const HRESULT result =
        manager->RegisterCategory(kNeuralWeaselTextServiceClsid, category,
                                  kNeuralWeaselTextServiceClsid);
    if (FAILED(result)) {
      throw std::runtime_error("failed to register experimental TSF category");
    }
  }
}

void UnregisterCategories(ITfCategoryMgr* manager) {
  const GUID categories[] = {
      GUID_TFCAT_CATEGORY_OF_TIP,
      GUID_TFCAT_TIP_KEYBOARD,
      GUID_TFCAT_TIPCAP_SECUREMODE,
      GUID_TFCAT_TIPCAP_UIELEMENTENABLED,
      GUID_TFCAT_TIPCAP_INPUTMODECOMPARTMENT,
      GUID_TFCAT_TIPCAP_COMLESS,
      GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT,
  };
  for (const auto& category : categories) {
    manager->UnregisterCategory(kNeuralWeaselTextServiceClsid, category,
                                kNeuralWeaselTextServiceClsid);
  }
}

void WithTsfManagers(
    const std::function<void(ITfInputProcessorProfileMgr*, ITfCategoryMgr*)>&
        operation) {
  const HRESULT initialized =
      CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  if (FAILED(initialized) && initialized != RPC_E_CHANGED_MODE) {
    throw std::runtime_error("COM initialization failed");
  }
  ITfInputProcessorProfileMgr* profiles = nullptr;
  ITfCategoryMgr* categories = nullptr;
  HRESULT result = CoCreateInstance(
      CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
      IID_ITfInputProcessorProfileMgr,
      reinterpret_cast<void**>(&profiles));
  if (SUCCEEDED(result)) {
    result = CoCreateInstance(CLSID_TF_CategoryMgr, nullptr,
                              CLSCTX_INPROC_SERVER, IID_ITfCategoryMgr,
                              reinterpret_cast<void**>(&categories));
  }
  if (FAILED(result) || !profiles || !categories) {
    if (profiles) {
      profiles->Release();
    }
    if (categories) {
      categories->Release();
    }
    if (SUCCEEDED(initialized)) {
      CoUninitialize();
    }
    throw std::runtime_error("TSF profile manager is unavailable");
  }
  try {
    operation(profiles, categories);
  } catch (...) {
    profiles->Release();
    categories->Release();
    if (SUCCEEDED(initialized)) {
      CoUninitialize();
    }
    throw;
  }
  profiles->Release();
  categories->Release();
  if (SUCCEEDED(initialized)) {
    CoUninitialize();
  }
}

ProfileState ReadProfileState() {
  ProfileState state;
  WithTsfManagers(
      [&](ITfInputProcessorProfileMgr* profiles, ITfCategoryMgr*) {
        IEnumTfInputProcessorProfiles* enumerator = nullptr;
        if (FAILED(profiles->EnumProfiles(kZhCn, &enumerator)) ||
            enumerator == nullptr) {
          throw std::runtime_error(
              "failed to enumerate experimental language profiles");
        }
        TF_INPUTPROCESSORPROFILE profile{};
        ULONG fetched = 0;
        while (enumerator->Next(1, &profile, &fetched) == S_OK &&
               fetched == 1) {
          if (profile.dwProfileType != TF_PROFILETYPE_INPUTPROCESSOR) {
            continue;
          }
          const bool clsid_matches = IsEqualCLSID(
              profile.clsid, kNeuralWeaselTextServiceClsid);
          const bool guid_matches = IsEqualGUID(
              profile.guidProfile, kNeuralWeaselZhCnProfileGuid);
          state.exact = state.exact || (clsid_matches && guid_matches);
          state.conflict =
              state.conflict || (clsid_matches != guid_matches);
        }
        enumerator->Release();
      });
  return state;
}

void RegisterProfile(const std::filesystem::path& dll_path) {
  RegisterComServer(dll_path);
  try {
    WithTsfManagers(
        [&](ITfInputProcessorProfileMgr* profiles, ITfCategoryMgr* categories) {
          const auto description_length =
              static_cast<ULONG>(std::wstring_view(kNeuralWeaselDisplayName).size());
          const auto icon_path = CanonicalExistingPath(dll_path).wstring();
          const HRESULT result = profiles->RegisterProfile(
              kNeuralWeaselTextServiceClsid, kZhCn,
              kNeuralWeaselZhCnProfileGuid, kNeuralWeaselDisplayName,
              description_length, icon_path.c_str(),
              static_cast<ULONG>(icon_path.size()), 0, nullptr, 0, TRUE, 0);
          if (FAILED(result)) {
            throw std::runtime_error(
                "failed to register experimental language profile");
          }
          RegisterCategories(categories);
        });
  } catch (...) {
    try {
      WithTsfManagers(
          [](ITfInputProcessorProfileMgr* profiles,
             ITfCategoryMgr* categories) {
            profiles->UnregisterProfile(
                kNeuralWeaselTextServiceClsid, kZhCn,
                kNeuralWeaselZhCnProfileGuid, 0);
            UnregisterCategories(categories);
          });
    } catch (...) {
      // Preserve the original registration failure. The COM key is still
      // removed below and a later idempotent uninstall retries cleanup.
    }
    RegDeleteTreeW(HKEY_CURRENT_USER,
                   (std::wstring(kClassesRoot) +
                    kNeuralWeaselTextServiceClsidString)
                       .c_str());
    throw;
  }
}

void UnregisterProfile() {
  WithTsfManagers(
      [](ITfInputProcessorProfileMgr* profiles, ITfCategoryMgr* categories) {
        profiles->UnregisterProfile(kNeuralWeaselTextServiceClsid, kZhCn,
                                    kNeuralWeaselZhCnProfileGuid, 0);
        UnregisterCategories(categories);
      });
  const LSTATUS status =
      RegDeleteTreeW(HKEY_CURRENT_USER,
                     (std::wstring(kClassesRoot) +
                      kNeuralWeaselTextServiceClsidString)
                         .c_str());
  if (status != ERROR_SUCCESS && status != ERROR_FILE_NOT_FOUND) {
    throw std::runtime_error("failed to remove experimental COM registration");
  }
}

void PrintUsage() {
  std::wcerr
      << L"usage: NeuralWeaselProfileTool.exe "
         L"<verify|register|unregister|status> --clsid <experimental-clsid> "
         L"--profile-guid <experimental-guid> [--dll <path>] "
         L"[--dry-run] [--json]\n";
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  try {
    const auto parsed = ParseOptions(argc, argv);
    if (!parsed || !IsExpectedIdentity(*parsed)) {
      PrintUsage();
      std::wcerr << L"Refusing non-experimental identifier.\n";
      return 64;
    }
    const Options& options = *parsed;
    if (options.command == L"verify") {
      if (options.dll_path.empty()) {
        throw std::runtime_error("--dll is required for verification");
      }
      VerifyTsfIdentity(options.dll_path);
      return 0;
    }
    const auto registered = ReadRegisteredDll();
    const ProfileState profile_state = ReadProfileState();

    if (options.command == L"status") {
      if (options.json) {
        std::wcout << L"{\"registered\":"
                   << (registered && profile_state.exact ? L"true" : L"false")
                   << L",\"com_registered\":"
                   << (registered ? L"true" : L"false")
                   << L",\"profile_registered\":"
                   << (profile_state.exact ? L"true" : L"false")
                   << L",\"identity_conflict\":"
                   << (profile_state.conflict ? L"true" : L"false")
                   << L",\"clsid\":\""
                   << kNeuralWeaselTextServiceClsidString
                   << L"\",\"profile_guid\":\""
                   << kNeuralWeaselZhCnProfileGuidString << L"\",\"com_path\":";
        if (registered) {
          std::wcout << L"\"" << JsonEscape(*registered) << L"\"";
        } else {
          std::wcout << L"null";
        }
        std::wcout << L"}\n";
      } else {
        std::wcout << (registered && profile_state.exact
                            ? L"registered"
                            : L"not registered")
                   << L"\n";
      }
      return 0;
    }

    if (options.command == L"register") {
      if (profile_state.conflict) {
        throw std::runtime_error(
            "reserved experimental profile identity is in conflict");
      }
      if (options.dll_path.empty()) {
        throw std::runtime_error("--dll is required for registration");
      }
      VerifyTsfIdentity(options.dll_path);
      if (registered && !SamePath(*registered, options.dll_path)) {
        throw std::runtime_error(
            "experimental CLSID is already registered to a different path");
      }
      if (!options.dry_run) {
        RegisterProfile(options.dll_path);
      }
      return 0;
    }

    if (options.command == L"unregister") {
      if (profile_state.conflict) {
        throw std::runtime_error(
            "refusing unregister while reserved identity is in conflict");
      }
      if (registered && !options.dll_path.empty() &&
          !SamePath(*registered, options.dll_path)) {
        throw std::runtime_error(
            "registered experimental COM path does not match uninstall target");
      }
      if (!options.dll_path.empty() &&
          std::filesystem::exists(options.dll_path)) {
        VerifyTsfIdentity(options.dll_path);
      }
      if (!options.dry_run) {
        UnregisterProfile();
      }
      return 0;
    }

    PrintUsage();
    return 64;
  } catch (const std::exception& error) {
    std::cerr << "NeuralWeaselProfileTool: " << error.what() << "\n";
    return 1;
  }
}
