#include "context_ipc_protocol.h"

#include <utility>

namespace neural_weasel::context {
namespace {

constexpr std::size_t kMagicOffset = 0;
constexpr std::size_t kVersionOffset = 4;
constexpr std::size_t kKindOffset = 6;
constexpr std::size_t kScopeOffset = 7;
constexpr std::size_t kSourcePidOffset = 8;
constexpr std::size_t kRevisionOffset = 12;
constexpr std::size_t kCapabilityOffset = 20;
constexpr std::size_t kBeforeLengthOffset = 36;
constexpr std::size_t kAfterLengthOffset = 40;

bool IsKnownKind(ContextFrameKind kind) noexcept {
  return kind == ContextFrameKind::kContext || kind == ContextFrameKind::kClear;
}

bool IsKnownScope(ContextScopeLabel label) noexcept {
  return label == ContextScopeLabel::kNormal ||
         label == ContextScopeLabel::kPrivate ||
         label == ContextScopeLabel::kPassword;
}

bool HasCapability(const SourceContextCapability& capability) noexcept {
  for (std::uint8_t byte : capability) {
    if (byte != 0) {
      return true;
    }
  }
  return false;
}

bool IsValidUtf16(std::u16string_view text) noexcept {
  for (std::size_t index = 0; index < text.size(); ++index) {
    const std::uint16_t unit = static_cast<std::uint16_t>(text[index]);
    if (unit >= 0xd800U && unit <= 0xdbffU) {
      if (index + 1 >= text.size()) {
        return false;
      }
      const std::uint16_t low =
          static_cast<std::uint16_t>(text[index + 1]);
      if (low < 0xdc00U || low > 0xdfffU) {
        return false;
      }
      ++index;
    } else if (unit >= 0xdc00U && unit <= 0xdfffU) {
      return false;
    }
  }
  return true;
}

bool ValidateFrame(const ContextFrame& frame) noexcept {
  if (!IsKnownKind(frame.kind) || !IsKnownScope(frame.scope_label) ||
      frame.source_pid == 0 || frame.revision == 0 ||
      !HasCapability(frame.source_capability)) {
    return false;
  }
  if (frame.before.size() > kMaxContextBeforeUtf16Units ||
      frame.after.size() > kMaxContextAfterUtf16Units ||
      !IsValidUtf16(frame.before) || !IsValidUtf16(frame.after)) {
    return false;
  }
  if (frame.kind == ContextFrameKind::kClear &&
      (!frame.before.empty() || !frame.after.empty())) {
    return false;
  }
  if (frame.kind == ContextFrameKind::kContext &&
      frame.scope_label == ContextScopeLabel::kPassword) {
    return false;
  }
  return true;
}

void AppendU16(std::uint16_t value, std::vector<std::uint8_t>* output) {
  output->push_back(static_cast<std::uint8_t>(value & 0xffU));
  output->push_back(static_cast<std::uint8_t>((value >> 8U) & 0xffU));
}

void AppendU32(std::uint32_t value, std::vector<std::uint8_t>* output) {
  for (unsigned shift = 0; shift < 32; shift += 8) {
    output->push_back(static_cast<std::uint8_t>((value >> shift) & 0xffU));
  }
}

void AppendU64(std::uint64_t value, std::vector<std::uint8_t>* output) {
  for (unsigned shift = 0; shift < 64; shift += 8) {
    output->push_back(static_cast<std::uint8_t>((value >> shift) & 0xffU));
  }
}

std::uint16_t ReadU16(std::string_view bytes, std::size_t offset) noexcept {
  const auto byte = [&bytes](std::size_t index) {
    return static_cast<std::uint8_t>(bytes[index]);
  };
  return static_cast<std::uint16_t>(byte(offset)) |
         (static_cast<std::uint16_t>(byte(offset + 1)) << 8U);
}

std::uint32_t ReadU32(std::string_view bytes, std::size_t offset) noexcept {
  std::uint32_t value = 0;
  for (unsigned index = 0; index < 4; ++index) {
    value |= static_cast<std::uint32_t>(
                 static_cast<std::uint8_t>(bytes[offset + index]))
             << (8U * index);
  }
  return value;
}

std::uint64_t ReadU64(std::string_view bytes, std::size_t offset) noexcept {
  std::uint64_t value = 0;
  for (unsigned index = 0; index < 8; ++index) {
    value |= static_cast<std::uint64_t>(
                 static_cast<std::uint8_t>(bytes[offset + index]))
             << (8U * index);
  }
  return value;
}

void AppendUtf16(std::u16string_view text,
                 std::vector<std::uint8_t>* output) {
  for (char16_t unit : text) {
    AppendU16(static_cast<std::uint16_t>(unit), output);
  }
}

std::u16string ReadUtf16(std::string_view bytes,
                         std::size_t offset,
                         std::uint32_t units) {
  std::u16string text;
  text.reserve(units);
  for (std::uint32_t index = 0; index < units; ++index) {
    text.push_back(static_cast<char16_t>(ReadU16(bytes, offset + 2U * index)));
  }
  return text;
}

}  // namespace

bool EncodeContextFrame(const ContextFrame& frame,
                        std::vector<std::uint8_t>* output) noexcept {
  if (output == nullptr) {
    return false;
  }
  output->clear();
  if (!ValidateFrame(frame)) {
    return false;
  }
  try {
    const std::size_t payload_bytes =
        2U * (frame.before.size() + frame.after.size());
    if (payload_bytes > kMaxContextFrameBytes - kContextFrameHeaderBytes) {
      return false;
    }

    output->reserve(kContextFrameHeaderBytes + payload_bytes);
    AppendU32(kContextFrameMagic, output);
    AppendU16(kContextFrameVersion, output);
    output->push_back(static_cast<std::uint8_t>(frame.kind));
    output->push_back(static_cast<std::uint8_t>(frame.scope_label));
    AppendU32(frame.source_pid, output);
    AppendU64(frame.revision, output);
    output->insert(output->end(), frame.source_capability.begin(),
                   frame.source_capability.end());
    AppendU32(static_cast<std::uint32_t>(frame.before.size()), output);
    AppendU32(static_cast<std::uint32_t>(frame.after.size()), output);
    AppendUtf16(frame.before, output);
    AppendUtf16(frame.after, output);
    return output->size() <= kMaxContextFrameBytes;
  } catch (...) {
    output->clear();
    return false;
  }
}

ContextFrameDecodeResult DecodeContextFrame(std::string_view bytes,
                                            ContextFrame* output) noexcept {
  if (bytes.size() > kMaxContextFrameBytes) {
    return ContextFrameDecodeResult::kOversized;
  }
  if (output == nullptr || bytes.size() < kContextFrameHeaderBytes) {
    return ContextFrameDecodeResult::kMalformed;
  }
  if (ReadU32(bytes, kMagicOffset) != kContextFrameMagic ||
      ReadU16(bytes, kVersionOffset) != kContextFrameVersion) {
    return ContextFrameDecodeResult::kMalformed;
  }

  const auto kind = static_cast<ContextFrameKind>(
      static_cast<std::uint8_t>(bytes[kKindOffset]));
  const auto scope = static_cast<ContextScopeLabel>(
      static_cast<std::uint8_t>(bytes[kScopeOffset]));
  const std::uint32_t before_units = ReadU32(bytes, kBeforeLengthOffset);
  const std::uint32_t after_units = ReadU32(bytes, kAfterLengthOffset);
  if (before_units > kMaxContextBeforeUtf16Units ||
      after_units > kMaxContextAfterUtf16Units) {
    return ContextFrameDecodeResult::kOversized;
  }

  const std::size_t payload_bytes =
      2U * (static_cast<std::size_t>(before_units) + after_units);
  if (bytes.size() != kContextFrameHeaderBytes + payload_bytes) {
    return ContextFrameDecodeResult::kMalformed;
  }

  try {
    ContextFrame frame;
    frame.kind = kind;
    frame.scope_label = scope;
    frame.source_pid = ReadU32(bytes, kSourcePidOffset);
    frame.revision = ReadU64(bytes, kRevisionOffset);
    for (std::size_t index = 0; index < frame.source_capability.size(); ++index) {
      frame.source_capability[index] = static_cast<std::uint8_t>(
          bytes[kCapabilityOffset + index]);
    }
    frame.before = ReadUtf16(bytes, kContextFrameHeaderBytes, before_units);
    frame.after = ReadUtf16(
        bytes, kContextFrameHeaderBytes + 2U * before_units, after_units);
    if (!ValidateFrame(frame)) {
      return ContextFrameDecodeResult::kMalformed;
    }
    *output = std::move(frame);
    return ContextFrameDecodeResult::kOk;
  } catch (...) {
    return ContextFrameDecodeResult::kMalformed;
  }
}

ContextFrameAcceptResult ContextFrameReceiver::Accept(
    std::string_view bytes, ContextFrame* accepted) noexcept {
  ContextFrame frame;
  const ContextFrameDecodeResult decoded = DecodeContextFrame(bytes, &frame);
  if (decoded == ContextFrameDecodeResult::kOversized) {
    return ContextFrameAcceptResult::kOversized;
  }
  if (decoded != ContextFrameDecodeResult::kOk) {
    return ContextFrameAcceptResult::kMalformed;
  }

  if (retired_capabilities_.count(frame.source_capability) != 0U) {
    return ContextFrameAcceptResult::kStale;
  }

  if (has_source_ && frame.source_capability == latest_capability_) {
    if (frame.source_pid != latest_source_pid_) {
      return ContextFrameAcceptResult::kMalformed;
    }
    if (frame.revision <= latest_revision_) {
      return ContextFrameAcceptResult::kStale;
    }
  } else if (has_source_) {
    retired_capabilities_.insert(latest_capability_);
  }

  latest_capability_ = frame.source_capability;
  latest_source_pid_ = frame.source_pid;
  latest_revision_ = frame.revision;

  if (frame.kind == ContextFrameKind::kClear) {
    retired_capabilities_.insert(frame.source_capability);
    has_source_ = false;
  } else {
    has_source_ = true;
  }

  if (accepted != nullptr) {
    *accepted = std::move(frame);
  }
  return ContextFrameAcceptResult::kAccepted;
}

}  // namespace neural_weasel::context
