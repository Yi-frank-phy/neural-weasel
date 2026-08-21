#include "context/context_capture_broker.h"

#include <Windows.h>
#include <sddl.h>

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "context/context_ipc_protocol.h"
#include "context/context_update_bridge.h"

#ifndef PIPE_REJECT_REMOTE_CLIENTS
#define PIPE_REJECT_REMOTE_CLIENTS 0x00000008
#endif

namespace neural_weasel::context {
namespace {

constexpr wchar_t kContextPipePrefix[] =
    L"\\\\.\\pipe\\NeuralWeaselContext-v1-";

struct SecurityDescriptor final {
  PSECURITY_DESCRIPTOR value = nullptr;
  SECURITY_ATTRIBUTES attributes{};

  ~SecurityDescriptor() {
    if (value != nullptr) {
      LocalFree(value);
    }
  }
};

std::wstring CurrentUserSid() noexcept {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
    return {};
  }

  DWORD required = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &required);
  if (required == 0) {
    CloseHandle(token);
    return {};
  }
  std::vector<std::uint8_t> buffer(required);
  if (!GetTokenInformation(
          token, TokenUser, buffer.data(), required, &required)) {
    CloseHandle(token);
    return {};
  }
  CloseHandle(token);

  const auto* user = reinterpret_cast<const TOKEN_USER*>(buffer.data());
  LPWSTR sid = nullptr;
  if (!ConvertSidToStringSidW(user->User.Sid, &sid) || sid == nullptr) {
    return {};
  }
  std::wstring result(sid);
  LocalFree(sid);
  return result;
}

std::wstring ContextPipeName() noexcept {
  const std::wstring sid = CurrentUserSid();
  return sid.empty() ? std::wstring{} : std::wstring(kContextPipePrefix) + sid;
}

bool BuildCurrentUserSecurity(SecurityDescriptor* output) noexcept {
  if (output == nullptr) {
    return false;
  }
  const std::wstring sid = CurrentUserSid();
  if (sid.empty()) {
    return false;
  }
  const std::wstring sddl = L"D:P(A;;GA;;;" + sid + L")";
  if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
          sddl.c_str(), SDDL_REVISION_1, &output->value, nullptr)) {
    return false;
  }
  output->attributes.nLength = sizeof(SECURITY_ATTRIBUTES);
  output->attributes.lpSecurityDescriptor = output->value;
  output->attributes.bInheritHandle = FALSE;
  return true;
}

HANDLE CreateContextPipe(std::wstring_view name, bool first) noexcept {
  SecurityDescriptor security;
  if (name.empty() || !BuildCurrentUserSecurity(&security)) {
    return INVALID_HANDLE_VALUE;
  }
  DWORD open_mode = PIPE_ACCESS_INBOUND;
  if (first) {
    open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE;
  }
  return CreateNamedPipeW(
      std::wstring(name).c_str(), open_mode,
      PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT |
          PIPE_REJECT_REMOTE_CLIENTS,
      PIPE_UNLIMITED_INSTANCES, 4096,
      static_cast<DWORD>(kMaxContextFrameBytes), 0, &security.attributes);
}

std::string CapabilityHex(const SourceContextCapability& capability) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string output;
  output.resize(capability.size() * 2U);
  for (std::size_t index = 0; index < capability.size(); ++index) {
    const std::uint8_t byte = capability[index];
    output[2U * index] = kHex[(byte >> 4U) & 0x0fU];
    output[2U * index + 1U] = kHex[byte & 0x0fU];
  }
  return output;
}

std::wstring Utf16ToWide(std::u16string_view text) {
  std::wstring output;
  output.reserve(text.size());
  for (char16_t unit : text) {
    output.push_back(static_cast<wchar_t>(unit));
  }
  return output;
}

EditorSecurityLabel BridgeLabel(ContextScopeLabel label) noexcept {
  switch (label) {
    case ContextScopeLabel::kPrivate:
      return EditorSecurityLabel::kPrivate;
    case ContextScopeLabel::kPassword:
      return EditorSecurityLabel::kPassword;
    case ContextScopeLabel::kNormal:
    default:
      return EditorSecurityLabel::kNormal;
  }
}

}  // namespace

struct ContextCaptureBroker::Impl final {
  std::wstring pipe_name;
  std::atomic<bool> stopping{false};
  std::thread listener;
  std::mutex clients_mutex;
  std::vector<std::thread> clients;
  std::mutex receiver_mutex;
  ContextFrameReceiver receiver;
  std::unique_ptr<ContextUpdateBridge> bridge;

  void Forward(ContextFrame frame) noexcept {
    if (bridge == nullptr) {
      return;
    }

    const std::string capability = CapabilityHex(frame.source_capability);
    if (frame.kind == ContextFrameKind::kClear) {
      bridge->Invalidate();
      if (frame.scope_label == ContextScopeLabel::kPassword) {
        tsf::SurroundingTextSnapshot snapshot;
        snapshot.result = E_ACCESSDENIED;
        ContextUpdateMetadata metadata;
        metadata.application_id =
            L"pid:" + std::to_wstring(frame.source_pid);
        metadata.session_id = capability;
        metadata.source_capability = capability;
        metadata.source_revision = frame.revision;
        metadata.security_label = EditorSecurityLabel::kPassword;
        metadata.secure = true;
        metadata.partial = true;
        bridge->Submit(std::move(snapshot), std::move(metadata));
      }
      return;
    }

    tsf::SurroundingTextSnapshot snapshot;
    snapshot.before = Utf16ToWide(frame.before);
    snapshot.after = Utf16ToWide(frame.after);
    snapshot.partial = true;
    snapshot.result = S_OK;

    ContextUpdateMetadata metadata;
    metadata.application_id = L"pid:" + std::to_wstring(frame.source_pid);
    metadata.session_id = capability;
    metadata.source_capability = capability;
    metadata.source_revision = frame.revision;
    metadata.security_label = BridgeLabel(frame.scope_label);
    metadata.secure = false;
    metadata.partial = true;
    bridge->Submit(std::move(snapshot), std::move(metadata));
  }

  void ServeClient(HANDLE pipe) noexcept {
    try {
      while (!stopping.load(std::memory_order_acquire)) {
        std::vector<std::uint8_t> bytes(kMaxContextFrameBytes);
        DWORD read = 0;
        if (!ReadFile(
                pipe, bytes.data(), static_cast<DWORD>(bytes.size()), &read,
                nullptr)) {
          break;
        }
        if (read == 0) {
          break;
        }
        bytes.resize(read);

        ULONG client_pid = 0;
        if (!GetNamedPipeClientProcessId(pipe, &client_pid) || client_pid == 0) {
          break;
        }

        const std::string_view view(
            reinterpret_cast<const char*>(bytes.data()), bytes.size());
        ContextFrame decoded;
        if (DecodeContextFrame(view, &decoded) != ContextFrameDecodeResult::kOk ||
            decoded.source_pid != static_cast<std::uint32_t>(client_pid)) {
          continue;
        }

        ContextFrame accepted;
        ContextFrameAcceptResult result;
        {
          std::lock_guard lock(receiver_mutex);
          result = receiver.Accept(view, &accepted);
        }
        if (result == ContextFrameAcceptResult::kAccepted) {
          Forward(std::move(accepted));
        }
      }
    } catch (...) {
    }
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
  }

  void AddClient(HANDLE pipe) {
    std::lock_guard lock(clients_mutex);
    clients.emplace_back([this, pipe] { ServeClient(pipe); });
  }

  void Listen(HANDLE first_pipe) noexcept {
    HANDLE pipe = first_pipe;
    while (!stopping.load(std::memory_order_acquire)) {
      bool connected = ConnectNamedPipe(pipe, nullptr) != FALSE;
      if (!connected && GetLastError() == ERROR_PIPE_CONNECTED) {
        connected = true;
      }

      if (connected && !stopping.load(std::memory_order_acquire)) {
        try {
          AddClient(pipe);
          pipe = INVALID_HANDLE_VALUE;
        } catch (...) {
          DisconnectNamedPipe(pipe);
          CloseHandle(pipe);
          pipe = INVALID_HANDLE_VALUE;
        }
      } else if (pipe != INVALID_HANDLE_VALUE) {
        CloseHandle(pipe);
        pipe = INVALID_HANDLE_VALUE;
      }

      if (stopping.load(std::memory_order_acquire)) {
        break;
      }
      pipe = CreateContextPipe(pipe_name, false);
      if (pipe == INVALID_HANDLE_VALUE) {
        Sleep(5);
      }
    }
    if (pipe != INVALID_HANDLE_VALUE) {
      CloseHandle(pipe);
    }
  }
};

ContextCaptureBroker::ContextCaptureBroker() = default;

ContextCaptureBroker::~ContextCaptureBroker() {
  Stop();
}

bool ContextCaptureBroker::Start() noexcept {
  if (impl_ != nullptr) {
    return true;
  }
  try {
    auto impl = std::make_unique<Impl>();
    impl->pipe_name = ContextPipeName();
    if (impl->pipe_name.empty()) {
      return false;
    }

    HANDLE first_pipe = CreateContextPipe(impl->pipe_name, true);
    if (first_pipe == INVALID_HANDLE_VALUE) {
      // FILE_FLAG_FIRST_PIPE_INSTANCE makes predictable-name squatting a hard
      // startup failure instead of silently accepting an attacker endpoint.
      return false;
    }

    impl->bridge = std::make_unique<ContextUpdateBridge>(
        std::make_unique<NamedPipeContextUpdateTransport>());
    impl->listener =
        std::thread([raw = impl.get(), first_pipe] { raw->Listen(first_pipe); });
    impl_ = std::move(impl);
    return true;
  } catch (...) {
    return false;
  }
}

void ContextCaptureBroker::Stop() noexcept {
  std::unique_ptr<Impl> impl = std::move(impl_);
  if (impl == nullptr) {
    return;
  }

  impl->stopping.store(true, std::memory_order_release);
  if (impl->listener.joinable()) {
    CancelSynchronousIo(impl->listener.native_handle());
    HANDLE wake = CreateFileW(
        impl->pipe_name.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0,
        nullptr);
    if (wake != INVALID_HANDLE_VALUE) {
      CloseHandle(wake);
    }
    impl->listener.join();
  }

  {
    std::lock_guard lock(impl->clients_mutex);
    for (auto& client : impl->clients) {
      if (client.joinable()) {
        CancelSynchronousIo(client.native_handle());
      }
    }
  }
  for (auto& client : impl->clients) {
    if (client.joinable()) {
      client.join();
    }
  }

  if (impl->bridge != nullptr) {
    impl->bridge->Stop();
  }
}

}  // namespace neural_weasel::context
