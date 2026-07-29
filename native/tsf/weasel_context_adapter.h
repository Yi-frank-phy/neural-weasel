#pragma once

#include <Windows.h>
#include <msctf.h>

namespace neural_weasel::tsf {

void StartWeaselContext();
void StopWeaselContext() noexcept;

// Called from the pinned Weasel 0.17.4 text-edit sink. It only schedules a
// read-only TSF edit session; model-service pipe work runs on the bridge worker.
HRESULT CaptureWeaselContext(ITfContext* context, TfClientId client_id);

// Sends a text-free secure-focus cleanup after focus leaves the editor.
void ClearWeaselContext() noexcept;

}  // namespace neural_weasel::tsf
