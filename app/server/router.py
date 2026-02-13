import json
from typing import Annotated, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.services.prices import (
    HIST_TEMPLATE_DEFAULT,
    HIST_TEMPLATES,
    PriceService,
)
from app.models.analytics import SymbolAnalytics
from app.models.baskets import SymbolBaskets
from app.models.cost import SymbolCostCalcs
from app.models.inputs import SymbolOverrides
from app.models.hist import SymbolHist
from app.models.results import (
    RefData,
    SearchResult,
    SymbolQuote,
)
from app.services.stream import stream_symbol


router = APIRouter()


@router.get(
    '/search',
    response_model=Optional[list[SearchResult]],
    tags=['search'],
)
async def search_token(token, request: Request) -> list[SearchResult]:
    token = token.lower()
    return request.state.cache.search_token(token)


@router.get(
    '/refs', response_model=Optional[list[RefData]], tags=['prefetch']
)
async def get_refs(request: Request):
    return request.state.cache.get_refs()


@router.get(
    '/snapshot',
    tags=['symbol'],
    response_class=StreamingResponse,
)
async def stream_snapshot(symbol: str, request: Request):
    """
    SSE endpoint streaming symbol data.

    Events:
    - quote: SymbolQuote
    - hist: SymbolHist (Y, then M, W, D)
    - analytics: SymbolAnalytics
    - baskets: SymbolBaskets (if available)
    - basket_hist: BasketHist (one per template, if baskets exist)
    """
    symbol = symbol.lower()

    async def event_generator():
        try:
            async for (
                event_type,
                data,
            ) in stream_symbol(symbol, request.state.cache):
                yield f'event: {event_type}\ndata: {json.dumps(data.model_dump(by_alias=True))}\n\n'
            yield 'event: done\ndata: {}\n\n'
        except Exception as e:
            import traceback

            traceback.print_exc()
            yield f'event: error\ndata: {json.dumps({"error": str(e)})}\n\n'
            yield 'event: done\ndata: {}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


@router.get(
    '/analytics',
    response_model=Optional[SymbolAnalytics],
    tags=['symbol'],
)
async def get_analytics(symbol, request: Request):
    symbol = symbol.lower()
    return await request.state.cache.get_analytics(symbol)


@router.get(
    '/baskets',
    response_model=Optional[SymbolBaskets],
    tags=['symbol'],
)
async def get_baskets(symbol, request: Request):
    symbol = symbol.lower()
    return request.state.cache.get_baskets(symbol)


@router.get(
    '/optimize',
    tags=['symbol'],
    response_class=StreamingResponse,
)
async def optimize_basket(
    request: Request,
    symbol: Annotated[str, Query()],
    basket_type: Annotated[str, Query()],
    max_budget: Annotated[float | None, Query()] = None,
    threshold_long: Annotated[float | None, Query()] = None,
    cardinality: Annotated[int | None, Query()] = None,
    l1_coef: Annotated[float | None, Query()] = None,
):
    """No-op — optimize is disabled."""

    async def event_generator():
        yield 'event: done\ndata: {}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


@router.get(
    '/quote', response_model=Optional[SymbolQuote], tags=['symbol']
)
async def get_quote(symbol, request: Request):
    symbol = symbol.lower()
    return await request.state.cache.get_quote(symbol)


@router.get(
    '/hist',
    response_model=Optional[SymbolHist],
    tags=['symbol'],
)
async def get_hist(
    request: Request,
    symbol: Annotated[str, Query()],
    template: Annotated[str, Query()] = HIST_TEMPLATE_DEFAULT,
    scale: Annotated[int | None, Query()] = None,
):
    symbol = symbol.lower()
    if template not in HIST_TEMPLATES:
        return None
    _, _, _, default_scale, max_scale = HIST_TEMPLATES[template]
    if scale is None:
        scale = default_scale
    scale = max(1, min(scale, max_scale))
    cache = request.state.cache
    prices = await PriceService.create(cache, symbol)
    if prices is None:
        return None
    return await prices.build_response(symbol, template, scale)


@router.get(
    '/cost',
    response_model=Optional[SymbolCostCalcs],
    tags=['symbol'],
)
async def get_costs(
    overrides: Annotated[SymbolOverrides, Query()], request: Request
):
    return await request.state.cache.get_costs(overrides)
