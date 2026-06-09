import uuid

from fastapi.testclient import TestClient

import server


client = TestClient(server.app)


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def signup(email: str, password: str, display_name: str):
    res = client.post(
        "/auth/signup",
        json={"email": email, "password": password, "displayName": display_name},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_auth_http_flow_signup_me_refresh_logout():
    email = unique_email("pytest-http")
    password = "StrongPass!234"

    data = signup(email, password, "HTTP PyTest")
    access = data["access_token"]
    refresh = data["refresh_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    history_no_auth = client.get("/history")
    assert history_no_auth.status_code in (401, 403)

    refreshed = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 200
    new_access = refreshed.json().get("access_token")
    assert new_access

    logout = client.post(
        "/auth/logout",
        json={"refresh_token": refresh},
        headers={"Authorization": f"Bearer {new_access}"},
    )
    assert logout.status_code == 200

    refresh_after_logout = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert refresh_after_logout.status_code == 401
