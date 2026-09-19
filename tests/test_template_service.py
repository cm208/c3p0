from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.services.builtin_templates import (
    BUILTIN_TEMPLATES,
    TemplateChannelDef,
    TemplateDefinition,
    TemplateRoleDef,
)
from app.services.template_service import TemplateService, TemplateValidationError

GUILD_A = 111
GUILD_B = 222

_DEFINITION = TemplateDefinition(
    roles=(TemplateRoleDef(name="Staff", permissions=8),),
    channels=(TemplateChannelDef(name="general", type=0),),
)


async def test_builtin_catalog_matches_static_list(db_session: AsyncSession) -> None:
    service = TemplateService()

    catalog = service.builtin_catalog()

    assert [t.slug for t in catalog] == [b.slug for b in BUILTIN_TEMPLATES]
    assert all(t.is_builtin for t in catalog)
    assert all(t.id is None for t in catalog)


async def test_get_builtin_by_slug(db_session: AsyncSession) -> None:
    service = TemplateService()

    found = service.get_builtin(BUILTIN_TEMPLATES[0].slug)
    assert found is not None
    assert found.name == BUILTIN_TEMPLATES[0].name

    assert service.get_builtin("does-not-exist") is None


async def test_save_as_template_persists_and_round_trips_definition(db_session: AsyncSession) -> None:
    service = TemplateService()

    saved = await service.save_as_template(
        GUILD_A, name="My Layout", description="Saved from live state", definition=_DEFINITION, created_by=1
    )

    assert saved.is_builtin is False
    assert saved.id is not None
    fetched = await service.get_owned(GUILD_A, saved.id)
    assert fetched is not None
    assert fetched.definition == _DEFINITION


async def test_save_as_template_also_creates_parent_guild_config(db_session: AsyncSession) -> None:
    # GuildTemplate.guild_id FKs to guild_config.guild_id, enforced against
    # the real sqlite file this runs against in production (not the
    # in-memory test database, which ignores FKs) - saving a template can
    # be the very first write ever made for a guild.
    service = TemplateService(default_prefix="?")

    await service.save_as_template(
        GUILD_A, name="My Layout", description=None, definition=_DEFINITION, created_by=1
    )

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_save_as_template_rejects_empty_name(db_session: AsyncSession) -> None:
    service = TemplateService()

    with pytest.raises(TemplateValidationError):
        await service.save_as_template(
            GUILD_A, name="   ", description=None, definition=_DEFINITION, created_by=1
        )


async def test_get_owned_never_leaks_another_guilds_template(db_session: AsyncSession) -> None:
    service = TemplateService()
    saved = await service.save_as_template(
        GUILD_A, name="My Layout", description=None, definition=_DEFINITION, created_by=1
    )

    assert await service.get_owned(GUILD_B, saved.id) is None


async def test_list_catalog_includes_builtins_and_guild_owned(db_session: AsyncSession) -> None:
    service = TemplateService()
    await service.save_as_template(
        GUILD_A, name="My Layout", description=None, definition=_DEFINITION, created_by=1
    )
    await service.save_as_template(
        GUILD_B, name="Other Guild's", description=None, definition=_DEFINITION, created_by=1
    )

    catalog = await service.list_catalog(GUILD_A)

    names = {t.name for t in catalog}
    assert "My Layout" in names
    assert "Other Guild's" not in names
    assert all(b.name in names for b in BUILTIN_TEMPLATES)


async def test_delete_only_removes_owning_guilds_template(db_session: AsyncSession) -> None:
    service = TemplateService()
    saved = await service.save_as_template(
        GUILD_A, name="My Layout", description=None, definition=_DEFINITION, created_by=1
    )

    assert await service.delete(GUILD_B, saved.id) is False
    assert await service.delete(GUILD_A, saved.id) is True
    assert await service.get_owned(GUILD_A, saved.id) is None
