#include "tsf/surrounding_text_edit_session.h"

#include <cassert>
#include <string>

namespace neural_weasel::tsf::detail {
void TrimUnpairedUtf16Edges(std::wstring* text) noexcept;
}

namespace {

using neural_weasel::tsf::CaptureDenyReason;
using neural_weasel::tsf::CapturePolicyDecision;
using neural_weasel::tsf::CaptureSurroundingText;
using neural_weasel::tsf::SurroundingTextLimits;
using neural_weasel::tsf::detail::TrimUnpairedUtf16Edges;

constexpr wchar_t kHighSurrogate = static_cast<wchar_t>(0xD83D);
constexpr wchar_t kLowSurrogate = static_cast<wchar_t>(0xDE00);

void TestDefaultCaptureBounds() {
  const SurroundingTextLimits limits;
  assert(limits.before_code_units == 8192);
  assert(limits.after_code_units == 4096);
}

void TestNormalCapturedTextIsUnchanged() {
  std::wstring text = L"ordinary editor context";
  TrimUnpairedUtf16Edges(&text);
  assert(text == L"ordinary editor context");
}

void TestSplitSurrogateAtCaptureBoundaryIsRemoved() {
  std::wstring trailing = L"before";
  trailing.push_back(kHighSurrogate);
  TrimUnpairedUtf16Edges(&trailing);
  assert(trailing == L"before");

  std::wstring leading;
  leading.push_back(kLowSurrogate);
  leading += L"after";
  TrimUnpairedUtf16Edges(&leading);
  assert(leading == L"after");
}

void TestCompleteSurrogatePairIsPreserved() {
  std::wstring text = L"left";
  text.push_back(kHighSurrogate);
  text.push_back(kLowSurrogate);
  text += L"right";
  const std::wstring expected = text;

  TrimUnpairedUtf16Edges(&text);
  assert(text == expected);
}

void TestDeniedCaptureNeverTouchesProtectedText() {
  CapturePolicyDecision policy;
  policy.allowed = false;
  policy.reason = CaptureDenyReason::kSensitiveInputScope;

  const auto snapshot = CaptureSurroundingText(
      nullptr, 0, SurroundingTextLimits{}, policy);

  assert(snapshot.result == E_ACCESSDENIED);
  assert(snapshot.before.empty());
  assert(snapshot.after.empty());
}

}  // namespace

int main() {
  TestDefaultCaptureBounds();
  TestNormalCapturedTextIsUnchanged();
  TestSplitSurrogateAtCaptureBoundaryIsRemoved();
  TestCompleteSurrogatePairIsPreserved();
  TestDeniedCaptureNeverTouchesProtectedText();
  return 0;
}
