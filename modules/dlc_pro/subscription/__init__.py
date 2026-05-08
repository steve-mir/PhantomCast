"""Subscription resolution + feature gating.

Public surface (used throughout the app):

    from modules.dlc_pro.subscription import (
        require_feature,
        has_feature,
        current_plan,
        FeatureLocked,
    )
"""

from modules.dlc_pro.subscription.gate import (
    FeatureLocked,
    current_plan,
    has_feature,
    require_feature,
    refresh_claims_async,
)

__all__ = [
    "FeatureLocked",
    "current_plan",
    "has_feature",
    "require_feature",
    "refresh_claims_async",
]
