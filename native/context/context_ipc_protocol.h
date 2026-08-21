#pragma once

#include <cstddef>
#include <cstdint>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include "context/source_context_identity.h"

namespace neural_weasel::context {

constexpr std::uint32_t kContextFrameMagic = 0x5443574eU;  // "NWCT" LE
constexpr std::uint16_t kContextFrameVersion = 1;
constexpr std::size_t kContextFrameHeaderBytes = 44;
constexpr std::uint32_t kMaxContextBeforeUtf16Units = 8192;
constexpr std::uint32_t kMaxContextAfterUtf16Units = 4096;
constexpr std::size_t kMaxContextFrameBytes =
    kContextFrameHeaderBytes +
    2U * (kMaxContextBeforeUtf16Units + kMaxContextAfterUtf16Units);

enum class ContextFrameKind : std::uint8_t {
  kContext = 1,
  kClear = 2,
};

enum class ContextScopeLabel : std::uint8_t {
  kNormal = 0,
  kPrivate = 1,
  kPassword = 2,
};

struct ContextFrame {
  ContextFrameKind kind = ContextFrameKind::kContext;
  ContextScopeLabel scope_label = ContextScopeLabel::kNormal;
  std::uint32_t source_pid = 0;
  std::uint64_t revision = 0;
  SourceContextCapability source_capability{};
  std::u16string before;
  std::u16string after;
};

enum class ContextFrameDecodeResult {
  kOk,
  kMalformed,
  kOversized,
};

enum class ContextFrameAcceptResult {
  kAccepted,
  kMalformed,
  kOversized,
  kStale,
};

bool EncodeContextFrame(const ContextFrame& frame,
                        std::vector<std::uint8_t>* output) noexcept;
ContextFrameDecodeResult DecodeContextFrame(std::string_view bytes,
                                            ContextFrame* output) noexcept;

class ContextFrameReceiver final {
 public:
  ContextFrameAcceptResult Accept(std::string_view bytes,
                                  ContextFrame* accepted = nullptr) noexcept;

  bool has_source() const noexcept { return has_source_; }
  std::uint64_t latest_revision() const noexcept { return latest_revision_; }

 private:
  SourceContextCapability latest_capability_{};
  std::uint32_t latest_source_pid_ = 0;
  std::uint64_t latest_revision_ = 0;
  bool has_source_ = false;

  // Capability identifiers are metadata, not raw editor text. Keeping retired
  // identifiers for the broker lifetime prevents an old focus from becoming a
  // "new" source again after A -> B -> A delivery reordering.
  std::set<SourceContextCapability> retired_capabilities_;
};

}  // namespace neural_weasel::context
