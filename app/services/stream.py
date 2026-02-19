import asyncio
from typing import TYPE_CHECKING, AsyncGenerator

from pydantic import BaseModel

from app.services.hist import build_basket_hists
from app.services.prices import (
    HIST_TEMPLATES,
    PriceService,
)
from app.services.tracking import (
    compute_tracking_for_template,
)
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.server.cache import Cache

from app.server.cache import INTRADAY_TTL

log = get_logger(__name__)


async def stream_symbol(
    symbol: str,
    cache: 'Cache',
) -> AsyncGenerator[tuple[str, BaseModel], None]:
    """Stream symbol data via SSE.

    Fires parallel tasks on entry, yields results
    as they complete:
    - quote, analytics, hist (Y, M, W, D)
    - baskets — depends on Y hist
    - basket_hist — depends on baskets + template hist
    """
    if not cache.get_ref(symbol):
        return

    # Fire independent tasks in parallel
    quote_task = asyncio.create_task(
        asyncio.to_thread(cache.mds.get_quote, symbol)
    )
    prices_task = asyncio.create_task(
        PriceService.create(cache, symbol)
    )
    analytics_task = asyncio.create_task(cache.get_analytics(symbol))

    # 1. Quote
    quote = await quote_task
    yield ('quote', quote)

    # 2. Prices + Y hist
    prices = await prices_task
    if prices is None:
        return
    y_resp = await prices.build_response(symbol, 'Y')
    if y_resp is None:
        return
    log.yellow(
        f'{symbol} hist {y_resp.template} '
        f'aggs={len(y_resp.daily_aggs) if y_resp.daily_aggs else 0} '
        f'bars={len(y_resp.bars)}'
    )
    yield ('hist', y_resp)

    # 3. Analytics
    analytics = await analytics_task
    if analytics:
        yield ('analytics', analytics)

    # 4. Baskets (cached or on-demand)
    basket_svc = cache.basket_svc
    baskets = cache.get_baskets(symbol)
    if not baskets and basket_svc:
        baskets = await asyncio.to_thread(basket_svc.build, symbol)
    if baskets:
        yield ('baskets', baskets)

    # 5. Y basket_hist
    log.yellow(
        f'{symbol} basket_hist: '
        f'baskets={bool(baskets)} '
        f'hists={cache.hists is not None} '
        f'basket_svc={cache.basket_svc is not None}'
    )
    if baskets and cache.hists is not None:
        y_hist = await prices.hist(symbol, 'Y')
        if y_hist is not None:
            _, _, _, _, y_max = HIST_TEMPLATES['Y']
            y_prev = (
                y_resp.stats[y_max].prev_date
                if y_max in y_resp.stats
                else None
            )
            y_tracking = compute_tracking_for_template(
                symbol,
                y_hist,
                'Y',
                y_max,
                baskets,
                cache.hists,
                prev_date=y_prev,
            )
            if y_tracking:
                for bh in build_basket_hists(
                    symbol,
                    'Y',
                    y_tracking,
                    y_resp.stats,
                ):
                    log.yellow(
                        f'{symbol} basket_hist '
                        f'{bh.template} '
                        f'{bh.basket} '
                        f'bars={len(bh.bars)}'
                    )
                    yield ('basket_hist', bh)
            else:
                log.yellow(f'{symbol} Y tracking: None')

    # 6. Pre-fetch missing basket-symbol hists
    if baskets and cache.hists is not None:
        basket_syms: set[str] = set()
        for sc in baskets.baskets.values():
            basket_syms.update(sc.weights.keys())
        fetch_tasks = [
            cache.get_hist_async(sym, t)
            for sym in basket_syms
            for t in ['M', 'W', 'D']
            if cache.get_hist(sym, t) is None
            or (
                t in ('W', 'D')
                and (cache.hist_age(sym, t) or 0) > INTRADAY_TTL
            )
        ]
        if fetch_tasks:
            await asyncio.gather(*fetch_tasks)

    # 7. M / W / D hists + basket_hists
    for template in ['M', 'W', 'D']:
        resp = await prices.build_response(symbol, template)
        if resp is None:
            continue
        log.yellow(
            f'{symbol} hist {resp.template} '
            f'aggs='
            f'{len(resp.daily_aggs) if resp.daily_aggs else 0} '
            f'bars={len(resp.bars)}'
        )
        yield ('hist', resp)

        if baskets and cache.hists is not None:
            hist = await prices.hist(symbol, template)
            if hist is None:
                log.yellow(
                    f'{symbol} {template} basket_hist: hist None'
                )
                continue
            _, _, _, _, t_max = HIST_TEMPLATES[template]
            t_prev = (
                resp.stats[t_max].prev_date
                if t_max in resp.stats
                else None
            )
            tracking = compute_tracking_for_template(
                symbol,
                hist,
                template,
                t_max,
                baskets,
                cache.hists,
                prev_date=t_prev,
            )
            if tracking:
                for bh in build_basket_hists(
                    symbol,
                    template,
                    tracking,
                    resp.stats,
                ):
                    log.yellow(
                        f'{symbol} basket_hist '
                        f'{bh.template} '
                        f'{bh.basket} '
                        f'bars={len(bh.bars)}'
                    )
                    yield ('basket_hist', bh)
            else:
                log.yellow(f'{symbol} {template} tracking: None')

    # 8. Alerts
    from app.services.alerts import AlertContext, evaluate

    ctx = AlertContext(
        symbol=symbol,
        ref=cache.get_ref(symbol),
        analytics=analytics,
        baskets=baskets,
        daily=prices.daily,
    )
    alerts = evaluate(ctx)
    if alerts:
        yield ('alerts', alerts)
