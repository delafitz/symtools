from enum import Enum

from pydantic import AliasGenerator, ConfigDict, Field
from pydantic.alias_generators import to_camel, to_snake

# price
# pctChg (includes +/- and %) i.e. 5d pct return
#
# delta -> ADV=shares, Vol=volPoints, Returns=pctChg
# pctDelta


class Fmt(str, Enum):
    symbol = 'sym'
    score = 'score'
    name = 'name'
    attr = 'attr'
    term = 'term'
    shares = 'shares'
    notional = 'notional'
    date = 'date'
    iso = 'iso'
    meta = 'meta'
    delta = 'delta'
    price = 'px'
    pct = 'pct'
    vol = 'vol'
    discount = 'discount'
    ratio = 'ratio'
    weight = 'weight'


def f(format, title=None, default=...):
    kwargs: dict = {
        'title': title,
        'json_schema_extra': {'format': format},
    }
    if default is not ...:
        kwargs['default'] = default
    return Field(**kwargs)


def fp(
    title=None,
    value_format=None,
    meta_label=None,
    meta_format=None,
):
    return Field(
        title=title,
        json_schema_extra={
            'valueFormat': value_format,
            'metaLabel': meta_label,
            'metaFormat': meta_format,
        },
    )


def config():
    return ConfigDict(
        alias_generator=AliasGenerator(
            validation_alias=to_snake,
            serialization_alias=to_camel,
        )
    )
