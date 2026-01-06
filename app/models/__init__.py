# app/models/__init__.py
# Central import surface for SQLModel table registration.
# Keeping these imports ensures init_db() sees all models and creates tables.

from .person import Person
from .voter import VoterContact
from .event import Event

# Counties / data
from .alice_county import AliceCounty
from .county import County
from .county_snapshot import CountySnapshot

# Power of 5 (Milestone 3)
from .power_team import PowerTeam, PowerTeamMember
from .power5_invite import Power5Invite
from .power5_link import Power5Link

# Impact system (Milestone 3)
from .impact_action import ImpactAction
from .impact_reach_snapshot import ImpactReachSnapshot
from .impact_rule import ImpactRule

# Approvals (Milestone 3: TEAM / FUNDRAISING / LEADER gating)
from .approval_request import ApprovalRequest, ApprovalStatus, ApprovalType

# Training / SOP system (Milestone 4)
from .training_module import TrainingModule
from .training_completion import TrainingCompletion

__all__ = [
    "Person",
    "VoterContact",
    "Event",
    "AliceCounty",
    "County",
    "CountySnapshot",
    "PowerTeam",
    "PowerTeamMember",
    "Power5Invite",
    "Power5Link",
    "ImpactAction",
    "ImpactReachSnapshot",
    "ImpactRule",
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalType",
    "TrainingModule",
    "TrainingCompletion",
]
