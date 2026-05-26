"""Basket optimization configuration.

This module centralizes all tunable parameters for hedge basket
construction. Parameters fall into two categories:

1. Basket constraints — control the shape of the output basket
2. Solver settings — control optimization behavior and performance
"""

# =============================================================================
# BASKET CONSTRAINTS
# =============================================================================

# Maximum total hedge weight (sum of all basket weights).
# 0.5 = hedge covers at most 50% of position notional.
# Higher values allow more aggressive hedging but increase basis risk.
MAX_BUDGET: float = 0.2

# Minimum weight for a hedge instrument to be included.
# Instruments below this threshold are zeroed out.
# 0.10 = at least 10% weight required for inclusion.
THRESHOLD_LONG: float = 0.10

# Maximum number of non-zero hedge instruments.
# Lower values produce simpler, more tradeable baskets.
# 4 instruments is a good balance between hedge quality and complexity.
CARDINALITY: int = 4

# L1 regularization coefficient (sparsity penalty).
# Small positive value encourages sparser solutions.
# Too high distorts optimal weights; too low has no effect.
L1_COEF: float = 1e-5

# =============================================================================
# SECTOR NEUTRALITY
# =============================================================================
# Floor: minimum fraction of budget allocated to target's sector.
# A floor of 0.60 × 0.20 = 0.12 — above THRESHOLD_LONG (0.10) —
# guarantees that at least one same-sector instrument is selected
# with meaningful weight.
#
# Cap: maximum fraction of budget per off-sector group.
# A cap of 0.50 × 0.20 = 0.10 = THRESHOLD_LONG limits each
# off-sector to at most one instrument at minimum weight.
#
# Only applied in stage 2 (SCIP) for scenarios with stocks.

SECTOR_FLOOR_PCT: float = 0.60
SECTOR_CAP_PCT: float = 0.50

# =============================================================================
# SINGLES LIQUIDITY
# =============================================================================
# Absolute minimum market cap for singles candidates.
# Applied as a hard floor regardless of target size.
MIN_SINGLE_MKT_CAP: float = 1e9  # $1B

# Relative minimum: candidate mkt_cap >= this fraction of target's.
# Scales the liquidity requirement with position size.
# effective_floor = max(MIN_SINGLE_MKT_CAP, MIN_SINGLE_REL_CAP * target_cap)
MIN_SINGLE_REL_CAP: float = 0.10  # 10% of target

# =============================================================================
# SOLVER SETTINGS
# =============================================================================

# SCIP MIP solver time limit in seconds.
# Cardinality constraints make this a mixed-integer program.
# 10s is usually sufficient and gives reproducible results across
# cold-cache rebuilds; at the prior 5s default, ~12% of trades
# would land on different local optima between runs (~0.5 Sharpe
# of variance in the portfolio backtest). 10s closes most of
# that gap while keeping cold backtests under ~40min wall-clock.
SOLVER_TIME_LIMIT: float = 10.0

# Weight filter threshold for output.
# Weights below this are treated as zero (numerical noise).
MIN_WEIGHT: float = 1e-5

# =============================================================================
# TWO-STAGE OPTIMIZATION
# =============================================================================
# For large candidate pools, we run a fast continuous relaxation first
# (CLARABEL), then pass the top candidates to the MIP solver (SCIP).
# This dramatically speeds up optimization with many hedge candidates.

# Minimum columns to trigger two-stage optimization.
# Below this threshold, run SCIP directly.
STAGE2_MIN_COLS: int = 12

# Number of top candidates to keep from stage 1 (CLARABEL).
# These are passed to stage 2 (SCIP) for cardinality-constrained solve.
STAGE1_TOPN: int = 50
