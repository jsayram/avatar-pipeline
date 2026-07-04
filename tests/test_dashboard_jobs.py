import time

from lib.dashboard_jobs import JobManager


def wait_for(manager, job_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job["state"] in {"done", "error"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_manager_runs_jobs_and_records_result():
    manager = JobManager()
    record = manager.enqueue("demo", lambda: ({"status": "ok"}, 0), run_id="abc")

    job = wait_for(manager, record.id)

    assert job["state"] == "done"
    assert job["run_id"] == "abc"
    assert job["result"] == {"status": "ok"}


def test_job_manager_records_exceptions_as_error():
    manager = JobManager()

    def boom():
        raise RuntimeError("broken")

    record = manager.enqueue("demo", boom)

    job = wait_for(manager, record.id)

    assert job["state"] == "error"
    assert "broken" in job["error"]
