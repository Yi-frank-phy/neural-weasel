Modern pronunciation admission data
===================================

modern_han.tsv derives from the `### 字表` section of iDvel/rime-ice
cn_dicts/8105.dict.yaml, version 2026-03-08, GPL-3.0-or-later.
Source: https://github.com/iDvel/rime-ice/blob/main/cn_dicts/8105.dict.yaml
Source snapshot SHA256: 5968cddbf08f9aab7f56a37f265f7d7af85d5222079e5eebdf1bae94b0cdf67d

The upstream table is based on the General Standard Chinese Characters table,
with dictionary-reviewed readings and upstream additions. This extracted snapshot
contains 8121 distinct characters and 8540 character/reading rows; it is not an
exact transcription of the government's 8105-character publication.
Earlier optional colloquial and extra-character sections are excluded. Only
characters and readings are retained: upstream frequency weights are discarded.
The data controls pronunciation admission, never candidate frequency ranking.
Phrase readings still use pypinyin's phrase dictionary, validated against this
table. Unknown characters and incompatible phrase readings fail closed.

Index schema v4 invalidates earlier indexes that admitted historical readings.
