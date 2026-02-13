from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.server.cache import Cache
from app.server.router import router


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
