#include <Windows.h>
#include <msctf.h>
#include <objbase.h>

#include <cwctype>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "tsf/experimental_profile_ids.h"

namespace {

using neural_weasel::tsf::kNeuralWeaselTextServiceClsid;
using neural_weasel::tsf::kNeuralWeaselTextServiceClsidString;
using neural_weasel::tsf::kNeuralWeaselZhCnProfileGuid;
using neural_weasel::tsf::kNeuralWeaselZhCnProfileGuidString;

constexpr LANGID kZhCn =
    MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED);

struct Options {
  std::wstring command;
  std::wstring clsid;
  std::wstring profile_guid;
  bool dry_run = false;
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

void ActivateForCurrentSession() {
  const HRESULT initialized =
      CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  if (FAILED(initialized) && initialized != RPC_E_CHANGED_MODE) {
    throw std::runtime_error("COM initialization failed");
  }

  ITfInputProcessorProfileMgr* profiles = nullptr;
  const HRESULT created = CoCreateInstance(
      CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
      IID_ITfInputProcessorProfileMgr,
      reinterpret_cast<void**>(&profiles));
  if (FAILED(created) || profiles == nullptr) {
    if (SUCCEEDED(initialized)) {
      CoUninitialize();
    }
    throw std::runtime_error("TSF profile manager is unavailable");
  }

  const HRESULT activated = profiles->ActivateProfile(
      TF_PROFILETYPE_INPUTPROCESSOR, kZhCn,
      kNeuralWeaselTextServiceClsid, kNeuralWeaselZhCnProfileGuid, nullptr,
      TF_IPPMF_FORSESSION | TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE);
  profiles->Release();
  if (SUCCEEDED(initialized)) {
    CoUninitialize();
  }

  if (activated == S_FALSE) {
    throw std::runtime_error("experimental language profile is not enabled");
  }
  if (FAILED(activated)) {
    throw std::runtime_error("current-session profile activation failed");
  }
}

void PrintUsage() {
  std::wcerr
      << L"usage: NeuralWeaselSessionActivator.exe activate "
         L"--clsid <experimental-clsid> "
         L"--profile-guid <experimental-guid> [--dry-run]\n";
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  try {
    const auto parsed = ParseOptions(argc, argv);
    if (!parsed || parsed->command != L"activate" ||
        !IsExpectedIdentity(*parsed)) {
      PrintUsage();
      std::wcerr << L"Refusing non-experimental identifier.\n";
      return 64;
    }
    if (!parsed->dry_run) {
      ActivateForCurrentSession();
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "NeuralWeaselSessionActivator: " << error.what() << "\n";
    return 1;
  }
}
