#include "context/source_context_identity.h"

#include <Windows.h>
#include <bcrypt.h>

namespace neural_weasel::context {

bool SourceContextIdentity::BeginFocus() noexcept {
  EndFocus();

  SourceContextCapability next_capability{};
  const NTSTATUS status = BCryptGenRandom(
      nullptr,
      reinterpret_cast<PUCHAR>(next_capability.data()),
      static_cast<ULONG>(next_capability.size()),
      BCRYPT_USE_SYSTEM_PREFERRED_RNG);
  if (status != 0) {
    return false;
  }

  capability_ = next_capability;
  active_ = true;
  return true;
}

std::optional<SourceContextStamp> SourceContextIdentity::Capture() noexcept {
  if (!active_) {
    return std::nullopt;
  }

  ++revision_;
  return SourceContextStamp{capability_, revision_};
}

void SourceContextIdentity::EndFocus() noexcept {
  capability_.fill(0);
  revision_ = 0;
  active_ = false;
}

void SourceContextIdentity::OnDocumentChange() noexcept {
  EndFocus();
}

void SourceContextIdentity::Deactivate() noexcept {
  EndFocus();
}

bool SourceContextIdentity::Reactivate() noexcept {
  return BeginFocus();
}

bool SourceContextIdentity::IsCurrent(
    const SourceContextStamp& stamp) const noexcept {
  return active_ && stamp.revision == revision_ &&
         stamp.capability == capability_;
}

}  // namespace neural_weasel::context
