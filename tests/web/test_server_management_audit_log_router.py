from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_log_entry import AuditAction, AuditTargetType
from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.audit_log_service import AuditLogService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111
_MANAGE_GUILD = 0x20


async def _seed(db_session: AsyncSession, seed_session: SeedSession, *, permissions: int = _MANAGE_GUILD) -> str:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    return await seed_session(db_session, permissions={str(GUILD_A): permissions})


def _client(web_config: WebConfig) -> TestClient:
    # The audit log page makes no Discord calls of its own - this handler
    # exists only because create_app requires an http_client, never
    # expected to actually be invoked by these tests.
    def _unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.method} {request.url}")

    app = create_app(web_config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(_unexpected)))
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "good-token")
    return client


async def test_audit_log_shows_recorded_entries(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    await AuditLogService().record(
        GUILD_A, actor_discord_user_id=42, action=AuditAction.ROLE_CREATE,
        target_type=AuditTargetType.ROLE, target_id=500, target_name="Staff",
        summary="Created role 'Staff'.",
    )

    with _client(web_config) as client:
        response = client.get(f"/guilds/{GUILD_A}/server-management/audit-log")

    assert response.status_code == 200
    assert "Staff" in response.text
    assert "role_create" in response.text


async def test_audit_log_empty_state(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    with _client(web_config) as client:
        response = client.get(f"/guilds/{GUILD_A}/server-management/audit-log")

    assert response.status_code == 200
    assert "No server-management activity logged yet." in response.text


async def test_audit_log_requires_manage_access(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session, permissions=0)

    with _client(web_config) as client:
        response = client.get(f"/guilds/{GUILD_A}/server-management/audit-log")

    assert response.status_code == 403


async def test_audit_log_pagination(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    for i in range(3):
        await AuditLogService().record(
            GUILD_A, actor_discord_user_id=42, action=AuditAction.ROLE_CREATE,
            target_type=AuditTargetType.ROLE, target_id=i, target_name=f"role-{i}", summary="s",
        )

    with _client(web_config) as client:
        response = client.get(f"/guilds/{GUILD_A}/server-management/audit-log?offset=0")

    assert response.status_code == 200
    assert "role-0" in response.text
    assert "Load older" not in response.text  # only 3 entries, page size 50
