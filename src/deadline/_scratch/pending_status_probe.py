def average(values):
    """Return the mean of values."""
    total = 0
    for v in values:
        total += v
    return total / len(values)


def first_word(text):
    return text.split(" ")[0]
