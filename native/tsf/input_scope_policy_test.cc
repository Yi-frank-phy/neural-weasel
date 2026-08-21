#include "tsf/input_scope_policy.h"

#include <inputscope.h>

#include <iostream>

#include "context/capture_pipeline.h"
#include "context/source_context_identity.h"

int main() {
  using neural_weasel::context::CaptureContextSnapshot;
  using neural_weasel::context::CaptureWithPolicy;
  using neural_weasel::context::SourceContextIdentity;
  using neural_weasel::tsf::ClassifyInputScopes;
  using neural_weasel::tsf::InputScopeState;
  using neural_weasel::tsf::SurroundingTextSnapshot;

  const InputScope password_scope = IS_PASSWORD;
  const auto password = ClassifyInputScopes(&password_scope, 1);
  if (password.state != InputScopeState::kPassword ||
      password.allow_prediction || password.allow_persistence ||
      password.allow_capture) {
    std::cerr << "password scope was not denied\n";
    return 1;
  }

  const InputScope credential_scopes[] = {
      IS_NUMERIC_PASSWORD,
      IS_NUMERIC_PIN,
      IS_ALPHANUMERIC_PIN,
      IS_ALPHANUMERIC_PIN_SET,
  };
  for (const InputScope credential_scope : credential_scopes) {
    const auto credential = ClassifyInputScopes(&credential_scope, 1);
    if (credential.state != InputScopeState::kPassword ||
        credential.allow_prediction || credential.allow_persistence ||
        credential.allow_capture) {
      std::cerr << "credential scope was not denied\n";
      return 1;
    }
  }

  const InputScope private_scope_value = IS_PRIVATE;
  const auto private_scope = ClassifyInputScopes(&private_scope_value, 1);
  if (private_scope.state != InputScopeState::kPrivate ||
      !private_scope.allow_prediction || private_scope.allow_persistence ||
      !private_scope.allow_capture) {
    std::cerr << "private scope classification failed\n";
    return 1;
  }

  const auto normal = ClassifyInputScopes(nullptr, 0);
  if (normal.state != InputScopeState::kNormal || !normal.allow_prediction ||
      !normal.allow_persistence || !normal.allow_capture) {
    std::cerr << "empty scope was not normal\n";
    return 1;
  }

  const InputScope unknown_scope = static_cast<InputScope>(0x7fffffff);
  const auto unknown = ClassifyInputScopes(&unknown_scope, 1);
  if (unknown.state != InputScopeState::kNormal || !unknown.allow_capture) {
    std::cerr << "unknown scope defaulted to deny\n";
    return 1;
  }

  SourceContextIdentity identity;
  if (!identity.BeginFocus()) {
    std::cerr << "BeginFocus failed\n";
    return 1;
  }

  bool password_capture_called = false;
  const auto password_result = CaptureWithPolicy(
      password, identity, [&]() {
        password_capture_called = true;
        return SurroundingTextSnapshot{};
      });
  if (password_result || password_capture_called) {
    std::cerr << "password path called text capture\n";
    return 1;
  }

  const auto private_result = CaptureWithPolicy(
      private_scope, identity, []() {
        SurroundingTextSnapshot snapshot;
        snapshot.before = L"secret draft";
        snapshot.after = L"tail";
        snapshot.partial = false;
        snapshot.result = S_OK;
        return snapshot;
      });
  if (!private_result ||
      private_result->metadata.scope_label != "PRIVATE" ||
      private_result->allow_persistence ||
      private_result->metadata.before_length != 12 ||
      private_result->metadata.after_length != 4) {
    std::cerr << "private capture metadata was invalid\n";
    return 1;
  }

  const auto normal_result = CaptureWithPolicy(
      normal, identity, []() {
        SurroundingTextSnapshot snapshot;
        snapshot.before = L"hello";
        snapshot.after = L"world";
        snapshot.partial = false;
        snapshot.result = S_OK;
        return snapshot;
      });
  if (!normal_result || normal_result->metadata.scope_label != "NORMAL" ||
      !normal_result->allow_persistence ||
      normal_result->snapshot.before != L"hello" ||
      normal_result->snapshot.after != L"world" ||
      normal_result->metadata.revision == 0) {
    std::cerr << "normal capture snapshot was invalid\n";
    return 1;
  }

  SourceContextIdentity inactive_identity;
  bool inactive_capture_called = false;
  const auto inactive_result = CaptureWithPolicy(
      normal, inactive_identity, [&]() {
        inactive_capture_called = true;
        return SurroundingTextSnapshot{};
      });
  if (inactive_result || inactive_capture_called) {
    std::cerr << "inactive identity did not reject capture\n";
    return 1;
  }

  return 0;
}
