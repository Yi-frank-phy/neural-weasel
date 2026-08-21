#pragma once

#include <Windows.h>
#include <msctf.h>

namespace neural_weasel::tsf {

// Focus hooks rotate a fresh 128-bit source capability. No worker/backend
// lifecycle is owned by the editor-hosted TSF DLL.
void BeginWeaselContextFocus() noexcept;

// Schedules a bounded read-only edit session and best-effort one-way push.
HRESULT CaptureWeaselContext(ITfContext* context, TfClientId client_id) noexcept;

// Invalidates the current source and queues a text-free clear frame.
void ClearWeaselContext() noexcept;

}  // namespace neural_weasel::tsf
