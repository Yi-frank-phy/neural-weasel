"""Run via colab-job highram; public synthetic data and pinned public Q8 only."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if len(args.source_commit) != 40 or any(
        c not in "0123456789abcdef" for c in args.source_commit
    ):
        parser.error("source-commit must be the full immutable Git commit")
    root = Path(tempfile.mkdtemp(prefix="neural-weasel-fim-"))
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(["git", "init", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "fetch",
            "--depth=1",
            "https://github.com/Yi-frank-phy/neural-weasel.git",
            args.source_commit,
        ],
        check=True,
        timeout=120,
    )
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", "FETCH_HEAD"], check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "llama-cpp-python==0.3.23",
            "numpy",
            "pypinyin",
            "huggingface_hub",
            "--extra-index-url",
            "https://abetlen.github.io/llama-cpp-python/whl/cpu",
        ],
        check=True,
        timeout=480,
    )
    sys.path.insert(0, str(root / "src"))
    from huggingface_hub import hf_hub_download

    from neural_weasel.gguf_artifact import PRODUCTION_GGUF

    model = hf_hub_download(
        repo_id=PRODUCTION_GGUF.repo_id,
        filename=PRODUCTION_GGUF.filename,
        revision=PRODUCTION_GGUF.revision,
    )
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    print(f"FIM_CONTEXT_AB_SOURCE={args.source_commit}", flush=True)
    subprocess.run(
        [sys.executable, str(root / "scripts" / "benchmark-fim-context.py"), "--gguf", model],
        check=True,
        env=environment,
        timeout=600,
    )


if __name__ == "__main__":
    main()
