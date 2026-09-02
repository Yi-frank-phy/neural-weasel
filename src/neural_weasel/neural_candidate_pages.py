from __future__ import annotations

import unicodedata

from .candidate import Candidate
from .neural_candidate_pages_v2 import NeuralCandidatePageManager as _CandidatePageManager
from .neural_candidates import NeuralLanguageMode, _latin_key


class NeuralCandidatePageManager(_CandidatePageManager):
    """Active PR36 pager with coherent dynamic page-0 supplements.

    Startup single-letter pages are deliberately prewarmed from the permanent
    empty-context root. When later bounded baseline continuation discovers a
    genuine multi-token Latin path, the affected single-letter prewarm must be
    invalidated; otherwise that immutable startup tuple would hide the new
    model-scored supplement forever. Recomputing the page still uses only the
    already-copied baseline logits and cache, so it performs no model forward.
    """

    def _remember_baseline_latin_candidate(self, candidate: Candidate) -> None:
        normalized = unicodedata.normalize("NFKC", candidate.text).casefold()
        key = (normalized, candidate.token_path)
        previous = self._baseline_latin_cache.get(key)
        changed = previous is None or _latin_key(candidate) < _latin_key(previous)

        super()._remember_baseline_latin_candidate(candidate)
        if not changed or not normalized:
            return

        first = normalized[0]
        for mode in NeuralLanguageMode:
            self._baseline_single_letter.pop((first, mode), None)
