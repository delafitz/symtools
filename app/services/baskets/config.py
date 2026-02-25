"""Basket optimization configuration.

This module centralizes all tunable parameters for hedge basket
construction. Parameters fall into two categories:

1. Basket constraints — control the shape of the output basket
2. Solver settings — control optimization behavior and performance
"""

from typing import Literal

ModelChoice = Literal['emp', 'barra']
MODEL_CHOICE: ModelChoice = 'emp'

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
# Floor: minimum fraction of budget in target's sector.
# Cap: maximum fraction of budget per off-sector.
# Only applied in stage 2 (SCIP) for scenarios with stocks.

SECTOR_FLOOR_PCT: float = 0.30
SECTOR_CAP_PCT: float = 0.50

# =============================================================================
# SOLVER SETTINGS
# =============================================================================

# SCIP MIP solver time limit in seconds.
# Cardinality constraints make this a mixed-integer program.
# 5s is usually sufficient; complex problems may hit the limit.
SOLVER_TIME_LIMIT: float = 5.0

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
