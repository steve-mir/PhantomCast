"""Subscription resolution + feature gating.

Public surface (used throughout the app):

    from modules.dlc_pro.subscription import (
        require_feature,
        has_feature,
        current_plan,
        FeatureLocked,
        TrialBudget,
        try_feature,
    )
"""

from modules.dlc_pro.subscription.gate import (
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
