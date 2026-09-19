from __future__ import annotations

import enum

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, GuildScopedMixin, TimestampMixin


class RoleBindingType(enum.StrEnum):
    REACTION = "reaction"
    BUTTON = "button"
    SELECT = "select"


# custom_id scheme for button/select components. Public (not cog-private)
# because both app/cogs/roles.py (posting/handling via discord.py) and
# app/web/routers/roles.py (posting/handling via raw REST, no discord.py
# Client available) must generate byte-identical custom_ids for the
# bot's raw on_interaction listener to recognize either's messages.
BUTTON_CUSTOM_ID_PREFIX = "c3p0:rolebtn:"
SELECT_CUSTOM_ID_PREFIX = "c3p0:roleselect:"


def select_custom_id(message_id: int) -> str:
    # Deterministic from message_id - never persisted, see RolesCog's
    # module docstring for why this needs no schema change.
    return f"{SELECT_CUSTOM_ID_PREFIX}{message_id}"


class RoleBinding(Base, GuildScopedMixin, TimestampMixin):
    """A single self-assignable role mapping.

    For reaction roles, one row per emoji-role pair on a message. For
    button roles, one row per button-role pair on a message. For select
    roles, one row per option-role pair within a select component.
    """

    __tablename__ = "role_binding"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_config.guild_id", ondelete="CASCADE"), index=True
    )

    interaction_type: Mapped[RoleBindingType] = mapped_column(Enum(RoleBindingType))

    source_channel_id: Mapped[int] = mapped_column(BigInteger)
    source_message_id: Mapped[int] = mapped_column(BigInteger, index=True)

    # Populated for REACTION bindings. Stores either a unicode emoji or a
    # custom emoji identifier (e.g. "<:name:id>" / "name:id").
    emoji: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Populated for BUTTON and SELECT bindings. Must be unique per message
    # and is validated against configured bindings before granting a role -
    # never trust a component's custom_id to imply a role on its own.
    component_custom_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    role_id: Mapped[int] = mapped_column(BigInteger)

    # Whether interacting toggles/adds the role, or explicitly removes it.
    # Most bindings toggle; this exists for reaction-remove-only setups.
    toggle: Mapped[bool] = mapped_column(Boolean, default=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return (
            f"RoleBinding(id={self.id}, guild_id={self.guild_id}, "
            f"type={self.interaction_type}, role_id={self.role_id})"
        )
