from __future__ import annotations

from dataclasses import dataclass

from .index import IndexedPronunciation, PinyinIndex


@dataclass(frozen=True, slots=True)
class PartialPinyinMatch:
    entry: IndexedPronunciation
    next_position: int
    shorthand: int = 0
    incomplete_final: bool = False
    completion_syllables: int = 0

    @property
    def cost(self) -> float:
        return (
            -0.06 * self.shorthand
            - (0.03 if self.incomplete_final else 0.0)
            - 0.04 * self.completion_syllables
        )


class _Node:
    __slots__ = ("children", "terminals")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.terminals: list[IndexedPronunciation] = []


class PartialPinyinMatcher:
    def __init__(self, index: PinyinIndex) -> None:
        self.root = _Node()
        self._cache: dict[tuple[str, int], tuple[PartialPinyinMatch, ...]] = {}
        self._character_token_rank_cache: dict[str, float | None] = {}
        character_token_ranks: dict[str, int] = {}
        seen: set[IndexedPronunciation] = set()
        stack = [index.root]
        while stack:
            node = stack.pop()
            stack.extend(node.children.values())
            for entry in node.terminals:
                if entry in seen:
                    continue
                seen.add(entry)
                if entry.token_id is not None and len(entry.text) == 1:
                    character_token_ranks[entry.text] = min(
                        int(entry.token_id),
                        character_token_ranks.get(entry.text, int(entry.token_id)),
                    )
                target = self.root
                for syllable in entry.syllable_path:
                    target = target.children.setdefault(syllable, _Node())
                target.terminals.append(entry)
        self.character_token_ranks = character_token_ranks
        grouped: dict[str, list[str]] = {}
        for syllable in index.syllables:
            if syllable:
                grouped.setdefault(syllable[0], []).append(syllable)
        self.by_initial = {key: tuple(values) for key, values in grouped.items()}

    def character_token_rank(self, text: str) -> float | None:
        if text in self._character_token_rank_cache:
            return self._character_token_rank_cache[text]
        ranks = [self.character_token_ranks.get(character) for character in text]
        if not ranks or any(rank is None for rank in ranks):
            result = None
        else:
            result = sum(rank for rank in ranks if rank is not None) / len(ranks)
        self._character_token_rank_cache[text] = result
        return result

    def is_complete_syllable_sequence(self, raw: str) -> bool:
        if not raw:
            return False
        reachable = [False] * (len(raw) + 1)
        reachable[0] = True
        for pos in range(len(raw)):
            if not reachable[pos]:
                continue
            for syllable in self.by_initial.get(raw[pos], ()):
                if raw.startswith(syllable, pos):
                    reachable[pos + len(syllable)] = True
        return reachable[-1]

    def partial_matches(self, raw: str, start: int = 0) -> tuple[PartialPinyinMatch, ...]:
        if not raw or start < 0 or start >= len(raw):
            return ()
        key = (raw, start)
        if key in self._cache:
            return self._cache[key]
        found: dict[tuple[IndexedPronunciation, int], PartialPinyinMatch] = {}

        def record(
            node: _Node,
            pos: int,
            shorthand: int,
            incomplete: bool,
            completion_syllables: int = 0,
        ) -> None:
            for entry in node.terminals:
                match = PartialPinyinMatch(
                    entry,
                    pos,
                    shorthand,
                    incomplete,
                    completion_syllables,
                )
                old = found.get((entry, pos))
                if old is None or match.cost > old.cost:
                    found[(entry, pos)] = match

        def visit(node: _Node, pos: int, shorthand: int, incomplete: bool) -> None:
            if pos > start:
                record(node, pos, shorthand, incomplete)
            if pos >= len(raw):
                if not incomplete:
                    for child in node.children.values():
                        record(child, pos, shorthand, incomplete, 1)
                return
            full = [
                (syllable, child)
                for syllable, child in node.children.items()
                if raw.startswith(syllable, pos)
            ]
            if full:
                for syllable, child in full:
                    visit(child, pos + len(syllable), shorthand, incomplete)
                return
            remaining = raw[pos:]
            for syllable, child in node.children.items():
                if raw[pos] == syllable[0]:
                    visit(child, pos + 1, shorthand + 1, incomplete)
                if 1 < len(remaining) < len(syllable) and syllable.startswith(remaining):
                    visit(child, len(raw), shorthand, True)

        visit(self.root, start, 0, False)
        result = tuple(
            sorted(
                found.values(),
                key=lambda item: (-item.next_position, -item.cost, item.entry.text),
            )
        )
        self._cache[key] = result
        return result
