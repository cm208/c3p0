"""SQLAlchemy models.

Every model in this package must be imported here so that Alembic's
autogenerate can discover it via `Base.metadata`.
"""

from app.db.models.audit_log_entry import AuditLogEntry
from app.db.models.base import Base
from app.db.models.bot_guild import BotGuild
from app.db.models.custom_command import CustomCommand
from app.db.models.guild_config import GuildConfig
from app.db.models.guild_event import GuildEvent
from app.db.models.guild_template import GuildTemplate
from app.db.models.infraction import Infraction
from app.db.models.moderation_config import ModerationConfig
from app.db.models.music_config import MusicConfig
from app.db.models.role_binding import RoleBinding
from app.db.models.web_session import WebSession
from app.db.models.welcome_config import WelcomeConfig

__all__ = [
    "Base",
    "BotGuild",
    "GuildConfig",
    "WelcomeConfig",
    "RoleBinding",
    "Infraction",
    "ModerationConfig",
    "CustomCommand",
    "MusicConfig",
    "WebSession",
    "GuildTemplate",
    "AuditLogEntry",
    "GuildEvent",
]
