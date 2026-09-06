[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot,
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Keep the large pinned overlay stable and layer the C1 presentation-readiness
# transport on top. Dot-sourcing intentionally retains Replace-Literal and the
# resolved pinned-tree paths while preserving the same public entry point.
$CoreOverlay = Join-Path $PSScriptRoot 'prepare-weasel-overlay-core.ps1'
. $CoreOverlay -WeaselRoot $WeaselRoot -RepositoryRoot $RepositoryRoot

# ProcessKeyEvent's boolean means "handled", not "presentation ready". Carry
# the single readiness bit through Weasel's existing status response instead so
# the private U+FDD0 refresh key can remain handled and never enter commit
# history. No editor text, candidate text, or context identity is added here.
$IpcDataHeader = Join-Path $ResolvedWeaselRoot 'include/WeaselIPCData.h'
Replace-Literal -Path $IpcDataHeader -Old @'
        disabled(false),
        full_shape(false) {}
'@ -New @'
        disabled(false),
        full_shape(false),
        neural_candidate_pending(false) {}
'@
Replace-Literal -Path $IpcDataHeader -Old @'
    disabled = false;
    full_shape = false;
    type = SCHEMA;
'@ -New @'
    disabled = false;
    full_shape = false;
    neural_candidate_pending = false;
    type = SCHEMA;
'@
Replace-Literal -Path $IpcDataHeader -Old @'
            status.composing == composing && status.disabled == disabled &&
            status.full_shape == full_shape && status.type == type);
'@ -New @'
            status.composing == composing && status.disabled == disabled &&
            status.full_shape == full_shape &&
            status.neural_candidate_pending == neural_candidate_pending &&
            status.type == type);
'@
Replace-Literal -Path $IpcDataHeader -Old @'
  // 全角状态
  bool full_shape;
  // 图标类型, schema/full_shape
'@ -New @'
  // 全角状态
  bool full_shape;
  // Experimental metadata only; never contains editor or candidate text.
  bool neural_candidate_pending;
  // 图标类型, schema/full_shape
'@

$ContextUpdater = Join-Path $ResolvedWeaselRoot 'WeaselIPC/ContextUpdater.cpp'
Replace-Literal -Path $ContextUpdater -Old @'
  if (k[1] == L"full_shape") {
    m_pTarget->p_status->full_shape = bool_value;
    return;
  }
'@ -New @'
  if (k[1] == L"full_shape") {
    m_pTarget->p_status->full_shape = bool_value;
    return;
  }

  if (k[1] == L"neural_candidate_pending") {
    m_pTarget->p_status->neural_candidate_pending = bool_value;
    return;
  }
'@

$RimeWithWeasel = Join-Path $ResolvedWeaselRoot 'RimeWithWeasel/RimeWithWeasel.cpp'
Replace-Literal -Path $RimeWithWeasel -Old @'
    messages.push_back(std::string("status.full_shape=") +
                       std::to_string(status.is_full_shape) + '\n');
    messages.push_back(std::string("status.schema_id=") +
                       std::string(status.schema_id) + '\n');
'@ -New @'
    messages.push_back(std::string("status.full_shape=") +
                       std::to_string(status.is_full_shape) + '\n');
    char neural_candidate_pending[8] = {};
    const bool candidate_pending =
        rime_api->get_property(session_id, "neural_candidate_pending",
                               neural_candidate_pending,
                               sizeof(neural_candidate_pending) - 1) &&
        neural_candidate_pending[0] == '1';
    messages.push_back(std::string("status.neural_candidate_pending=") +
                       std::to_string(candidate_pending) + '\n');
    messages.push_back(std::string("status.schema_id=") +
                       std::string(status.schema_id) + '\n');
'@

$WeaselTsfSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.cpp'
Replace-Literal -Path $WeaselTsfSource -Old @'
  const bool presentation_ready = m_client.ProcessKeyEvent(refresh);
  _UpdateComposition(context);

  if (presentation_ready ||
'@ -New @'
  if (!m_client.ProcessKeyEvent(refresh)) {
    _CancelNeuralRefresh();
    return;
  }
  _UpdateComposition(context);
  const bool presentation_ready = !_status.neural_candidate_pending;

  if (presentation_ready ||
'@

Write-Host 'Added identity-free Neural Weasel presentation readiness status transport'
