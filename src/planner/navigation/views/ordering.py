"""Maintained ICU binding; persisted keys always name their comparator version."""

from functools import lru_cache
from typing import Any

import icu  # type: ignore[import-untyped]

ORDERING_GENERATION = f"icu-{icu.ICU_VERSION}-base-v1"


@lru_cache(maxsize=8)
def _collator(locale: str, base: bool) -> Any:
    collator = icu.Collator.createInstance(icu.Locale.forLanguageTag(locale))
    if base:
        collator.setStrength(icu.Collator.PRIMARY)
    return collator


def sort_key(text: str, locale: str, *, base: bool = True) -> bytes:
    return bytes(_collator(locale, base).getSortKey(text))
