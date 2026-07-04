"""Telegram message -> yes/no/link classification -> dispatch to the right phase."""
import json

import pytest

import handle_telegram_reply as reply_handler
import worker
from lib.pending import save_pending

URL = "https://www.tiktok.com/@me/video/555"
TIKTOK_ID = "555"
NEW_URL = "https://www.tiktok.com/t/ZNEW999/"
NEW_TIKTOK_ID = "ZNEW999"


def run_reply(capsys, text, config_path):
    code = reply_handler.main(["--text", text, "--config", str(config_path)])
    out = capsys.readouterr().out.strip()
    return code, json.loads(out)


def save_frame_pending(work_dir):
    save_pending(work_dir, TIKTOK_ID, stage="frame", url=URL,
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")


def save_avatar_pending(work_dir):
    save_pending(work_dir, TIKTOK_ID, stage="avatar", url=URL,
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png",
                avatar_frame_path="/w/avatar.png", attempt=1)


@pytest.fixture
def stub_phases(monkeypatch):
    """Stub every phase function so we only test reply-interpretation,
    stage-based dispatch, and id-resolution — not the (separately-tested)
    phase logic itself."""
    calls = []

    def fake_run_prepare(cfg, tiktok_id, url, work, logger):
        calls.append(("prepare", tiktok_id, url))
        return {"status": "pending_approval", "id": tiktok_id, "url": url, "stage": "frame"}, 0

    def fake_run_generate_avatar(cfg, tiktok_id, work, logger):
        calls.append(("generate_avatar", tiktok_id))
        return {"status": "pending_approval", "id": tiktok_id, "stage": "avatar"}, 0

    def fake_run_reject_frame(cfg, tiktok_id, work, logger):
        calls.append(("reject_frame", tiktok_id))
        return {"status": "not_consumed", "id": tiktok_id, "stage": "frame_rejected"}, 0

    def fake_run_animate(cfg, tiktok_id, work, logger):
        calls.append(("animate", tiktok_id))
        return {"status": "published", "id": tiktok_id}, 0

    def fake_run_regenerate(cfg, tiktok_id, work, logger):
        calls.append(("regenerate", tiktok_id))
        return {"status": "pending_approval", "id": tiktok_id, "attempt": 2}, 0

    def fake_notify(cfg, text, logger):
        calls.append(("notify", text))

    monkeypatch.setattr(worker, "run_prepare", fake_run_prepare)
    monkeypatch.setattr(worker, "run_generate_avatar", fake_run_generate_avatar)
    monkeypatch.setattr(worker, "run_reject_frame", fake_run_reject_frame)
    monkeypatch.setattr(worker, "run_animate", fake_run_animate)
    monkeypatch.setattr(worker, "run_regenerate", fake_run_regenerate)
    monkeypatch.setattr(worker, "_notify", fake_notify)
    return calls


@pytest.fixture
def stub_archive(monkeypatch):
    appended = []
    monkeypatch.setattr(reply_handler, "append_link",
                        lambda path, url: appended.append((str(path), url)))
    return appended


# --------------------------------------------------------------------------
# yes/no at the FRAME gate

@pytest.mark.parametrize("text", ["yes", "Yes", " YES ", "y", "approve", "\U0001F44D"])
def test_yes_at_frame_stage_triggers_generate_avatar(
        make_config, tmp_path, capsys, stub_phases, text):
    config_path = make_config()
    save_frame_pending(tmp_path / "work")
    code, result = run_reply(capsys, text, config_path)
    assert code == 0
    assert result["stage"] == "avatar"
    # immediate ack ("sent to Seedream...") before the actual phase call
    assert stub_phases[0][0] == "notify"
    assert "Seedream" in stub_phases[0][1]
    assert stub_phases[1] == ("generate_avatar", TIKTOK_ID)


@pytest.mark.parametrize("text", ["no", "No", " NO ", "n", "reject", "\U0001F44E"])
def test_no_at_frame_stage_triggers_reject_frame(
        make_config, tmp_path, capsys, stub_phases, text):
    config_path = make_config()
    save_frame_pending(tmp_path / "work")
    code, result = run_reply(capsys, text, config_path)
    assert code == 0
    assert result["status"] == "not_consumed"
    assert stub_phases[0][0] == "notify"
    assert "clearing" in stub_phases[0][1]
    assert stub_phases[1] == ("reject_frame", TIKTOK_ID)


# --------------------------------------------------------------------------
# yes/no at the AVATAR gate

@pytest.mark.parametrize("text", ["yes", "Yes", " YES ", "y", "approve", "\U0001F44D"])
def test_yes_at_avatar_stage_triggers_animate(
        make_config, tmp_path, capsys, stub_phases, text):
    config_path = make_config()
    save_avatar_pending(tmp_path / "work")
    code, result = run_reply(capsys, text, config_path)
    assert code == 0
    assert result == {"status": "published", "id": TIKTOK_ID}
    assert stub_phases[0][0] == "notify"
    assert "WaveSpeed Kling" in stub_phases[0][1]
    assert stub_phases[1] == ("animate", TIKTOK_ID)


@pytest.mark.parametrize("text", ["no", "No", " NO ", "n", "reject", "\U0001F44E"])
def test_no_at_avatar_stage_triggers_regenerate(
        make_config, tmp_path, capsys, stub_phases, text):
    config_path = make_config()
    save_avatar_pending(tmp_path / "work")
    code, result = run_reply(capsys, text, config_path)
    assert code == 0
    assert result["status"] == "pending_approval"
    assert stub_phases[0][0] == "notify"
    assert "regenerating" in stub_phases[0][1]
    assert stub_phases[1] == ("regenerate", TIKTOK_ID)


def test_unrecognized_text_is_ignored(make_config, tmp_path, capsys, stub_phases):
    config_path = make_config()
    save_avatar_pending(tmp_path / "work")
    code, result = run_reply(capsys, "hi there", config_path)
    assert code == 0
    assert result == {"status": "ignored", "text": "hi there"}
    assert stub_phases == []


def test_yes_with_no_pending_approval_is_ignored_not_error(make_config, capsys, stub_phases):
    config_path = make_config()
    code, result = run_reply(capsys, "yes", config_path)
    assert code == 0
    assert result["status"] == "ignored"
    assert "reason" in result
    assert stub_phases == []


def test_yes_declined_when_dashboard_already_claimed_approval(
        make_config, tmp_path, capsys, stub_phases):
    from lib.approval_lock import try_claim

    config_path = make_config()
    save_frame_pending(tmp_path / "work")
    try_claim(tmp_path / "work", TIKTOK_ID, "dashboard")

    code, result = run_reply(capsys, "yes", config_path)

    assert code == 0
    assert result["status"] == "ignored"
    assert "dashboard" in result["reason"]
    assert ("generate_avatar", TIKTOK_ID) not in stub_phases
    assert any(call[0] == "notify" and "already being processed" in call[1]
               for call in stub_phases)


# --------------------------------------------------------------------------
# texting in a new TikTok link

def test_new_link_with_no_pending_triggers_prepare_and_archives(
        make_config, tmp_path, capsys, stub_phases, stub_archive):
    config_path = make_config()
    code, result = run_reply(capsys, NEW_URL, config_path)
    assert code == 0
    assert result["status"] == "pending_approval"
    # immediate "received + archived" acknowledgement, then the real work
    assert stub_phases[0][0] == "notify"
    assert "Link received" in stub_phases[0][1]
    assert str(tmp_path / "linksThroughTelegram.numbers") in stub_phases[0][1]
    assert stub_phases[1] == ("prepare", NEW_TIKTOK_ID, NEW_URL)
    assert stub_archive == [(str(tmp_path / "linksThroughTelegram.numbers"), NEW_URL)]


def test_same_link_while_pending_resends_existing_frame_without_prepare(
        make_config, tmp_path, capsys, stub_phases, monkeypatch):
    config_path = make_config()
    frame = tmp_path / "work" / NEW_TIKTOK_ID / "frame1.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"frame")
    save_pending(tmp_path / "work", NEW_TIKTOK_ID, stage="frame", url=NEW_URL,
                 ref_video_path=str(frame.parent / "ref.mp4"),
                 frame1_path=str(frame))
    sent = []
    monkeypatch.setattr(
        worker, "send_photo",
        lambda cfg, image_path, caption, logger=None: sent.append((str(image_path), caption)),
    )

    code, result = run_reply(capsys, NEW_URL, config_path)

    assert code == 0
    assert result["status"] == "pending_approval"
    assert result["stage"] == "frame"
    assert ("prepare", NEW_TIKTOK_ID, NEW_URL) not in stub_phases
    assert len(sent) == 1
    assert sent[0][0] == str(frame)
    assert "already waiting on the extracted frame approval" in sent[0][1]


def test_new_link_updates_archive_processing_note(
        make_config, tmp_path, capsys, monkeypatch, stub_archive):
    config_path = make_config()
    calls = []
    note = "Source video was 13.40s, so the reference clip was trimmed to 9.95s."

    def fake_run_prepare(cfg, tiktok_id, url, work, logger):
        calls.append(("prepare", tiktok_id, url))
        return {
            "status": "pending_approval",
            "id": tiktok_id,
            "url": url,
            "stage": "frame",
            "processing_note": note,
        }, 0

    def fake_notify(cfg, text, logger):
        calls.append(("notify", text))

    def fake_update_processing_note(path, url, processing_note):
        calls.append(("update_processing_note", str(path), url, processing_note))
        return True

    monkeypatch.setattr(worker, "run_prepare", fake_run_prepare)
    monkeypatch.setattr(worker, "_notify", fake_notify)
    monkeypatch.setattr(reply_handler, "update_processing_note", fake_update_processing_note)

    code, result = run_reply(capsys, NEW_URL, config_path)

    assert code == 0
    assert result["processing_note"] == note
    assert ("update_processing_note", str(tmp_path / "linksThroughTelegram.numbers"),
            NEW_URL, note) in calls


def test_new_link_while_pending_is_declined_with_notice(
        make_config, tmp_path, capsys, stub_phases, stub_archive):
    config_path = make_config()
    save_frame_pending(tmp_path / "work")

    code, result = run_reply(capsys, NEW_URL, config_path)
    assert code == 0
    assert result["status"] == "ignored"
    assert TIKTOK_ID in result["reason"]
    # prepare was never called for the new link, and the operator got a notice
    assert ("prepare", NEW_TIKTOK_ID, NEW_URL) not in stub_phases
    assert any(call[0] == "notify" and TIKTOK_ID in call[1] for call in stub_phases)
    assert stub_archive == []  # declined link is never archived


def test_new_link_already_processed_sends_feedback(
        make_config, tmp_path, capsys, stub_archive, monkeypatch):
    config_path = make_config()
    calls = []

    def fake_run_prepare(cfg, tiktok_id, url, work, logger):
        calls.append(("prepare", tiktok_id, url))
        return {"status": "already_processed", "id": tiktok_id, "url": url,
                "previous_status": "flagged"}, 0

    def fake_notify(cfg, text, logger):
        calls.append(("notify", text))

    monkeypatch.setattr(worker, "run_prepare", fake_run_prepare)
    monkeypatch.setattr(worker, "_notify", fake_notify)

    code, result = run_reply(capsys, NEW_URL, config_path)
    assert code == 0
    assert result["status"] == "already_processed"
    notify_texts = [c[1] for c in calls if c[0] == "notify"]
    assert any("already flagged" in t for t in notify_texts)


def test_new_link_declined_while_download_in_progress(
        make_config, tmp_path, capsys, stub_phases, stub_archive):
    """Simulates the pre-pending-record race window: link A is still being
    downloaded (lock held, no pending_approval.json yet) when link B arrives."""
    from lib.processing_lock import try_acquire

    config_path = make_config()
    try_acquire(tmp_path / "work", "some-other-id")

    code, result = run_reply(capsys, NEW_URL, config_path)
    assert code == 0
    assert result["status"] == "ignored"
    assert "some-other-id" in result["reason"]
    assert ("prepare", NEW_TIKTOK_ID, NEW_URL) not in stub_phases
    assert stub_archive == []  # never reached the archive step
    assert any(call[0] == "notify" and "some-other-id" in call[1] for call in stub_phases)
