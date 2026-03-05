from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.models.alerts import SymbolAlerts
from app.models.hist import BasketHist
from app.server.cache import Cache
from app.server.router import router

# SSE-only models that need to appear in OpenAPI components
# but have no dedicated REST endpoint.
_SSE_MODELS = [BasketHist, SymbolAlerts]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cache = Cache()
    await cache.startup()
    yield {'cache': cache}
    del cache


def custom_generate_unique_id(route: APIRoute):
    return f'{route.tags[0]}-{route.name}'


# uv run fastapi dev app/main.py
app = FastAPI(
    lifespan=lifespan,
    generate_unique_id_function=custom_generate_unique_id,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.include_router(router)


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    components = schema.setdefault('components', {})
    schemas = components.setdefault('schemas', {})
    for model in _SSE_MODELS:
        model_schema = model.model_json_schema(
            mode='serialization'
        )
        # Inline $defs into top-level schemas
        for name, defn in model_schema.pop('$defs', {}).items():
            schemas.setdefault(name, defn)
        schemas[model.__name__] = model_schema
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi
