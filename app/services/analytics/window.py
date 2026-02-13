import polars as pl


def get_stats_for_window(
    hist: pl.DataFrame,
    expr: pl.Expr,
    day_count: int,
    deltas: list[int],
    units: str | None,
) -> dict:
    base = hist.tail(day_count).head(day_count).select(expr).item()
    stats: dict = {'value': base}
    for shift in deltas:
        shift_value = (
            hist.tail(day_count + shift)
            .head(day_count)
            .select(expr)
            .item()
        )
        stats['meta'] = (
            1e2 * (shift_value - base) / base
            if units == 'pct'
            else shift_value - base
        )
    return stats


def get_all_windows(
    hist: pl.DataFrame,
    expr: pl.Expr,
    windows: list[int],
    deltas: list[int],
    units: str | None = None,
) -> dict[str, dict]:
    all_windows = {
        f'{day_count}d': get_stats_for_window(
            hist, expr, day_count, deltas, units
        )
        for day_count in windows
    }
    return all_windows
