from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import indexes_root
from .pinyin import ParsedPinyinInput, concatenate_path, is_all_han, pronunciation_paths

SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class IndexedPronunciation:
    token_id: int | None
    text: str
    pinyin: str
    syllable_path: tuple[str, ...]
    syllables: int
    coverage: bool

    @property
    def display_pinyin(self) -> str:
        return "'".join(self.syllable_path)

    @property
    def boundaries(self) -> frozenset[int]:
        position = 0
        values: set[int] = set()
        for syllable in self.syllable_path:
            position += len(syllable)
            values.add(position)
        return frozenset(values)


def tokenizer_fingerprint(tokenizer: Any) -> str:
    digest = hashlib.sha256()
    digest.update(tokenizer.__class__.__name__.encode())
    digest.update(str(len(tokenizer)).encode())
    vocabulary = tokenizer.get_vocab()
    for token, token_id in sorted(vocabulary.items(), key=lambda item: item[1]):
        digest.update(str(token_id).encode())
        digest.update(b"\0")
        digest.update(token.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
    return digest.hexdigest()


def _installed_pypinyin_version() -> str:
    from importlib.metadata import version

    return version("pypinyin")


def _safe_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-._" else "-"
        for character in value
    )


def resolved_tokenizer_revision(tokenizer: Any, fallback: str = "main") -> str:
    return (
        getattr(tokenizer, "_commit_hash", None)
        or getattr(tokenizer, "init_kwargs", {}).get("_commit_hash")
        or fallback
    )


def default_index_path(
    model_id: str,
    fingerprint: str,
    revision: str = "main",
    pypinyin_version: str | None = None,
) -> Path:
    safe_model = model_id.replace("/", "--")
    safe_revision = _safe_component(revision)[:16]
    pinyin_version = _safe_component(pypinyin_version or _installed_pypinyin_version())
    return indexes_root() / (
        f"{safe_model}-{safe_revision}-{fingerprint[:16]}-"
        f"pypinyin-{pinyin_version}-v{SCHEMA_VERSION}.sqlite3"
    )


class PinyinIndexBuilder:
    def __init__(self, tokenizer: Any, model_id: str, revision: str = "main") -> None:
        self.tokenizer = tokenizer
        self.model_id = model_id
        self.revision = resolved_tokenizer_revision(tokenizer, revision)
        self.fingerprint = tokenizer_fingerprint(tokenizer)

    def build(self, path: Path | None = None) -> Path:
        path = path or default_index_path(
            self.model_id,
            self.fingerprint,
            self.revision,
            self._pypinyin_version(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".building")
        temporary.unlink(missing_ok=True)

        connection = sqlite3.connect(temporary)
        try:
            self._create_schema(connection)
            self._insert_tokens(connection)
            self._insert_character_coverage(connection)
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "model_id": self.model_id,
                "revision": self.revision,
                "tokenizer_hash": self.fingerprint,
                "pypinyin_version": self._pypinyin_version(),
                "built_unix": int(time.time()),
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ((key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()),
            )
            connection.commit()
        finally:
            connection.close()
        temporary.replace(path)
        return path

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE pronunciations (
                id INTEGER PRIMARY KEY,
                token_id INTEGER,
                text TEXT NOT NULL,
                pinyin TEXT NOT NULL,
                syllable_path TEXT NOT NULL,
                syllables INTEGER NOT NULL,
                coverage INTEGER NOT NULL,
                UNIQUE(token_id, text, pinyin, syllable_path, coverage)
            );
            CREATE INDEX idx_pronunciations_pinyin ON pronunciations(pinyin);
            CREATE INDEX idx_pronunciations_token ON pronunciations(token_id);
            """
        )

    def _insert_tokens(self, connection: sqlite3.Connection) -> None:
        rows: list[tuple[int, str, str, str, int, int]] = []
        special_ids = set(self.tokenizer.all_special_ids)
        for token_id in range(len(self.tokenizer)):
            if token_id in special_ids:
                continue
            text = self.tokenizer.decode(
                [token_id],
                skip_special_tokens=False,
                clean_up_tokenization_spaces=False,
            )
            if not is_all_han(text):
                continue
            for path in pronunciation_paths(text):
                rows.append(
                    (token_id, text, concatenate_path(path), "'".join(path), len(path), 0)
                )
            if len(rows) >= 10_000:
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO pronunciations
                    (token_id, text, pinyin, syllable_path, syllables, coverage)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                rows.clear()
        if rows:
            connection.executemany(
                """
                INSERT OR IGNORE INTO pronunciations
                (token_id, text, pinyin, syllable_path, syllables, coverage)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    @staticmethod
    def _insert_character_coverage(connection: sqlite3.Connection) -> None:
        from pypinyin.constants import PINYIN_DICT

        direct_characters = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT text FROM pronunciations WHERE length(text) = 1"
            )
        }
        rows: list[tuple[None, str, str, str, int, int]] = []
        for codepoint in PINYIN_DICT:
            char = chr(codepoint)
            if char in direct_characters or not is_all_han(char):
                continue
            for path in pronunciation_paths(char):
                rows.append((None, char, concatenate_path(path), "'".join(path), 1, 1))
        connection.executemany(
            """
            INSERT OR IGNORE INTO pronunciations
            (token_id, text, pinyin, syllable_path, syllables, coverage)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    @staticmethod
    def _pypinyin_version() -> str:
        return _installed_pypinyin_version()


class _TrieNode:
    __slots__ = ("children", "terminals")

    def __init__(self) -> None:
        self.children: dict[str, _TrieNode] = {}
        self.terminals: list[IndexedPronunciation] = []


class PinyinIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.root = _TrieNode()
        self.metadata: dict[str, object] = {}
        self._load()

    def _load(self) -> None:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            self.metadata = {
                key: json.loads(value)
                for key, value in connection.execute("SELECT key, value FROM metadata")
            }
            if self.metadata.get("schema_version") != SCHEMA_VERSION:
                raise RuntimeError("pinyin index schema is incompatible; rebuild it")
            for (
                token_id,
                text,
                pinyin_value,
                syllable_path,
                syllables,
                coverage,
            ) in connection.execute(
                """
                SELECT token_id, text, pinyin, syllable_path, syllables, coverage
                FROM pronunciations
                ORDER BY pinyin, coverage, token_id
                """
            ):
                entry = IndexedPronunciation(
                    token_id=token_id,
                    text=text,
                    pinyin=pinyin_value,
                    syllable_path=tuple(syllable_path.split("'")),
                    syllables=syllables,
                    coverage=bool(coverage),
                )
                node = self.root
                for character in pinyin_value:
                    node = node.children.setdefault(character, _TrieNode())
                node.terminals.append(entry)
        finally:
            connection.close()

    def compatible(self, parsed: ParsedPinyinInput) -> list[IndexedPronunciation]:
        raw = parsed.compact
        if not raw:
            return []
        results: list[IndexedPronunciation] = []
        node = self.root

        # Tokens whose entire pronunciation was typed; they may consume a prefix
        # and leave the remaining raw pinyin for the next conversion.
        for character in raw:
            if node.terminals:
                results.extend(node.terminals)
            node = node.children.get(character)
            if node is None:
                return self._apply_explicit_boundaries(results, parsed)

        # Tokens that extend the current incomplete/full pinyin prefix.
        stack = [node]
        while stack:
            current = stack.pop()
            results.extend(current.terminals)
            stack.extend(current.children.values())
        return self._apply_explicit_boundaries(results, parsed)

    @staticmethod
    def _apply_explicit_boundaries(
        entries: list[IndexedPronunciation],
        parsed: ParsedPinyinInput,
    ) -> list[IndexedPronunciation]:
        return [
            entry
            for entry in entries
            if {
                boundary
                for boundary in parsed.explicit_boundaries
                if boundary <= len(entry.pinyin)
            }.issubset(entry.boundaries)
        ]

    def stats(self) -> dict[str, int]:
        counts: defaultdict[str, int] = defaultdict(int)
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            for coverage, count in connection.execute(
                "SELECT coverage, count(*) FROM pronunciations GROUP BY coverage"
            ):
                counts["coverage" if coverage else "model"] = count
        finally:
            connection.close()
        return dict(counts)

    def covered_characters(self) -> frozenset[str]:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            return frozenset(
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT text FROM pronunciations WHERE length(text) = 1"
                )
            )
        finally:
            connection.close()
