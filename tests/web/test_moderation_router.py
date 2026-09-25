from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.infraction import InfractionType
from app.db.repositories.bot_guild_repository import BotGuildRepository
from app.services.config_service import ConfigurationService
from app.services.moderation_service import ModerationService
from app.web.app import create_app
from app.web.config import WebConfig
from app.web.sessions import SESSION_COOKIE_NAME
from tests.web.conftest import SeedSession

GUILD_A = 111
CHANNEL_A = 600


def _discord_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/roles"):
        return httpx.Response(
            200, json=[{"id": str(GUILD_A), "name": "@everyone", "position": 0, "managed": False}]
        )
    if path.endswith("/channels"):
        return httpx.Response(200, json=[{"id": str(CHANNEL_A), "name": "mod-log", "type": 0}])
    if "/members/" in path:
        # Serves both fetch_bot_role_ids (only reads "roles") and
        # fetch_guild_member (reads "user"/"nick") off the same generic
        # response - a per-id deterministic username is enough for tests
        # that don't assert on a specific resolved display name.
        member_id = path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={"roles": [], "nick": None, "user": {"id": member_id, "username": f"user-{member_id}", "global_name": None}},
        )
    raise AssertionError(f"unexpected Discord call: {request.url}")


async def _seed(db_session: AsyncSession, seed_session: SeedSession) -> str:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    return await seed_session(db_session, permissions={str(GUILD_A): 0x20})


async def test_get_moderation_renders_config_and_empty_log(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation")

    assert response.status_code == 200
    assert "No infractions match these filters" in response.text
    assert "No escalation thresholds configured" in response.text
    assert "#mod-log" in response.text


async def test_get_moderation_lists_infractions_and_thresholds(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    service = ModerationService()
    await service.set_escalation_threshold(GUILD_A, 3, InfractionType.TIMEOUT)
    await service.record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN, reason="Spamming"
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation")

    assert response.status_code == 200
    assert "3 warnings" in response.text
    assert "timeout" in response.text
    assert "user-555" in response.text  # resolved display name, not the raw id
    assert "Spamming" in response.text


async def test_get_moderation_403s_without_manage_permission(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await BotGuildRepository(db_session).mark_present(GUILD_A, "Guild A")
    await seed_session(db_session, permissions={str(GUILD_A): 0x800})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation")

    assert response.status_code == 403


async def test_post_moderation_updates_log_channel_and_escalation(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation",
                data={
                    "moderation_log_channel_id": str(CHANNEL_A),
                    "escalation_enabled": "on",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )

    assert response.status_code == 303
    guild_config = await ConfigurationService().get_config(GUILD_A)
    mod_config = await ModerationService().get_config(GUILD_A)
    assert guild_config.moderation_log_channel_id == CHANNEL_A
    assert mod_config.escalation_enabled is True


async def test_post_moderation_leaves_general_log_channel_untouched(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await ConfigurationService().set_log_channel(GUILD_A, CHANNEL_A)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            client.post(
                f"/guilds/{GUILD_A}/moderation",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    guild_config = await ConfigurationService().get_config(GUILD_A)
    assert guild_config.log_channel_id == CHANNEL_A
    assert guild_config.moderation_log_channel_id is None


async def test_post_moderation_rejects_stale_channel_id(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation",
                data={"moderation_log_channel_id": "999999999", "csrf_token": csrf_token},
            )

    assert response.status_code == 400


async def test_post_add_threshold_persists(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/thresholds",
                data={"warnings": "5", "action": "kick", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await ModerationService().get_config(GUILD_A)
    assert config.escalation_thresholds == {"5": "kick"}


async def test_post_add_threshold_rejects_zero_warnings(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/thresholds",
                data={"warnings": "0", "action": "kick", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "at least 1" in response.text


async def test_post_add_threshold_rejects_invalid_action(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/thresholds",
                data={"warnings": "3", "action": "banhammer", "csrf_token": csrf_token},
            )

    assert response.status_code == 400
    assert "timeout, kick, or ban" in response.text


async def test_post_delete_threshold_removes_it(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    await ModerationService().set_escalation_threshold(GUILD_A, 3, InfractionType.TIMEOUT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/thresholds/3/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    config = await ModerationService().get_config(GUILD_A)
    assert config.escalation_thresholds == {}


async def test_post_moderation_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation",
                data={"escalation_enabled": "on", "csrf_token": "wrong-token"},
            )

    assert response.status_code == 403
    config = await ModerationService().get_config(GUILD_A)
    assert config.escalation_enabled is False


async def test_get_moderation_404s_when_bot_absent(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await seed_session(db_session, permissions={str(GUILD_A): 0x20})

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Discord call: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation")

    assert response.status_code == 404


# --- Search/sort/filter ---


async def test_get_moderation_filters_by_search_text(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    service = ModerationService()
    await service.record_infraction(GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN, reason="Spamming")
    await service.record_infraction(GUILD_A, user_id=556, moderator_id=42, type=InfractionType.WARN, reason="Being rude")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation?q=spam")

    assert response.status_code == 200
    assert "Spamming" in response.text
    assert "Being rude" not in response.text


async def test_get_moderation_filters_by_type_and_state(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    service = ModerationService()
    warn = await service.record_infraction(GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN, reason="Warned")
    await service.record_infraction(GUILD_A, user_id=555, moderator_id=42, type=InfractionType.KICK, reason="Kicked")
    await service.resolve_infraction(GUILD_A, warn.id)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            type_filtered = client.get(f"/guilds/{GUILD_A}/moderation?type=kick")
            state_filtered = client.get(f"/guilds/{GUILD_A}/moderation?state=resolved")

    assert "Kicked" in type_filtered.text
    assert "Warned" not in type_filtered.text
    assert "Warned" in state_filtered.text
    assert "Kicked" not in state_filtered.text


async def test_get_moderation_sorts_by_type(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    service = ModerationService()
    await service.record_infraction(GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN)
    await service.record_infraction(GUILD_A, user_id=555, moderator_id=42, type=InfractionType.BAN)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation?sort=type&dir=asc")

    assert response.status_code == 200
    assert response.text.index(">ban<") < response.text.index(">warn<")


# --- Infraction detail: view, edit reason, delete ---


async def test_get_infraction_detail_shows_resolved_name_and_reason(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    infraction = await ModerationService().record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN, reason="Spamming"
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation/infractions/{infraction.id}")

    assert response.status_code == 200
    assert "user-555" in response.text
    assert "Spamming" in response.text


async def test_get_infraction_detail_404_for_missing(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation/infractions/999999")

    assert response.status_code == 404


async def test_post_infraction_reason_updates_it(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    infraction = await ModerationService().record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN, reason="original"
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/infractions/{infraction.id}/reason",
                data={"reason": "corrected reason", "csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    updated = await ModerationService().get_infraction(GUILD_A, infraction.id)
    assert updated is not None
    assert updated.reason == "corrected reason"


async def test_post_infraction_reason_rejects_too_long(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    infraction = await ModerationService().record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/infractions/{infraction.id}/reason",
                data={"reason": "x" * 600, "csrf_token": csrf_token},
            )

    assert response.status_code == 400


async def test_post_infraction_reason_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    infraction = await ModerationService().record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN, reason="original"
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/infractions/{infraction.id}/reason",
                data={"reason": "hijacked", "csrf_token": "wrong-token"},
            )

    assert response.status_code == 403
    unchanged = await ModerationService().get_infraction(GUILD_A, infraction.id)
    assert unchanged is not None
    assert unchanged.reason == "original"


async def test_post_delete_infraction_removes_it(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)
    infraction = await ModerationService().record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/infractions/{infraction.id}/delete",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert await ModerationService().get_infraction(GUILD_A, infraction.id) is None


async def test_post_delete_infraction_404_for_missing(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    csrf_token = await _seed(db_session, seed_session)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/infractions/999999/delete",
                data={"csrf_token": csrf_token},
            )

    assert response.status_code == 404


async def test_post_delete_infraction_rejects_bad_csrf(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    infraction = await ModerationService().record_infraction(
        GUILD_A, user_id=555, moderator_id=42, type=InfractionType.WARN
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.post(
                f"/guilds/{GUILD_A}/moderation/infractions/{infraction.id}/delete",
                data={"csrf_token": "wrong-token"},
            )

    assert response.status_code == 403
    assert await ModerationService().get_infraction(GUILD_A, infraction.id) is not None


async def test_get_moderation_type_chips_count_each_action(
    db_session: AsyncSession, web_config: WebConfig, seed_session: SeedSession
) -> None:
    await _seed(db_session, seed_session)
    service = ModerationService()
    for kind in (InfractionType.WARN, InfractionType.WARN, InfractionType.BAN):
        await service.record_infraction(GUILD_A, user_id=555, moderator_id=42, type=kind, reason="x")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_discord_handler)) as http:
        app = create_app(web_config, http_client=http)
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, "good-token")
            response = client.get(f"/guilds/{GUILD_A}/moderation?type=warn")

    assert response.status_code == 200
    assert 'ALL <span class="count">3</span>' in response.text
    assert 'WARN <span class="count">2</span>' in response.text
    assert 'BAN <span class="count">1</span>' in response.text
    assert 'KICK <span class="count">0</span>' in response.text
    # The active chip is the current type filter; its link keeps the query.
    assert 'class="chip is-active" href="/guilds/111/moderation?sort=created_at&amp;dir=desc&amp;type=warn"' in response.text
