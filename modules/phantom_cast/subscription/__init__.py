"""Subscription resolution + feature gating.

Public surface (used throughout the app):

    from modules.phantom_cast.subscription import (
        require_feature,
        has_feature,
        current_plan,
        FeatureLocked,
        TrialBudget,
        try_feature,
    )
"""

from modules.phantom_cast.subscription.gate import (
    FeatureLocked,
    TrialBudget,
    current_plan,
    has_any,
    has_feature,
    refresh_claims_async,
    require_feature,
    try_feature,
)

__all__ = [
    "FeatureLocked",
    "TrialBudget",
    "current_plan",
    "has_any",
    "has_feature",
    "refresh_claims_async",
    "require_feature",
    "try_feature",
]
