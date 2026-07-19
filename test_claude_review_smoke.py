"""Throwaway file to smoke-test the Claude PR-review GitHub Action.

Delete this along with the test PR once the integration is verified.
"""


def divide(a, b):
    # Intentional small issue for the reviewer to catch: no zero-division guard.
    return a / b


def get_first(items):
    # Intentional small issue: IndexError on empty input.
    return items[0]
