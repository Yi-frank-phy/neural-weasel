import numpy as np

from neural_weasel.backends import FullLogitsSnapshotBackend


def test_shared_full_vocabulary_is_converted_once_per_batch():
    class TokenId:
        calls = 0

        def __int__(self):
            TokenId.calls += 1
            return 1

    class Runtime:
        def continue_from_root(self, root, paths, allowed, *, deadline_ms):
            assert allowed == ((1,),) * 8
            return [np.zeros(1)] * 8

    backend = FullLogitsSnapshotBackend(Runtime())
    vocab = (TokenId(),)
    result = backend.continue_from_root("root", [(1,)] * 8, [vocab] * 8, deadline_ms=1000)
    assert result is not None
    assert TokenId.calls == 1
