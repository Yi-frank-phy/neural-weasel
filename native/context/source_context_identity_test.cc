#include "context/source_context_identity.h"

#include <iostream>

int main() {
  using neural_weasel::context::SourceContextIdentity;

  // Focus change: the previous capture must become stale, and the new focus
  // must receive a different capability even when its revision restarts.
  SourceContextIdentity identity;
  if (!identity.BeginFocus()) {
    std::cerr << "BeginFocus failed\n";
    return 1;
  }
  auto old_focus = identity.Capture();
  if (!old_focus || !identity.IsCurrent(*old_focus)) {
    std::cerr << "initial focus capture failed\n";
    return 1;
  }
  if (!identity.BeginFocus()) {
    std::cerr << "focus change failed\n";
    return 1;
  }
  auto new_focus = identity.Capture();
  if (!new_focus || identity.IsCurrent(*old_focus) ||
      !identity.IsCurrent(*new_focus)) {
    std::cerr << "old focus capture was not rejected\n";
    return 1;
  }
  if (new_focus->capability == old_focus->capability) {
    std::cerr << "new focus reused old capability\n";
    return 1;
  }

  // Revision is strictly monotonic within one active source session. Once a
  // newer capture exists, the older revision cannot become current again.
  auto newer_revision = identity.Capture();
  if (!newer_revision || newer_revision->revision <= new_focus->revision ||
      identity.IsCurrent(*new_focus) ||
      !identity.IsCurrent(*newer_revision)) {
    std::cerr << "revision decreased or stale revision became current\n";
    return 1;
  }

  // EndFocus is a hard invalidation barrier. Capture cannot resurrect the
  // cleared source, and a later focus receives a fresh capability.
  identity.EndFocus();
  if (identity.active() || identity.Capture().has_value() ||
      identity.IsCurrent(*newer_revision)) {
    std::cerr << "cleared identity resurrected\n";
    return 1;
  }
  if (!identity.BeginFocus()) {
    std::cerr << "focus after clear failed\n";
    return 1;
  }
  auto after_clear = identity.Capture();
  if (!after_clear || identity.IsCurrent(*newer_revision) ||
      after_clear->capability == newer_revision->capability ||
      !identity.IsCurrent(*after_clear)) {
    std::cerr << "cleared identity resurrected after new focus\n";
    return 1;
  }

  // Document change invalidates the current document source. Reactivation is
  // a new source lifetime and must not restore the old document capture.
  identity.OnDocumentChange();
  if (identity.active() || identity.IsCurrent(*after_clear) ||
      identity.Capture().has_value()) {
    std::cerr << "document change kept old context alive\n";
    return 1;
  }
  if (!identity.Reactivate()) {
    std::cerr << "reactivate after document change failed\n";
    return 1;
  }
  auto after_document = identity.Capture();
  if (!after_document || identity.IsCurrent(*after_clear) ||
      after_document->capability == after_clear->capability ||
      !identity.IsCurrent(*after_document)) {
    std::cerr << "document change resurrected old context\n";
    return 1;
  }

  // Deactivation clears the source completely. Reactivation starts a new
  // capability and cannot make the pre-deactivation capture current again.
  identity.Deactivate();
  if (identity.active() || identity.Capture().has_value() ||
      identity.IsCurrent(*after_document)) {
    std::cerr << "deactivate did not clear context\n";
    return 1;
  }
  if (!identity.Reactivate()) {
    std::cerr << "reactivate failed\n";
    return 1;
  }
  auto reactivated = identity.Capture();
  if (!reactivated || identity.IsCurrent(*after_document) ||
      reactivated->capability == after_document->capability ||
      !identity.IsCurrent(*reactivated)) {
    std::cerr << "reactivate resurrected old context\n";
    return 1;
  }

  return 0;
}
