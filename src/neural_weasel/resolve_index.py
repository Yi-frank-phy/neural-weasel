from __future__ import annotations

import argparse

from transformers import AutoTokenizer

from .index import default_index_path, resolved_tokenizer_revision, tokenizer_fingerprint
from .paths import configure_hf_cache


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m neural_weasel.resolve_index")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    configure_hf_cache()
    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    path = default_index_path(
        args.model,
        tokenizer_fingerprint(tokenizer),
        resolved_tokenizer_revision(tokenizer),
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
