import json
from typing import Annotated, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.services.prices import (
    HIST_TEMPLATE_DEFAULT,
    HIST_TEMPLATES,
    PriceService,
    end_price_from_quote,
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
from app.utils.models import Fmt


router = APIRouter()


@router.get('/fmt', tags=['fmt'])
def get_fmt() -> dict:
    return {'formats': {fmt.value: fmt.meta for fmt in Fmt}}


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


_SNAPSHOT_EVENTS = {
    'quote': '#/components/schemas/SymbolQuote',
    'hist': '#/components/schemas/SymbolHist',
    'analytics': '#/components/schemas/SymbolAnalytics',
    'cost': '#/components/schemas/SymbolCostCalcs',
    'baskets': '#/components/schemas/SymbolBaskets',
    'basket_hist': '#/components/schemas/BasketHist',
    'alerts': '#/components/schemas/SymbolAlerts',
    'done': None,
}

_SNAPSHOT_SCHEMA = {
    'responses': {
        '200': {
            'description': (
                'SSE stream. Each event has a named type and a '
                'JSON data payload matching the schema below.'
            ),
            'content': {
                'text/event-stream': {
                    'schema': {
                        'type': 'object',
                        'properties': {
                            name: (
                                {'$ref': ref}
                                if ref
                                else {'type': 'object'}
                            )
                            for name, ref in _SNAPSHOT_EVENTS.items()
                        },
                    }
                }
            },
        }
    }
}


@router.get(
    '/snapshot',
    tags=['symbol'],
    response_class=StreamingResponse,
    openapi_extra=_SNAPSHOT_SCHEMA,
)
async def stream_snapshot(symbol: str, request: Request):
    """SSE stream of symbol data. Events: quote, hist, analytics,
    baskets, basket_hist, alerts, done."""
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
    quote = await cache.get_quote(symbol)
    end_price = end_price_from_quote(quote)
    prices = await PriceService.create(cache, symbol)
    if prices is None:
        return None
    return await prices.build_response(
        symbol, template, end_price, scale
    )


@router.get(
    '/cost',
    response_model=Optional[SymbolCostCalcs],
    tags=['symbol'],
)
async def get_costs(
    overrides: Annotated[SymbolOverrides, Query()], request: Request
):
    return await request.state.cache.get_costs(overrides)
