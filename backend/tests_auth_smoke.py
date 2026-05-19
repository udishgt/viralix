from models import init_db, SessionLocal, User, RefreshToken
from auth import register_user, authenticate_user, create_access_token, create_refresh_token, verify_refresh_token_in_db, revoke_refresh_token, verify_token
import importlib

# Initialize DB
init_db()
print('DB initialized')

db = SessionLocal()
# Clean up test users if exist
db.query(RefreshToken).delete()
db.query(User).filter(User.email.like('%testuser%')).delete()
db.commit()

# 1. Signup
user, err = register_user('testuser@example.com', 'Secr3tPass!', 'TestUser', db)
assert user is not None, f"Signup failed: {err}"
print('signup ok', user.email, user.id)

# 2. Authenticate
u, err = authenticate_user('testuser@example.com', 'Secr3tPass!', db)
assert u is not None, f"Auth failed: {err}"
print('auth ok', u.email)

# 3. Create tokens
access = create_access_token(u.id, u.email)
refresh = create_refresh_token(u.id, u.email, db)
print('tokens created (access len, refresh len):', len(access), len(refresh))

# 4. Verify access token payload
payload = verify_token(access, token_type='access')
assert payload and payload.sub == u.id
print('access verify ok')

# 5. Verify refresh in DB
user_from_refresh = verify_refresh_token_in_db(refresh, db)
assert user_from_refresh and user_from_refresh.id == u.id
print('refresh verify ok')

# 6. Revoke refresh
ok = revoke_refresh_token(refresh, db)
assert ok
print('revoke ok')

# 7. Verify revoked no longer valid
user_after = verify_refresh_token_in_db(refresh, db)
assert user_after is None
print('revoked token correctly invalidated')

# 8. Check server routes registered
server = importlib.import_module('server')
app = getattr(server, 'app', None)
assert app is not None
paths = [r.path for r in app.routes]
for p in ['/auth/signup','/auth/login','/auth/refresh','/auth/logout','/auth/me']:
    assert p in paths, f"Expected route {p} not registered"
print('routes registered ok')

# Cleanup
db.query(RefreshToken).delete()
db.query(User).filter(User.email=='testuser@example.com').delete()
db.commit()
db.close()
print('tests passed')
