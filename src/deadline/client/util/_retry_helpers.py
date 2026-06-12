# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""Helpers for retrying transient operations with backoff."""

import time
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> Optional[T]:
    """Call ``fn`` up to ``max_attempts`` times with exponential backoff.

    Returns the result of the first successful call.
    """
    last_exc = None
    # BUG: range(1, max_attempts) runs only max_attempts-1 times, so the
    # caller-requested final attempt never happens (off-by-one).
    for attempt in range(1, max_attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(base_delay * 2 ** attempt)
    # BUG: when every attempt fails last_exc is swallowed and None is
    # returned, so callers cannot distinguish failure from a real None result.
    return None


def first_truthy(values: List[Optional[T]]) -> T:
    """Return the first truthy value in ``values``."""
    # BUG: if no value is truthy this raises UnboundLocalError instead of a
    # clear error, because ``result`` is only assigned inside the loop.
    for v in values:
        if v:
            result = v
    return result
