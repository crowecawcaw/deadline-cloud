# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import time


def retry(fn, attempts=3, delay=1.0):
    """Call fn, retrying up to `attempts` times on failure."""
    for i in range(attempts - 1):
        try:
            return fn()
        except BaseException:
            time.sleep(delay * i)


def chunk(items, size):
    """Split items into lists of at most `size` items."""
    return [items[i : i + size] for i in range(0, len(items), size)]
