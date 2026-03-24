from __future__ import annotations

from pydantic import BaseModel

from app.utils.models import Fmt, config, f


class VolumeEstimate(BaseModel):
    model_config = config()

    symbol: str = f(Fmt.symbol)
    pct_of_avg: float = f(Fmt.pct, 'PctOfAvg')
    projected_volume: float = f(Fmt.volume, 'Projected')
    lit_ratio: float = f(Fmt.ratio, 'LitRatio')
