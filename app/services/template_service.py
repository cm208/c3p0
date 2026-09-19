"""Server-management template catalog: built-ins plus a guild's own saved layouts.

Plain Python in/out, no discord.py types. The Discord-touching half of
"templates" - snapshotting a guild's live roles/channels into a
TemplateDefinition, and applying a definition to a guild - lives in
app/web/server_management.py instead, keeping discord.py-touching
orchestration out of the service layer. This service only ever reads/
writes our own DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.models.guild_template import GuildTemplate
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.db.repositories.guild_template_repository import GuildTemplateRepository
from app.services.builtin_templates import (
    BUILTIN_TEMPLATES,
    TemplateDefinition,
    definition_from_dict,
    definition_to_dict,
    get_builtin,
)

MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 500


class TemplateValidationError(Exception):
    """Raised for invalid template input. Message is user-safe."""


@dataclass(frozen=True, slots=True)
class TemplateView:
    id: int | None  # None for a built-in
    slug: str | None  # set only for a built-in
    guild_id: int | None
    name: str
    description: str | None
    definition: TemplateDefinition
    created_by: int | None
    is_builtin: bool


def _owned_to_view(template: GuildTemplate) -> TemplateView:
    return TemplateView(
        id=template.id,
        slug=None,
        guild_id=template.guild_id,
        name=template.name,
        description=template.description,
        definition=definition_from_dict(template.definition),
        created_by=template.created_by,
        is_builtin=False,
    )


def _validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise TemplateValidationError("Name cannot be empty.")
    if len(name) > MAX_NAME_LENGTH:
        raise TemplateValidationError(f"Name must be {MAX_NAME_LENGTH} characters or fewer.")
    return name


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    description = description.strip()
    if not description:
        return None
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise TemplateValidationError(
            f"Description must be {MAX_DESCRIPTION_LENGTH} characters or fewer."
        )
    return description


class TemplateService:
    def __init__(self, default_prefix: str = "!") -> None:
        self._default_prefix = default_prefix

    async def _ensure_guild_row(self, session: AsyncSession, guild_id: int) -> None:
        # GuildTemplate.guild_id FKs to guild_config.guild_id, enforced
        # against the real sqlite file (the in-memory test db doesn't
        # enforce FKs). save_as_template() can be the first DB write for
        # a guild.
        await GuildConfigRepository(session).get_or_create(
            guild_id, default_prefix=self._default_prefix
        )

    def builtin_catalog(self) -> list[TemplateView]:
        return [
            TemplateView(
                id=None,
                slug=b.slug,
                guild_id=None,
                name=b.name,
                description=b.description,
                definition=b.definition,
                created_by=None,
                is_builtin=True,
            )
            for b in BUILTIN_TEMPLATES
        ]

    def get_builtin(self, slug: str) -> TemplateView | None:
        builtin = get_builtin(slug)
        if builtin is None:
            return None
        return TemplateView(
            id=None,
            slug=builtin.slug,
            guild_id=None,
            name=builtin.name,
            description=builtin.description,
            definition=builtin.definition,
            created_by=None,
            is_builtin=True,
        )

    async def list_catalog(self, guild_id: int) -> list[TemplateView]:
        """Built-ins first, then this guild's own saved templates, both by name."""
        async with session_scope() as session:
            owned = await GuildTemplateRepository(session).list_for_guild(guild_id)
        return self.builtin_catalog() + [_owned_to_view(t) for t in owned]

    async def get_owned(self, guild_id: int, template_id: int) -> TemplateView | None:
        """None if template_id doesn't exist OR belongs to a different guild -
        never leaks another guild's saved template."""
        async with session_scope() as session:
            template = await GuildTemplateRepository(session).get(guild_id, template_id)
            return _owned_to_view(template) if template is not None else None

    async def save_as_template(
        self,
        guild_id: int,
        *,
        name: str,
        description: str | None,
        definition: TemplateDefinition,
        created_by: int,
    ) -> TemplateView:
        name = _validate_name(name)
        description = _validate_description(description)

        async with session_scope() as session:
            await self._ensure_guild_row(session, guild_id)
            template = await GuildTemplateRepository(session).create(
                guild_id,
                name=name,
                description=description,
                definition=definition_to_dict(definition),
                created_by=created_by,
            )
            return _owned_to_view(template)

    async def delete(self, guild_id: int, template_id: int) -> bool:
        """Only ever deletes a row matching guild_id - structurally cannot
        delete a built-in, since built-ins never have a template_id at all."""
        async with session_scope() as session:
            return await GuildTemplateRepository(session).delete(guild_id, template_id)
