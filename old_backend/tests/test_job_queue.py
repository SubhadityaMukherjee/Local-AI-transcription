import time
import tempfile
from app import app


def test_job_queue_serial_processing(client=None, monkeypatch=None):
    """Submitting two jobs quickly should process them in FIFO order with the
    second remaining "queued" until the first completes.
    """
    if client is None:
        client = app.test_client()

    order = []

    def fake_process(src, job_id, on_progress):
        order.append(job_id)
        # simulate a brief transcription delay
        time.sleep(0.05)
        return "done", "dummy"

    monkeypatch.setattr(app.transcription_service, "process", fake_process)

    # create two temporary files to upload
    f1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f1.write(b"RIFF")
    f1.flush()
    f2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f2.write(b"RIFF")
    f2.flush()

    resp1 = client.post(
        "/api/upload",
        data={"file": (open(f1.name, "rb"), "a.wav")},
        content_type="multipart/form-data",
    )
    jid1 = resp1.get_json()["job_id"]

    resp2 = client.post(
        "/api/upload",
        data={"file": (open(f2.name, "rb"), "b.wav")},
        content_type="multipart/form-data",
    )
    jid2 = resp2.get_json()["job_id"]

    # poll until both jobs have status done (with a timeout)
    for jid in (jid1, jid2):
        for _ in range(40):
            r = client.get(f"/api/status/{jid}")
            if r.json.get("status") == "done":
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"job {jid} did not finish in time")

    # the order list should preserve FIFO sequence
    assert order == [jid1, jid2]
