[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$WeaselRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Read-SourceFile {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required upstream file is missing: $Path"
    }
    return [IO.File]::ReadAllText($Path)
}

function Write-SourceFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Replace-Literal {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Old,
        [Parameter(Mandatory)][string]$New
    )
    $Content = Read-SourceFile -Path $Path
    $UsesCrLf = $Content.Contains("`r`n")
    $NormalizedContent = $Content.Replace("`r`n", "`n")
    $NormalizedOld = $Old.Replace("`r`n", "`n")
    $NormalizedNew = $New.Replace("`r`n", "`n")
    if (-not $NormalizedContent.Contains($NormalizedOld)) {
        throw "Pinned upstream seam changed in $Path; missing expected text: $Old"
    }
    $Updated = $NormalizedContent.Replace($NormalizedOld, $NormalizedNew)
    if ($UsesCrLf) {
        $Updated = $Updated.Replace("`n", "`r`n")
    }
    Write-SourceFile -Path $Path -Content $Updated
}

$ResolvedWeaselRoot = (Resolve-Path -LiteralPath $WeaselRoot).Path
$CompositionSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/Composition.cpp'
$EditSessionSource = Join-Path $ResolvedWeaselRoot 'WeaselTSF/EditSession.cpp'
$WeaselTsfHeader = Join-Path $ResolvedWeaselRoot 'WeaselTSF/WeaselTSF.h'

# Coherent backport of the composition/edit-session half of upstream Weasel
# PR #1462 (merged as 8f2561f...). This removes the old non-inline CUAS
# placeholder text and fixes replacement-composition lifetime/positioning.
Replace-Literal -Path $CompositionSource -Old @'
  CStartCompositionEditSession(com_ptr<WeaselTSF> pTextService,
                               com_ptr<ITfContext> pContext,
                               BOOL fCUASWorkaroundEnabled,
                               BOOL inlinePreeditEnabled)
      : CEditSession(pTextService, pContext),
        _inlinePreeditEnabled(inlinePreeditEnabled) {
    _fCUASWorkaroundEnabled = fCUASWorkaroundEnabled;
  }

  /* ITfEditSession */
  STDMETHODIMP DoEditSession(TfEditCookie ec);

 private:
  BOOL _fCUASWorkaroundEnabled;
  BOOL _inlinePreeditEnabled;
'@ -New @'
  CStartCompositionEditSession(com_ptr<WeaselTSF> pTextService,
                               com_ptr<ITfContext> pContext,
                               BOOL fCUASWorkaroundEnabled)
      : CEditSession(pTextService, pContext) {
    _fCUASWorkaroundEnabled = fCUASWorkaroundEnabled;
  }

  /* ITfEditSession */
  STDMETHODIMP DoEditSession(TfEditCookie ec);

 private:
  BOOL _fCUASWorkaroundEnabled;
'@

Replace-Literal -Path $CompositionSource -Old @'
    /* WORKAROUND:
     *   CUAS does not provide a correct GetTextExt() position unless the
     * composition is filled with characters. So we insert a zero width space
     * here. The workaround is only needed when inline preedit is not enabled.
     *   See https://github.com/rime/weasel/pull/883#issuecomment-1567625762
     */
    if (!_inlinePreeditEnabled) {
      pRangeComposition->SetText(ec, TF_ST_CORRECTION, L" ", 1);
    }

    /* set selection */
    TF_SELECTION tfSelection;
    if (_inlinePreeditEnabled)
      pRangeComposition->Collapse(ec, TF_ANCHOR_END);
    else
      pRangeComposition->Collapse(ec, TF_ANCHOR_START);
    tfSelection.range = pRangeComposition;
    tfSelection.style.ase = TF_AE_NONE;
    tfSelection.style.fInterimChar = FALSE;
    _pContext->SetSelection(ec, 1, &tfSelection);
'@ -New @'
    /* set selection */
    TF_SELECTION tfSelection;
    pRangeComposition->Collapse(ec, TF_ANCHOR_END);
    tfSelection.range = pRangeComposition;
    tfSelection.style.ase = TF_AE_NONE;
    tfSelection.style.fInterimChar = FALSE;
    _pContext->SetSelection(ec, 1, &tfSelection);

    // The old composition's range can remain visible while its asynchronous
    // end session is pending. Position only after this new composition exists.
    _pTextService->_UpdateCompositionWindow(_pContext);
'@

Replace-Literal -Path $CompositionSource -Old @'
  pStartCompositionEditSession.Attach(new CStartCompositionEditSession(
      this, pContext, fCUASWorkaroundEnabled, _cand->style().inline_preedit));
'@ -New @'
  pStartCompositionEditSession.Attach(
      new CStartCompositionEditSession(this, pContext, fCUASWorkaroundEnabled));
'@

Replace-Literal -Path $CompositionSource -Old @'
  _pComposition->EndComposition(ec);
  _pTextService->_FinalizeComposition();
  return S_OK;
}

void WeaselTSF::_EndComposition(com_ptr<ITfContext> pContext, BOOL clear) {
  CEndCompositionEditSession* pEditSession;
  HRESULT hr;

  _cand->EndUI();
  if ((pEditSession = new CEndCompositionEditSession(
           this, pContext, _pComposition, clear)) != NULL) {
'@ -New @'
  // Drop ownership before EndComposition(). Some applications notify
  // OnCompositionTerminated synchronously while the old composition ends.
  if (_pTextService && _pTextService->_IsCurrentComposition(_pComposition))
    _pTextService->_FinalizeComposition();
  _pComposition->EndComposition(ec);
  return S_OK;
}

void WeaselTSF::_EndComposition(com_ptr<ITfContext> pContext,
                                BOOL clear,
                                BOOL endUI) {
  CEndCompositionEditSession* pEditSession;
  HRESULT hr;
  com_ptr<ITfComposition> pComposition = _pComposition;

  if (endUI)
    _cand->EndUI();
  if ((pEditSession = new CEndCompositionEditSession(
           this, pContext, pComposition, clear)) != NULL) {
'@

Replace-Literal -Path $CompositionSource -Old @'
  _pEditSessionContext->RequestEditSession(
      _tfClientId, this, TF_ES_ASYNCDONTCARE | TF_ES_READWRITE, &hr);
  _async_edit = !!(hr == TF_S_ASYNC);
  _UpdateCompositionWindow(pContext);
}

/* Composition State */
STDAPI WeaselTSF::OnCompositionTerminated(TfEditCookie ecWrite,
                                          ITfComposition* pComposition) {
  // NOTE:
  // This will be called when an edit session ended up with an empty composition
  // string, Even if it is closed normally. Silly M$.

  _AbortComposition();
  return S_OK;
}
'@ -New @'
  _pEditSessionContext->RequestEditSession(
      _tfClientId, this, TF_ES_ASYNCDONTCARE | TF_ES_READWRITE, &hr);
  _async_edit = !!(hr == TF_S_ASYNC);
}

/* Composition State */
STDAPI WeaselTSF::OnCompositionTerminated(TfEditCookie ecWrite,
                                          ITfComposition* pComposition) {
  // NOTE:
  // This will be called when an edit session ended up with an empty composition
  // string, Even if it is closed normally. Silly M$.

  // EndComposition() may generate this callback for the composition we just
  // closed. Only an active, matching composition is an external termination.
  if (!_IsCurrentComposition(pComposition))
    return S_OK;

  // A host may terminate the empty TSF composition used for a non-inline
  // preedit. Keep Rime's composing state; the next key will create a fresh
  // TSF composition. Only an inactive Rime session should be aborted here.
  if (_status.composing) {
    _FinalizeComposition();
    return S_OK;
  }

  _AbortComposition();
  return S_OK;
}
'@

Replace-Literal -Path $CompositionSource -Old @'
BOOL WeaselTSF::_IsComposing() {
  return _pComposition != NULL;
}
'@ -New @'
BOOL WeaselTSF::_IsComposing() {
  return _pComposition != NULL;
}

BOOL WeaselTSF::_IsCurrentComposition(ITfComposition* pComposition) {
  return _pComposition != nullptr && _pComposition == pComposition;
}
'@

Replace-Literal -Path $EditSessionSource -Old @'
  if (ok) {
    if (!commit.empty()) {
      // For auto-selecting, commit and preedit can both exist.
      // Commit and close the original composition first.
      if (!_IsComposing()) {
        _StartComposition(_pEditSessionContext,
                          _fCUASWorkaroundEnabled && !config.inline_preedit);
      }
      _InsertText(_pEditSessionContext, commit);
      _EndComposition(_pEditSessionContext, false);
      _committed = TRUE;
    } else {
      _committed = FALSE;
    }
    if (_status.composing && !_IsComposing()) {
      _StartComposition(_pEditSessionContext,
                        _fCUASWorkaroundEnabled && !config.inline_preedit);
    } else if (!_status.composing && _IsComposing()) {
      _EndComposition(_pEditSessionContext, true);
    }
    if (_IsComposing() && config.inline_preedit) {
      _ShowInlinePreedit(_pEditSessionContext, context);
    }
    _UpdateCompositionWindow(_pEditSessionContext);
  }

  _UpdateUI(*context, _status);
'@ -New @'
  bool compositionEnded = false;
  if (ok) {
    if (!commit.empty()) {
      // For auto-selecting, commit and preedit can both exist.
      // Commit the old TSF composition. If Rime immediately has a new
      // preedit, the following state check starts a replacement composition.
      if (!_IsComposing()) {
        _StartComposition(_pEditSessionContext,
                          _fCUASWorkaroundEnabled && !config.inline_preedit);
      }
      _InsertText(_pEditSessionContext, commit);
      // Keep the candidate UI alive while a replacement composition is being
      // created; otherwise the old window can disappear between edit sessions.
      _EndComposition(_pEditSessionContext, false, !_status.composing);
      compositionEnded = true;
      _committed = TRUE;
    } else {
      _committed = FALSE;
    }
    if (_status.composing && (compositionEnded || !_IsComposing())) {
      _StartComposition(_pEditSessionContext,
                        _fCUASWorkaroundEnabled && !config.inline_preedit);
    } else if (!_status.composing && _IsComposing()) {
      _EndComposition(_pEditSessionContext, true);
    }
    if (_IsComposing() && config.inline_preedit) {
      _ShowInlinePreedit(_pEditSessionContext, context);
    }
  }

  if (ok && !compositionEnded)
    _UpdateCompositionWindow(_pEditSessionContext);
  _UpdateUI(*context, _status);
'@

Replace-Literal -Path $WeaselTsfHeader -Old @'
  void _EndComposition(com_ptr<ITfContext> pContext, BOOL clear);
'@ -New @'
  void _EndComposition(com_ptr<ITfContext> pContext,
                       BOOL clear,
                       BOOL endUI = TRUE);
'@
Replace-Literal -Path $WeaselTsfHeader -Old @'
  BOOL _IsComposing();
  void _SetComposition(com_ptr<ITfComposition> pComposition);
'@ -New @'
  BOOL _IsComposing();
  BOOL _IsCurrentComposition(ITfComposition* pComposition);
  void _SetComposition(com_ptr<ITfComposition> pComposition);
'@

Write-Host 'Applied coherent upstream PR #1462 composition/edit-session backport.'
