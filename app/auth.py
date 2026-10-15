"""Auth stub: resolves the calling user from the X-User-Id header.

Deliberately a stub. Real OIDC/JWT verification is out of scope for the MVP;
`current_user` is the seam it will replace, so no handler changes when it does.
Handlers must take the user from this dependency ONLY — never from a request
body field, which would be a trivial IDOR.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User

DEMO_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


async def get_or_create_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Return the User with this id, inserting a placeholder row if absent."""
    user = await db.get(User, user_id)
    if user is not None:
        return user

    db.add(User(id=user_id, external_sub=f"stub:{user_id}", email=f"{user_id}@demo.local"))
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent request created the same user first.
        await db.rollback()
    return await db.get(User, user_id)


async def current_user(
    x_user_id: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> User:
    if x_user_id is None:
        user_id = DEMO_USER_ID
    else:
        try:
            user_id = uuid.UUID(x_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-User-Id must be a UUID")
    return await get_or_create_user(db, user_id)
