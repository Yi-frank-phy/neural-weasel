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
        self._neural_cache: dict[
            tuple[str, int, tuple[int, ...]], tuple[PartialPinyinMatch, ...]
        ] = {}
        seen: set[IndexedPronunciation] = set()
        entries: list[IndexedPronunciation] = []
        stack = [index.root]
        while stack:
            node = stack.pop()
            stack.extend(node.children.values())
            for entry in node.terminals:
                if entry in seen:
                    continue
                seen.add(entry)
                entries.append(entry)
                target = self.root
                for syllable in entry.syllable_path:
                    target = target.children.setdefault(syllable, _Node())
                target.terminals.append(entry)
        self.entries = tuple(entries)
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

    def neural_matches(
        self,
        raw: str,
        start: int = 0,
        boundaries: frozenset[int] | None = None,
    ) -> tuple[PartialPinyinMatch, ...]:
        """Enumerate model-token paths compatible with typed pinyin legality.

        ``boundaries`` contains compact-letter offsets created by explicit user
        apostrophes. A pronunciation syllable edge may end at such a boundary,
        but it may never consume across one. This preserves forms such as
        ``xi'an`` while still allowing initial shorthand such as ``n'h``.

        Unlike the legacy bounded matcher, this method walks every descendant
        after the typed path is covered and records how many *additional*
        syllables the token predicts. It is used only by the pure-neural page
        search; existing v0.2 matching behavior remains unchanged.
        """

        if not raw or start < 0 or start >= len(raw):
            return ()
        boundary_positions = frozenset(boundaries or ())
        key = (raw, start, tuple(sorted(boundary_positions)))
        cached = self._neural_cache.get(key)
        if cached is not None:
            return cached
        found: dict[tuple[IndexedPronunciation, int], PartialPinyinMatch] = {}

        def crosses_boundary(begin: int, end: int) -> bool:
            return any(begin < boundary < end for boundary in boundary_positions)

        def record(
            node: _Node,
            pos: int,
            shorthand: int,
            incomplete: bool,
            predicted: int,
        ) -> None:
            for entry in node.terminals:
                match = PartialPinyinMatch(entry, pos, shorthand, incomplete, predicted)
                old = found.get((entry, pos))
                if old is None or (
                    match.completion_syllables,
                    match.shorthand,
                    match.incomplete_final,
                ) < (
                    old.completion_syllables,
                    old.shorthand,
                    old.incomplete_final,
                ):
                    found[(entry, pos)] = match

        def descendants(node: _Node, pos: int, shorthand: int, predicted: int) -> None:
            for child in node.children.values():
                record(child, pos, shorthand, False, predicted)
                descendants(child, pos, shorthand, predicted + 1)

        def visit(node: _Node, pos: int, shorthand: int, incomplete: bool) -> None:
            if pos > start:
                record(node, pos, shorthand, incomplete, 0)
            if pos >= len(raw):
                if not incomplete:
                    descendants(node, pos, shorthand, 1)
                return
            full = [
                (syllable, child)
                for syllable, child in node.children.items()
                if raw.startswith(syllable, pos) and not crosses_boundary(pos, pos + len(syllable))
            ]
            if full:
                for syllable, child in full:
                    visit(child, pos + len(syllable), shorthand, incomplete)
                return
            remaining = raw[pos:]
            for syllable, child in node.children.items():
                if raw[pos] == syllable[0] and not crosses_boundary(pos, pos + 1):
                    visit(child, pos + 1, shorthand + 1, incomplete)
                if (
                    1 < len(remaining) < len(syllable)
                    and syllable.startswith(remaining)
                    and not crosses_boundary(pos, len(raw))
                ):
                    visit(child, len(raw), shorthand, True)

        visit(self.root, start, 0, False)
        result = tuple(
            sorted(
                found.values(),
                key=lambda item: (
                    item.next_position != len(raw),
                    item.completion_syllables,
                    -item.next_position,
                    item.entry.text,
                    item.entry.token_id if item.entry.token_id is not None else -1,
                ),
            )
        )
        self._neural_cache[key] = result
        return result
