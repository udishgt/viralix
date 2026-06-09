import uuid

from auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    register_user,
    revoke_refresh_token,
    verify_refresh_token_in_db,
    verify_token,
)
from models import RefreshToken, SessionLocal, User, init_db


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_auth_core_register_login_and_refresh_lifecycle():
    init_db()
    db = SessionLocal()
    email = unique_email("pytest-core")
    password = "StrongPass!234"
    user = None
    refresh = None

    try:
        user, err = register_user(email, password, "PyTest Core", db)
        assert user is not None, err

        authed, err = authenticate_user(email, password, db)
        assert authed is not None, err

        access = create_access_token(authed.id, authed.email)
        refresh = create_refresh_token(authed.id, authed.email, db)

        payload = verify_token(access, token_type="access")
        assert payload is not None
        assert payload.sub == authed.id

        refresh_user = verify_refresh_token_in_db(refresh, db)
        assert refresh_user is not None
        assert refresh_user.id == authed.id

        revoked = revoke_refresh_token(refresh, db)
        assert revoked is True

        refresh_user_after = verify_refresh_token_in_db(refresh, db)
        assert refresh_user_after is None
    finally:
        if refresh:
            db.query(RefreshToken).delete()
        if user:
            db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()
