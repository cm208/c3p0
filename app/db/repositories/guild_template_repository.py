from __future__ import annotations

from sqlalchemy import select

from app.db.models.guild_template import GuildTemplate
from app.db.repositories.base import BaseRepository


class GuildTemplateRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        *,
        name: str,
        description: str | None,
        definition: dict,
        created_by: int | None,
    ) -> GuildTemplate:
        template = GuildTemplate(
            guild_id=guild_id,
            name=name,
            description=description,
            definition=definition,
            created_by=created_by,
        )
        self.session.add(template)
        await self.session.flush()
        return template

    async def get(self, guild_id: int, template_id: int) -> GuildTemplate | None:
        stmt = select(GuildTemplate).where(
            GuildTemplate.id == template_id, GuildTemplate.guild_id == guild_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_guild(self, guild_id: int) -> list[GuildTemplate]:
        stmt = (
            select(GuildTemplate)
            .where(GuildTemplate.guild_id == guild_id)
            .order_by(GuildTemplate.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def delete(self, guild_id: int, template_id: int) -> bool:
        template = await self.get(guild_id, template_id)
        if template is None:
            return False
        await self.session.delete(template)
        await self.session.flush()
        return True
