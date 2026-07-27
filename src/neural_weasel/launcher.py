from __future__ import annotations

import os
import subprocess
import sys

from .gpu import child_environment, discover_target_gpu
from .paths import configure_hf_cache


def main() -> int:
    configure_hf_cache()
    if os.environ.get("NEURAL_WEASEL_GPU_CHILD") == "1":
        from .internal_cli import main as internal_main

        return internal_main()

    gpu = discover_target_gpu()
    env = child_environment(gpu)
    env["NEURAL_WEASEL_GPU_CHILD"] = "1"
    command = [sys.executable, "-m", "neural_weasel.launcher", *sys.argv[1:]]
    completed = subprocess.run(command, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

