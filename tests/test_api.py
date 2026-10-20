from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
def test_health(): assert client.get("/health").json()["status"]=="ok"
def test_ready(): assert client.get("/ready").status_code==200
def test_requires_input(): assert client.post("/api/v1/broll/jobs",json={}).status_code==422
def test_create_and_status():
    r=client.post("/api/v1/broll/jobs",json={"script":"Saving money starts with a budget.","aspect_ratios":["16:9"]})
    assert r.status_code==202
    assert client.get("/api/v1/jobs/"+r.json()["job_id"]).status_code==200
def test_idempotency():
    h={"Idempotency-Key":"demo-key"}; b={"script":"A test script."}
    a=client.post("/api/v1/broll/jobs",json=b,headers=h)
    c=client.post("/api/v1/broll/jobs",json=b,headers=h)
    assert a.json()["job_id"]==c.json()["job_id"]
