from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version

REQUIRED_LLAMA_CPP_PYTHON = "0.3.23"


def check() -> dict[str, object]:
    try:
        installed = version("llama-cpp-python")
    except PackageNotFoundError as exc:
        raise RuntimeError("llama-cpp-python is not installed") from exc
    base_version = installed.split("+", 1)[0]
    if base_version != REQUIRED_LLAMA_CPP_PYTHON:
        raise RuntimeError(
            f"llama-cpp-python {installed} is not pinned {REQUIRED_LLAMA_CPP_PYTHON}"
        )

    from llama_cpp import llama_cpp

    info = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
    if "cuda" not in info.lower():
        raise RuntimeError("llama-cpp-python is not a CUDA-enabled llama.cpp build")
    return {
        "status": "ok",
        "llama_cpp_python": installed,
        "backend": "CUDA",
        "system_info": info,
    }


def main() -> int:
    try:
        payload = check()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
