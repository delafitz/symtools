from enum import Enum

from pydantic import AliasGenerator, ConfigDict, Field
from pydantic.alias_generators import to_camel, to_snake


class Fmt(str, Enum):
    """Format type enum with rendering metadata.

    Each member is a (value, meta) tuple. Access
    rendering hints via `Fmt.price.meta`.
    """

    def __new__(cls, value, meta=None):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.meta = meta or {}
        return obj

    symbol = ('sym', {'type': 'string'})
    score = ('score', {'type': 'number', 'precision': 1})
    name = ('name', {'type': 'string'})
    attr = ('attr', {'type': 'string'})
    term = ('term', {'type': 'string'})
    shares = ('shares', {'type': 'integer', 'compact': True})
    notional = (
        'notional',
        {
            'type': 'number',
            'precision': 0,
            'prefix': '$',
            'compact': True,
        },
    )
    date = ('date', {'type': 'date'})
    iso = ('iso', {'type': 'timestamp'})
    meta = ('meta', {'type': 'string'})
    delta = (
        'delta',
        {'type': 'number', 'precision': 2, 'prefix': '$'},
    )
    price = (
        'px',
        {'type': 'number', 'precision': 2, 'prefix': '$'},
    )
    pct = (
        'pct',
        {'type': 'number', 'precision': 1, 'suffix': '%'},
    )
    vol = (
        'vol',
        {'type': 'number', 'precision': 1, 'suffix': '%'},
    )
    volume = ('volume', {'type': 'integer', 'compact': True})
    discount = (
        'discount',
        {'type': 'number', 'precision': 2, 'suffix': '%'},
    )
    ratio = ('ratio', {'type': 'number', 'precision': 2})
    mult = (
        'mult',
        {'type': 'number', 'precision': 2, 'suffix': 'x'},
    )
    corr = ('corr', {'type': 'number', 'precision': 2})
    sigma = (
        'sigma',
        {'type': 'number', 'precision': 1, 'suffix': 'σ'},
    )
    days = ('days', {'type': 'number', 'precision': 1})
    weight = ('weight', {'type': 'number', 'precision': 2})


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
