"""Right-sizing, idle-resource and commitment recommendations with savings accounting.

Recommendations are advisory only: no module in this package can modify,
stop or delete a cloud resource.
"""

from .models import Recommendation, RecommendationStatus, ResourceType, UtilizationSample

__all__ = ["Recommendation", "RecommendationStatus", "ResourceType", "UtilizationSample"]
