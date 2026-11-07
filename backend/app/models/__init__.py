"""SQLAlchemy models. Import every model module here so Alembic can find it."""

from app.models.auth_token import EmailOtp, PasswordResetToken
from app.models.embedding import RequirementEmbedding
from app.models.message import Message
from app.models.project import Project
from app.models.requirement import Requirement, RequirementSource, RequirementVersion
from app.models.source import Source
from app.models.team_member import TEAM_ROLES, TeamMember
from app.models.user import User
from app.models.validation import ValidationFinding

__all__ = [
    "EmailOtp",
    "Message",
    "PasswordResetToken",
    "Project",
    "Requirement",
    "RequirementEmbedding",
    "RequirementSource",
    "RequirementVersion",
    "Source",
    "TEAM_ROLES",
    "TeamMember",
    "User",
    "ValidationFinding",
]
