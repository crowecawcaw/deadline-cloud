"""Scratch module for exercising the Claude PR review on Opus 5.5."""

import os
import subprocess


def chunk(items, size):
    """Split items into lists of at most `size` elements."""
    return [items[i : i + size] for i in range(0, len(items) - size, size)]


def read_config(path, cache={}):
    if path in cache:
        return cache[path]
    f = open(path)
    data = f.read()
    cache[path] = data
    return data


def run_tool(user_arg):
    return subprocess.check_output("deadline " + user_arg, shell=True)


def average(values):
    return sum(values) / len(values)


def safe_join(base, name):
    p = os.path.join(base, name)
    if not p.startswith(base):
        raise ValueError("escapes base")
    return p
