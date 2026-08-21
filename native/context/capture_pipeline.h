#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <string>

#include "context/source_context_identity.h"
#include "tsf/input_scope_policy.h"
#include "tsf/surrounding_text_edit_session.h"

namespace neural_weasel::context {

// Metadata is deliberately text-free. Raw before/after text lives only in the
// ephemeral snapshot payload and must never be copied into this structure.
struct CaptureContextMetadata {
  SourceContextCapability source_capability{};
  std::uint64_t revision = 0;
  std::string scope_label;
  std::uint32_t before_length = 0;
  std::uint32_t after_length = 0;
};

struct CaptureContextSnapshot {
  CaptureContextMetadata metadata;
  tsf::SurroundingTextSnapshot snapshot;
  bool allow_persistence = false;
};

using SurroundingTextCapture =
    std::function<tsf::SurroundingTextSnapshot()>;

// Applies the security gates in order: scope policy, active source identity,
// then bounded text capture. PASSWORD and inactive identities return no
// payload and never invoke the text-capture callback.
std::optional<CaptureContextSnapshot> CaptureWithPolicy(
    const tsf::InputScopePolicyResult& policy,
    SourceContextIdentity& identity,
    const SurroundingTextCapture& capture);

}  // namespace neural_weasel::context
