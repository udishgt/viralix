import uuid
from datetime import datetime, timedelta

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


def test_status_endpoint_enforces_job_ownership():
    user_a = signup(unique_email("pytest-owner-a"), "OwnerA!234", "Owner A")
    user_b = signup(unique_email("pytest-owner-b"), "OwnerB!234", "Owner B")

    job_id = f"pytest-{uuid.uuid4().hex[:8]}"
    server.jobs[job_id] = {
        "id": job_id,
        "title": "Ownership Test Job",
        "status": "processing",
        "progress": 10,
        "stage": "download",
        "log": ["queued"],
        "clips": [],
        "startedAt": datetime.utcnow().isoformat(),
        "language": "English",
        "clipDuration": 30,
        "autoPost": False,
        "captions": True,
        "userId": user_a["user"]["uid"],
        "genre": "gaming",
        "niche": "ranked fps",
        "audience": "new players",
        "tone": "direct",
        "relevanceMode": "precision",
        "expiresAt": (datetime.utcnow() + timedelta(days=1)).isoformat(),
        "error": None,
    }

    try:
        owner_status = client.get(
            f"/status/{job_id}",
            headers={"Authorization": f"Bearer {user_a['access_token']}"},
        )
        assert owner_status.status_code == 200

        non_owner_status = client.get(
            f"/status/{job_id}",
            headers={"Authorization": f"Bearer {user_b['access_token']}"},
        )
        assert non_owner_status.status_code == 403
    finally:
        server.jobs.pop(job_id, None)
