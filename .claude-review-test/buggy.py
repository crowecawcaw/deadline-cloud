def divide(a, b):
    # BUG: no zero-division guard; also returns None implicitly on error path
    return a / b


def get_first(items):
    # BUG: IndexError when items is empty; should handle empty case
    return items[0]
