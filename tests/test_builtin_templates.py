from __future__ import annotations

from app.services.builtin_templates import (
    BUILTIN_TEMPLATES,
    EVERYONE_ROLE_NAME,
    TemplateChannelDef,
    TemplateDefinition,
    TemplateOverwriteDef,
    TemplateRoleDef,
    definition_from_dict,
    definition_to_dict,
    get_builtin,
)


def test_builtin_slugs_are_unique() -> None:
    slugs = [t.slug for t in BUILTIN_TEMPLATES]
    assert len(slugs) == len(set(slugs))


def test_get_builtin_found_and_missing() -> None:
    first = BUILTIN_TEMPLATES[0]
    assert get_builtin(first.slug) is first
    assert get_builtin("nonexistent-slug") is None


def test_every_channel_parent_name_refers_to_a_category_in_the_same_template() -> None:
    for template in BUILTIN_TEMPLATES:
        category_names = {
            c.name for c in template.definition.channels if c.type == 4  # CATEGORY_CHANNEL_TYPE
        }
        for channel in template.definition.channels:
            if channel.parent_name is not None:
                assert channel.parent_name in category_names, (
                    f"{template.slug}: channel {channel.name!r} references "
                    f"missing category {channel.parent_name!r}"
                )


def test_every_overwrite_role_name_refers_to_everyone_or_a_defined_role() -> None:
    for template in BUILTIN_TEMPLATES:
        role_names = {r.name for r in template.definition.roles}
        for channel in template.definition.channels:
            for overwrite in channel.overwrites:
                assert overwrite.role_name == EVERYONE_ROLE_NAME or overwrite.role_name in role_names, (
                    f"{template.slug}: channel {channel.name!r} overwrite references "
                    f"undefined role {overwrite.role_name!r}"
                )


def test_definition_round_trips_through_dict() -> None:
    definition = TemplateDefinition(
        roles=(TemplateRoleDef(name="Staff", color=123, hoist=True, mentionable=True, permissions=8),),
        channels=(
            TemplateChannelDef(
                name="mod-chat",
                type=0,
                parent_name="Staff",
                topic="Staff only",
                nsfw=False,
                rate_limit_per_user=5,
                bitrate=None,
                user_limit=None,
                overwrites=(TemplateOverwriteDef(role_name=EVERYONE_ROLE_NAME, deny=1024),),
            ),
        ),
    )

    round_tripped = definition_from_dict(definition_to_dict(definition))

    assert round_tripped == definition


def test_builtin_definitions_round_trip_through_dict() -> None:
    for template in BUILTIN_TEMPLATES:
        as_dict = definition_to_dict(template.definition)
        assert definition_from_dict(as_dict) == template.definition
