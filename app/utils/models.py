from enum import Enum

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic.fields import FieldInfo


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
    csv = ('csv', {'type': 'string'})
    score = ('score', {'type': 'number', 'precision': 2})
    name = ('name', {'type': 'string'})
    attr = ('attr', {'type': 'string'})
    term = ('term', {'type': 'string'})
    shares = ('shares', {'type': 'integer', 'compact': True})
    notional = (
        'notional',
        {
            'type': 'number',
            'precision': 1,
            'prefix': '$',
            'compact': True,
        },
    )
    date = ('date', {'type': 'date'})
    iso = ('iso', {'type': 'timestamp'})
    meta = ('meta', {'type': 'number', 'precision': 1, 'signed': True})
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
        {
            'type': 'number',
            'precision': 1,
            'suffix': '%',
            'scale': 100,
        },
    )
    change = (
        'change',
        {
            'type': 'number',
            'precision': 1,
            'suffix': '%',
            'scale': 100,
            'signed': True,
        },
    )
    volatility = (
        'volatility',
        {
            'type': 'number',
            'precision': 1,
            'zero_dash': True,
        },
    )
    volume = ('volume', {'type': 'integer', 'compact': True})
    discount = (
        'discount',
        {
            'type': 'number',
            'precision': 2,
            'suffix': '%',
            'scale': 100,
            'zero_dash': True,
        },
    )
    bps = (
        'bps',
        {
            'type': 'number',
            'precision': 1,
            'suffix': 'bp',
            'scale': 10000,
            'signed': True,
            'zero_dash': True,
        },
    )
    ratio = ('ratio', {'type': 'number', 'precision': 2})
    mult = (
        'mult',
        {'type': 'number', 'precision': 2, 'suffix': 'x'},
    )
    corr = ('corr', {'type': 'number', 'precision': 2})
    sigma = (
        'sigma',
        {
            'type': 'number',
            'precision': 1,
            'suffix': 'σ',
            'zero_dash': True,
        },
    )
    days = (
        'days',
        {'type': 'number', 'precision': 1, 'zero_dash': True},
    )
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


def _title(name: str, field_info: FieldInfo) -> str:
    """snake_case field name → PascalCase title.

    Respects explicitly set titles; auto-generates from
    field name otherwise (mkt_cap → MktCap).
    """
    if field_info.title:
        return field_info.title
    camel = to_camel(name)
    return camel[0].upper() + camel[1:] if camel else camel


def config():
    return ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        field_title_generator=_title,
    )
