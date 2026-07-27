from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "test_path",
    [
        "tests/test_backend_benchmark_v02.py",
        "tests/test_model_backends_v02.py",
        "tests/test_service_factory_v02.py",
    ],
)
def test_torch_bound_tests_skip_collection_when_torch_is_absent(test_path: str) -> None:
    """AT-MB-06: lightweight CI can collect tests without installing Torch."""
    source = Path(test_path).read_text(encoding="utf-8")

    assert 'pytest.importorskip("torch"' in source

