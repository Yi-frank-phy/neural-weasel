from __future__ import annotations

from dataclasses import dataclass

from .index import IndexedPronunciation, PinyinIndex


@dataclass(frozen=True, slots=True)
class PartialPinyinMatch:
    entry: IndexedPronunciation
    next_position: int
    shorthand: int = 0
    incomplete_final: bool = False

    @property
    def cost(self) -> float:
        return -0.06 * self.shorthand - (0.03 if self.incomplete_final else 0.0)


class _Node:
    __slots__ = ("children", "terminals")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.terminals: list[IndexedPronunciation] = []


class PartialPinyinMatcher:
    def __init__(self, index: PinyinIndex) -> None:
        self.root = _Node()
        self._cache: dict[tuple[str, int], tuple[PartialPinyinMatch, ...]] = {}
        seen: set[IndexedPronunciation] = set()
        stack = [index.root]
        while stack:
            node = stack.pop()
            stack.extend(node.children.values())
            for entry in node.terminals:
                if entry in seen:
                    continue
                seen.add(entry)
                target = self.root
                for syllable in entry.syllable_path:
                    target = target.children.setdefault(syllable, _Node())
                target.terminals.append(entry)
        grouped: dict[str, list[str]] = {}
        for syllable in index.syllables:
            if syllable:
                grouped.setdefault(syllable[0], []).append(syllable)
        self.by_initial = {key: tuple(values) for key, values in grouped.items()}

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

        def visit(node: _Node, pos: int, shorthand: int, incomplete: bool) -> None:
            if pos > start:
                for entry in node.terminals:
                    match = PartialPinyinMatch(entry, pos, shorthand, incomplete)
                    old = found.get((entry, pos))
                    if old is None or match.cost > old.cost:
                        found[(entry, pos)] = match
            if pos >= len(raw):
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
