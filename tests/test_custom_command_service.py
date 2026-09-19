from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import session_scope
from app.db.repositories.guild_config_repository import GuildConfigRepository
from app.services.custom_command_service import (
    CustomCommandService,
    CustomCommandValidationError,
    CustomCommandView,
)
from app.utils.templates import TemplateContext

GUILD_A = 111
USER_A = 1001
USER_B = 2002


def _context(user_id: int = USER_A) -> TemplateContext:
    return TemplateContext(
        user_display_name="Alice",
        user_mention=f"<@{user_id}>",
        user_id=user_id,
        guild_name="Test Server",
        member_count=10,
    )


def _make_view(**overrides: object) -> CustomCommandView:
    defaults: dict[str, object] = {
        "id": 1,
        "guild_id": GUILD_A,
        "name": "Greet",
        "trigger": "!hi",
        "response": "Hi {user}!",
        "embed_enabled": False,
        "enabled": True,
        "restriction_type": "public",
        "restricted_role_id": None,
        "restricted_permission": None,
        "cooldown_type": "none",
        "cooldown_seconds": 0,
        "usage_logging_enabled": False,
        "created_by": 1,
    }
    defaults.update(overrides)
    return CustomCommandView(**defaults)  # type: ignore[arg-type]


# --- Persistence (DB-backed) ---


async def test_create_persists(db_session: AsyncSession) -> None:
    service = CustomCommandService()

    view = await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    assert view.trigger == "!rules"
    found = await service.get_by_trigger(GUILD_A, "!rules")
    assert found is not None
    assert found.id == view.id


async def test_create_also_creates_parent_guild_config(db_session: AsyncSession) -> None:
    # CustomCommand.guild_id FKs to guild_config.guild_id, enforced against
    # the real sqlite file this runs against in production (not the
    # in-memory test database, which ignores FKs) - a guild the bot just
    # joined has no guild_config row yet, so this must create one rather
    # than assume it already exists.
    service = CustomCommandService(default_prefix="?")

    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    async with session_scope() as session:
        guild_config = await GuildConfigRepository(session).get(GUILD_A)

    assert guild_config is not None
    assert guild_config.prefix == "?"


async def test_create_rejects_duplicate_trigger(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    with pytest.raises(CustomCommandValidationError, match="already exists"):
        await service.create(GUILD_A, name="Rules2", trigger="!rules", response="Other.", created_by=1)


async def test_create_rejects_trigger_with_space(db_session: AsyncSession) -> None:
    service = CustomCommandService()

    with pytest.raises(CustomCommandValidationError, match="spaces"):
        await service.create(GUILD_A, name="Rules", trigger="! rules", response="Be nice.", created_by=1)


async def test_create_rejects_unknown_template_variable(db_session: AsyncSession) -> None:
    service = CustomCommandService()

    with pytest.raises(CustomCommandValidationError, match=r"\{typo\}"):
        await service.create(GUILD_A, name="Rules", trigger="!rules", response="Hi {typo}", created_by=1)


async def test_create_rejects_empty_response(db_session: AsyncSession) -> None:
    service = CustomCommandService()

    with pytest.raises(CustomCommandValidationError, match="empty"):
        await service.create(GUILD_A, name="Rules", trigger="!rules", response="   ", created_by=1)


async def test_set_response_by_trigger(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Old", created_by=1)

    updated = await service.set_response_by_trigger(GUILD_A, "!rules", "New {user}")
    assert updated is not None
    assert updated.response == "New {user}"

    assert await service.set_response_by_trigger(GUILD_A, "!missing", "New") is None


async def test_delete_by_trigger(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    assert await service.delete_by_trigger(GUILD_A, "!rules") is True
    assert await service.delete_by_trigger(GUILD_A, "!rules") is False


async def test_set_restriction_validates_role_required(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    with pytest.raises(CustomCommandValidationError, match="role is required"):
        await service.set_restriction_by_trigger(GUILD_A, "!rules", restriction_type="role")


async def test_set_restriction_persists(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    updated = await service.set_restriction_by_trigger(
        GUILD_A, "!rules", restriction_type="role", restricted_role_id=999
    )
    assert updated is not None
    assert updated.restriction_type == "role"
    assert updated.restricted_role_id == 999


async def test_set_cooldown_rejects_negative(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    with pytest.raises(CustomCommandValidationError, match="negative"):
        await service.set_cooldown_by_trigger(GUILD_A, "!rules", cooldown_type="user", cooldown_seconds=-1)


async def test_set_usage_logging_by_trigger(db_session: AsyncSession) -> None:
    service = CustomCommandService()
    await service.create(GUILD_A, name="Rules", trigger="!rules", response="Be nice.", created_by=1)

    updated = await service.set_usage_logging_by_trigger(GUILD_A, "!rules", True)
    assert updated is not None
    assert updated.usage_logging_enabled is True

    assert await service.set_usage_logging_by_trigger(GUILD_A, "!missing", True) is None


# --- Rendering / restriction / cooldown (pure, in-memory) ---


def test_render_response_uses_template_context() -> None:
    service = CustomCommandService()
    view = _make_view(response="Hi {user}, welcome to {server}!")

    rendered = service.render_response(view, _context())

    assert rendered == "Hi Alice, welcome to Test Server!"


def test_check_restriction_public_always_allows() -> None:
    service = CustomCommandService()
    view = _make_view(restriction_type="public")

    assert service.check_restriction(view, member_role_ids=set(), member_permission_names=set()) is True


def test_check_restriction_role_requires_membership() -> None:
    service = CustomCommandService()
    view = _make_view(restriction_type="role", restricted_role_id=42)

    assert service.check_restriction(view, member_role_ids={42}, member_permission_names=set()) is True
    assert service.check_restriction(view, member_role_ids={1}, member_permission_names=set()) is False


def test_check_restriction_permission_requires_grant() -> None:
    service = CustomCommandService()
    view = _make_view(restriction_type="permission", restricted_permission="manage_messages")

    assert (
        service.check_restriction(view, member_role_ids=set(), member_permission_names={"manage_messages"})
        is True
    )
    assert service.check_restriction(view, member_role_ids=set(), member_permission_names=set()) is False


def test_cooldown_none_never_blocks() -> None:
    service = CustomCommandService()
    view = _make_view(cooldown_type="none", cooldown_seconds=0)

    service.record_usage(view, USER_A)

    assert service.check_cooldown(view, USER_A) is None


def test_cooldown_per_user_blocks_same_user_only() -> None:
    service = CustomCommandService()
    view = _make_view(cooldown_type="user", cooldown_seconds=60)

    service.record_usage(view, USER_A)

    assert service.check_cooldown(view, USER_A) is not None
    assert service.check_cooldown(view, USER_B) is None


def test_cooldown_per_guild_blocks_every_user() -> None:
    service = CustomCommandService()
    view = _make_view(cooldown_type="guild", cooldown_seconds=60)

    service.record_usage(view, USER_A)

    assert service.check_cooldown(view, USER_A) is not None
    assert service.check_cooldown(view, USER_B) is not None


def test_cooldown_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    service = CustomCommandService()
    view = _make_view(cooldown_type="user", cooldown_seconds=30)

    service.record_usage(view, USER_A)
    assert service.check_cooldown(view, USER_A) is not None

    # Fast-forward past the cooldown window instead of a real sleep.
    real_datetime = datetime

    class _FrozenFuture(real_datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return real_datetime.now(tz) + timedelta(seconds=31)

    monkeypatch.setattr("app.services.custom_command_service.datetime", _FrozenFuture)

    assert service.check_cooldown(view, USER_A) is None
