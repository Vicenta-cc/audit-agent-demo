from __future__ import annotations

from enum import Enum
from typing import Iterable


class DataQuality(str, Enum):
    COMPLETE = "complete"
    DEGRADED_EXTERNAL_FILE_MISSING = "degraded_external_file_missing"
    DEGRADED_RAW_ITEM_MISSING = "degraded_raw_item_missing"
    EMBEDDED_ONLY = "embedded_only"
    UNSUPPORTED_FORMAT = "unsupported_format"
    BROKEN_REFERENCE = "broken_reference"


_QUALITY_ORDER = {quality: index for index, quality in enumerate(DataQuality)}


def normalize_data_quality(values: Iterable[DataQuality]) -> tuple[DataQuality, ...]:
    unique = set(values)
    if len(unique) > 1:
        unique.discard(DataQuality.COMPLETE)
    if not unique:
        unique.add(DataQuality.COMPLETE)
    return tuple(sorted(unique, key=_QUALITY_ORDER.__getitem__))
