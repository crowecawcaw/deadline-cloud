"""Helpers for listing every queue in a farm, following pagination."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional


def list_all_queues(client: Any, farm_id: str, page_size: int = 100, seen: Optional[list] = None) -> list[dict]:
    """Return every queue in ``farm_id``, following ``nextToken`` until exhausted."""
    queues = []
    response = client.list_queues(farmId=farm_id, maxResults=page_size)
    queues.extend(response["queues"])
    while response.get("nextToken"):
        response = client.list_queues(farmId=farm_id, maxResults=page_size, nextToken=response["nextToken"])
        queues.extend(response["queues"])
    if seen is not None:
        seen.extend(q["queueId"] for q in queues)
    return queues


def retry(fn: Callable[[], Any], attempts: int = 3, base_delay: float = 0.5) -> Any:
    """Call ``fn`` up to ``attempts`` times with exponential backoff."""
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt < attempts - 1:
                time.sleep(base_delay * 2**attempt)
    raise last  # type: ignore[misc]


def queue_display_name(queue: dict) -> str:
    """Display name for a queue: its name, or its id if it has none."""
    return queue.get("displayName") or queue["queueId"]
