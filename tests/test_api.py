import base64
import io
import time

from fastapi.testclient import TestClient
from PIL import Image

from murray.api import app

ADMIN = {"Authorization": "Bearer test-admin-token"}


def _wait(client, turn_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/turns/{turn_id}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.2)
    raise AssertionError("turn did not finish")


def test_site_and_chat_lifecycle():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        home = client.get("/")
        assert home.status_code == 200 and home.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
        state = client.get("/api/state").json()
        assert state["mode"] == "mock" and state["admin"] is False and "QUARTZ" not in state["persona"]["block"]

        # only JSON bodies are accepted
        assert client.post("/api/chat", data={"text": "hi"}).status_code == 415
        assert client.post("/api/chat", json={"text": ""}).status_code == 400
        assert client.post("/api/chat", json={"text": "x" * 5000}).status_code == 413

        turn_id = client.post("/api/chat", json={"text": "Good morning, Murray."}).json()["turn_id"]
        done = _wait(client, turn_id)
        assert done["status"] == "done" and done["reply"]
        events = client.get("/api/events?after=0").json()["events"]
        kinds = [e["type"] for e in events]
        assert kinds[:3] == ["tick", "gate", "retrieval"] and "turn_done" in kinds

        # a script tag is stored and returned as plain text, never interpreted server-side
        tid = client.post("/api/chat", json={"text": "<script>alert(1)</script> hello"}).json()["turn_id"]
        assert _wait(client, tid)["status"] == "done"

        # another visitor sees none of this
        with TestClient(app) as other:
            assert other.get("/api/events?after=0").json()["events"] == []
            assert other.get(f"/api/turns/{turn_id}").status_code == 404
            ep = events[[e["type"] for e in events].index("episode")]["data"]["episode_id"]
            assert other.get(f"/api/episodes/{ep}").status_code == 404
            assert client.get(f"/api/episodes/{ep}").status_code == 200


def test_upload_compare_and_admin():
    with TestClient(app) as client:
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        r = client.post("/api/compare", json={"text": "What is in this image?", "image_b64": b64, "baseline": "neon"}).json()
        murray = _wait(client, r["murray_turn_id"])
        base = _wait(client, r["baseline_turn_id"])
        assert murray["status"] == "done" and base["status"] == "done" and base["system"] == "baseline_neon"
        assert client.get(f"/uploads/{r['upload_id']}").status_code == 200
        assert client.post("/api/chat", json={"text": "look", "image_b64": base64.b64encode(b"junk").decode()}).status_code == 400

        assert client.get("/api/admin/status").status_code == 401
        assert client.post("/api/admin/reset", json={}).status_code == 401
        assert client.get("/api/admin/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        status = client.get("/api/admin/status", headers=ADMIN).json()
        assert "ledger" in status and status["ledger"]["total_usd"] >= 0

        assert client.get("/renders/../../etc/passwd").status_code == 404
        assert client.get("/renders/job1/evil.sh").status_code == 404
        assert client.get("/api/gallery").json() == {"jobs": []}
        assert [s["day"] for s in client.get("/api/snapshots").json()["snapshots"]] == [0]
