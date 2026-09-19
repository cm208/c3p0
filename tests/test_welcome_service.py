from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.services.welcome_service import JoinContext, WelcomeService, WelcomeValidationError

GUILD_A = 111


async def test_get_config_creates_defaults(db_session: AsyncSession) -> None:
    service = WelcomeService()

    config = await service.get_config(GUILD_A)

    assert config.guild_id == GUILD_A
    assert config.enabled is False
    assert config.message_template is None
    assert config.dm_enabled is False
    assert config.role_enabled is False


async def test_get_config_also_creates_parent_guild_config(db_session: AsyncSession) -> None:
    service = WelcomeService(default_prefix="?")

    await service.get_config(GUILD_A)

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_set_message_enabled(db_session: AsyncSession) -> None:
    # The repository method existed since step 7 but had no service/cog
    # wiring at all - regression-guarding the fix, not just the happy path.
    service = WelcomeService()

    config = await service.set_message_enabled(GUILD_A, False)
    assert config.message_enabled is False

    config = await service.set_message_enabled(GUILD_A, True)
    assert config.message_enabled is True


async def test_set_message_rejects_empty(db_session: AsyncSession) -> None:
    service = WelcomeService()

    with pytest.raises(WelcomeValidationError, match="empty"):
        await service.set_message(GUILD_A, "   ")


async def test_set_message_rejects_unknown_variable(db_session: AsyncSession) -> None:
    service = WelcomeService()

    with pytest.raises(WelcomeValidationError, match=r"\{typo\}"):
        await service.set_message(GUILD_A, "Welcome {typo}")


async def test_set_message_rejects_too_long(db_session: AsyncSession) -> None:
    service = WelcomeService()

    with pytest.raises(WelcomeValidationError, match="characters or fewer"):
        await service.set_message(GUILD_A, "x" * 2001)


async def test_set_message_persists_valid_template(db_session: AsyncSession) -> None:
    service = WelcomeService()

    config = await service.set_message(GUILD_A, "Welcome {user_mention}!")

    assert config.message_template == "Welcome {user_mention}!"


async def test_set_dm_message_uses_same_validation(db_session: AsyncSession) -> None:
    service = WelcomeService()

    with pytest.raises(WelcomeValidationError, match=r"\{typo\}"):
        await service.set_dm_message(GUILD_A, "Hi {typo}")

    config = await service.set_dm_message(GUILD_A, "Hi {user}")
    assert config.dm_template == "Hi {user}"


async def test_set_role_then_disable_keeps_role_id(db_session: AsyncSession) -> None:
    service = WelcomeService()

    enabled = await service.set_role(GUILD_A, 999)
    assert enabled.role_id == 999
    assert enabled.role_enabled is True

    disabled = await service.disable_role(GUILD_A)
    assert disabled.role_id == 999
    assert disabled.role_enabled is False


async def test_embed_setters_validate_and_persist(db_session: AsyncSession) -> None:
    service = WelcomeService()

    await service.set_embed_enabled(GUILD_A, True)
    await service.set_embed_title(GUILD_A, "Welcome!")
    config = await service.set_embed_description(GUILD_A, "Hi {user}")

    assert config.embed_enabled is True
    assert config.embed_title == "Welcome!"
    assert config.embed_description == "Hi {user}"


async def test_embed_title_rejects_unknown_variable(db_session: AsyncSession) -> None:
    service = WelcomeService()

    with pytest.raises(WelcomeValidationError, match=r"\{typo\}"):
        await service.set_embed_title(GUILD_A, "{typo}")


async def test_embed_footer_can_be_cleared(db_session: AsyncSession) -> None:
    service = WelcomeService()

    await service.set_embed_footer(GUILD_A, "Some footer")
    cleared = await service.set_embed_footer(GUILD_A, None)

    assert cleared.embed_footer is None


async def test_reset_restores_defaults(db_session: AsyncSession) -> None:
    service = WelcomeService()

    await service.set_enabled(GUILD_A, True)
    await service.set_channel(GUILD_A, 555)
    await service.set_message(GUILD_A, "Hi {user}")

    reset = await service.reset(GUILD_A)

    assert reset.enabled is False
    assert reset.channel_id is None
    assert reset.message_template is None


def test_render_join_message_fills_all_variables() -> None:
    service = WelcomeService()
    context = JoinContext(
        user_display_name="Alice",
        user_mention="<@1>",
        user_id=1,
        guild_name="Test Server",
        member_count=42,
        channel_name="welcome",
        channel_mention="<#2>",
    )

    rendered = service.render_join_message(
        "{user} ({user_mention}, {user_id}) joined {server} "
        "(member #{member_count}) - see {channel} {channel_mention}",
        context,
    )

    assert rendered == "Alice (<@1>, 1) joined Test Server (member #42) - see welcome <#2>"


def test_render_join_message_defaults_channel_fields_to_empty() -> None:
    service = WelcomeService()
    context = JoinContext(
        user_display_name="Alice",
        user_mention="<@1>",
        user_id=1,
        guild_name="Test Server",
        member_count=1,
    )

    rendered = service.render_join_message("{channel}|{channel_mention}", context)

    assert rendered == "|"
