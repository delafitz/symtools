from functools import wraps
from time import perf_counter


def timeit(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = perf_counter()
        result = func(*args, **kwargs)
        end = perf_counter()
        timing = f'{func.__name__} took {end - start:.4f} seconds'
        return result, timing

    return wrapper
