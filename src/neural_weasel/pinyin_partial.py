from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass

from .immutable_record_gc import (
    exclude_atomic_record,
    exclude_prevalidated_atomic_record,
    is_atomic_payload,
)
from .index import IndexedPronunciation, PinyinIndex
from .search_batches import cooperative_sorted

_MAX_NEURAL_MATCH_CACHE_KEYS = 256
_MAX_NEURAL_MATCH_CACHE_RECORDS = 16_384


@dataclass(frozen=True, slots=True)
class PartialPinyinMatch:
    entry: IndexedPronunciation
    next_position: int
    shorthand: int = 0
    incomplete_final: bool = False
    completion_syllables: int = 0

    def _exclude_from_cyclic_scans(self) -> None:
        if (
            type(self) is not _PARTIAL_MATCH_RECORD_TYPE
            or type(self.entry) is not IndexedPronunciation
            or len(self.__dataclass_fields__) != 5
            or len(self.entry.__dataclass_fields__) != 9
        ):
            return
        # IndexedPronunciation is frozen too. Check every owned field, rather
        # than trusting annotations or excluding arbitrary index subclasses.
        entry = self.entry
        exclude_atomic_record(
            self,
            (
                entry.token_id,
                entry.text,
                entry.pinyin,
                entry.syllable_path,
                entry.syllables,
                entry.coverage,
                entry.token_path,
                entry.display_pinyin,
                entry.normalized_text,
                self.next_position,
                self.shorthand,
                self.incomplete_final,
                self.completion_syllables,
            ),
        )

    @property
    def cost(self) -> float:
        return (
            -0.06 * self.shorthand
            - (0.03 if self.incomplete_final else 0.0)
            - 0.04 * self.completion_syllables
        )


_PARTIAL_MATCH_RECORD_TYPE = PartialPinyinMatch
_INDEX_ENTRY_RECORD_TYPE = IndexedPronunciation
_exclude_uncached_match = PartialPinyinMatch._exclude_from_cyclic_scans


def _is_atomic_index_entry(entry: IndexedPronunciation) -> bool:
    if type(entry) is not _INDEX_ENTRY_RECORD_TYPE or len(entry.__dataclass_fields__) != 9:
        return False
    return is_atomic_payload(
        (
            entry.token_id,
            entry.text,
            entry.pinyin,
            entry.syllable_path,
            entry.syllables,
            entry.coverage,
            entry.token_path,
            entry.display_pinyin,
            entry.normalized_text,
        )
    )


def _exclude_cached_match(match: PartialPinyinMatch, validated_entry_ids: frozenset[int]) -> None:
    if (
        type(match) is _PARTIAL_MATCH_RECORD_TYPE
        and len(match.__dataclass_fields__) == 5
        and type(match.entry) is _INDEX_ENTRY_RECORD_TYPE
        and len(match.entry.__dataclass_fields__) == 9
        and id(match.entry) in validated_entry_ids
    ):
        if is_atomic_payload(
            (
                match.next_position,
                match.shorthand,
                match.incomplete_final,
                match.completion_syllables,
            )
        ):
            exclude_prevalidated_atomic_record(match)
        return
    _exclude_uncached_match(match)


class _Node:
    __slots__ = ("children", "terminals")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.terminals: list[IndexedPronunciation] = []


class PartialPinyinMatcher:
    def __init__(self, index: PinyinIndex) -> None:
        self.root = _Node()
        self._cache: dict[tuple[str, int], tuple[PartialPinyinMatch, ...]] = {}
        self._neural_cache: OrderedDict[
            tuple[str, int, tuple[int, ...]], tuple[PartialPinyinMatch, ...]
        ] = OrderedDict()
        self._neural_exact_cache: OrderedDict[
            tuple[str, int, tuple[int, ...]], tuple[PartialPinyinMatch, ...]
        ] = OrderedDict()
        self._neural_unextended_cache: OrderedDict[
            tuple[str, int, tuple[int, ...]], tuple[PartialPinyinMatch, ...]
        ] = OrderedDict()
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
        # Exact frozen public index entries are owned for this matcher's entire
        # lifetime. Numeric identities therefore cannot be reused while this
        # proof exists. No context/state or new entry references are cached.
        self._validated_atomic_entry_ids = frozenset(
            id(entry) for entry in self.entries if _is_atomic_index_entry(entry)
        )
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
        *,
        exact_only: bool = False,
    ) -> tuple[PartialPinyinMatch, ...]:
        key = (raw, start, tuple(sorted(boundaries or ())))
        cache = self._neural_exact_cache if exact_only else self._neural_cache
        cached = self._cached_neural_matches(cache, key)
        if cached is not None:
            return cached
        return tuple(
            match
            for match in self.iter_neural_matches(
                raw, start, boundaries, exact_only=exact_only, _cooperative_sort=False
            )
            if match is not None
        )

    def iter_neural_matches(
        self,
        raw: str,
        start: int = 0,
        boundaries: frozenset[int] | None = None,
        *,
        exact_only: bool = False,
        include_descendants: bool = True,
        _cooperative_sort: bool = True,
    ) -> Iterator[PartialPinyinMatch | None]:
        """Enumerate model-token paths compatible with typed pinyin legality.

        ``boundaries`` contains compact-letter offsets created by explicit user
        apostrophes. A pronunciation syllable edge may end at such a boundary,
        but it may never consume across one. This preserves forms such as
        ``xi'an`` while still allowing initial shorthand such as ``n'h``.

        Unlike the legacy bounded matcher, this method walks every descendant
        after the typed path is covered and records how many *additional*
        syllables the token predicts. It is used only by the pure-neural page
        search; existing v0.2 matching behavior remains unchanged.
        ``include_descendants=False`` retains shorthand and unfinished syllable
        matching while deferring extra predicted syllables to a separate cache.
        """

        if not raw or start < 0 or start >= len(raw):
            return
        boundary_positions = frozenset(boundaries or ())
        key = (raw, start, tuple(sorted(boundary_positions)))
        cache = (
            self._neural_exact_cache
            if exact_only
            else self._neural_cache
            if include_descendants
            else self._neural_unextended_cache
        )
        cached = self._cached_neural_matches(cache, key)
        if cached is not None:
            yield from cached
            return
        found: dict[tuple[IndexedPronunciation, int], PartialPinyinMatch] = {}
        work = 0
        visited: dict[tuple[_Node, int, bool], int] = {}

        def checkpoint() -> bool:
            nonlocal work
            work += 1
            return work % 32 == 0

        def crosses_boundary(begin: int, end: int) -> bool:
            return any(begin < boundary < end for boundary in boundary_positions)

        def record(
            node: _Node,
            pos: int,
            shorthand: int,
            incomplete: bool,
            predicted: int,
        ) -> Iterator[None]:
            for entry in node.terminals:
                if checkpoint():
                    yield None
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

        def descendants(node: _Node, pos: int, shorthand: int, predicted: int) -> Iterator[None]:
            for child in node.children.values():
                if checkpoint():
                    yield None
                yield from record(child, pos, shorthand, False, predicted)
                yield from descendants(child, pos, shorthand, predicted + 1)

        def visit(node: _Node, pos: int, shorthand: int, incomplete: bool) -> Iterator[None]:
            if checkpoint():
                yield None
            state = (node, pos, incomplete)
            previous = visited.get(state)
            if previous is not None and previous <= shorthand:
                return
            visited[state] = shorthand
            if pos > start:
                yield from record(node, pos, shorthand, incomplete, 0)
            if pos >= len(raw):
                if not incomplete and not exact_only and include_descendants:
                    yield from descendants(node, pos, shorthand, 1)
                return
            for syllable, child in node.children.items():
                if checkpoint():
                    yield None
                if raw.startswith(syllable, pos) and not crosses_boundary(pos, pos + len(syllable)):
                    yield from visit(child, pos + len(syllable), shorthand, incomplete)
            if exact_only:
                return
            remaining = raw[pos:]
            for syllable, child in node.children.items():
                if checkpoint():
                    yield None
                if raw[pos] == syllable[0] and not crosses_boundary(pos, pos + 1):
                    yield from visit(child, pos + 1, shorthand + 1, incomplete)
                if (
                    1 < len(remaining) < len(syllable)
                    and syllable.startswith(remaining)
                    and not crosses_boundary(pos, len(raw))
                ):
                    yield from visit(child, len(raw), shorthand, True)

        try:
            yield from visit(self.root, start, 0, False)
            max_positions: dict[IndexedPronunciation, int] = {}
            for position, (entry, pos) in enumerate(found):
                if position % 32 == 0:
                    yield None
                max_positions[entry] = max(pos, max_positions.get(entry, start))
            values = (match for (entry, pos), match in found.items() if pos == max_positions[entry])

            def sort_key(item: PartialPinyinMatch) -> tuple[bool, int, int, str, int]:
                return (
                    item.next_position != len(raw),
                    item.completion_syllables,
                    -item.next_position,
                    item.entry.text,
                    item.entry.token_id if item.entry.token_id is not None else -1,
                )

            if _cooperative_sort:
                ordered = yield from cooperative_sorted(values, key=sort_key)
            else:
                # The synchronous tuple consumer discards every checkpoint.
                # Stable native sorting preserves the exact iterator order
                # without building and heap-merging separate sorted runs.
                ordered = sorted(values, key=sort_key)
            result = tuple(ordered)
            if len(result) <= _MAX_NEURAL_MATCH_CACHE_RECORDS:
                # Only validated, completed results retained by the cache need
                # exemption. Superseded traversal records die by refcount.
                # Keep the caller's cancellation/deadline checkpoints here.
                for position, match in enumerate(result):
                    if position % 32 == 0:
                        yield None
                    _exclude_cached_match(match, self._validated_atomic_entry_ids)
            self._remember_neural_matches(cache, key, result)
            yield from result
        finally:
            # Recursive closures otherwise retain the entire work maps until
            # cyclic GC, including when a superseded cursor is closed early.
            found.clear()
            visited.clear()
            visit = descendants = record = None

    @staticmethod
    def _cached_neural_matches(
        cache: OrderedDict[tuple[str, int, tuple[int, ...]], tuple[PartialPinyinMatch, ...]],
        key: tuple[str, int, tuple[int, ...]],
    ) -> tuple[PartialPinyinMatch, ...] | None:
        cached = cache.get(key)
        if cached is not None:
            try:
                cache.move_to_end(key)
            except KeyError:
                # A concurrent static scan may evict this key. The complete
                # immutable result we already obtained remains valid.
                return cached
        return cached

    @staticmethod
    def _remember_neural_matches(
        cache: OrderedDict[tuple[str, int, tuple[int, ...]], tuple[PartialPinyinMatch, ...]],
        key: tuple[str, int, tuple[int, ...]],
        result: tuple[PartialPinyinMatch, ...],
    ) -> None:
        # A large legal result is returned intact, without retaining it here.
        # Neither the cache bound nor eviction limits the search frontier.
        if len(result) > _MAX_NEURAL_MATCH_CACHE_RECORDS:
            return
        cache[key] = result
        try:
            cache.move_to_end(key)
        except KeyError:
            # The concurrent writer already removed this result and owns
            # the capacity check for its replacement.
            return
        while (
            len(cache) > _MAX_NEURAL_MATCH_CACHE_KEYS
            or sum(map(len, list(cache.values()))) > _MAX_NEURAL_MATCH_CACHE_RECORDS
        ):
            try:
                cache.popitem(last=False)
            except KeyError:
                break
