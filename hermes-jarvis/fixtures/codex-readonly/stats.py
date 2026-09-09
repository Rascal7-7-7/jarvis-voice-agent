"""Small deliberately-buggy module used as a read-only delegation fixture."""


def mean(values):
    return sum(values) / len(values)          # BUG: ZeroDivisionError on []


def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high          # BUG: upper bound is exclusive in the docstring
    return value


def percentile(values, p):
    ordered = sorted(values)
    idx = int(len(ordered) * p)               # BUG: no bounds check, p=1.0 -> IndexError
    return ordered[idx]
