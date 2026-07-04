"""Local read-only dashboard for avatar-pipeline."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response, status as http_status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import pick_next  # noqa: E402
import worker  # noqa: E402
from lib.approval_lock import ApprovalLockHeldError  # noqa: E402
from lib.approval_lock import release as release_approval_lock  # noqa: E402
from lib.approval_lock import try_claim as try_claim_approval  # noqa: E402
from lib.config import (  # noqa: E402
    VALID_ANIMATION_PROVIDERS,
    VALID_AVATAR_FRAME_PROVIDERS,
    ConfigError,
    load_config,
)
from lib.config_overrides import (  # noqa: E402
    ConfigOverrideError,
    clear_overrides,
    load_overrides,
    overrides_path,
    write_overrides,
    write_provider_overrides,
)
from lib.dashboard_jobs import JobManager  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.log_tail import LogTailError, tail_log  # noqa: E402
from lib.pending import PendingApprovalError, find_pending_id, load_pending  # noqa: E402
from lib.processing_lock import LockHeldError  # noqa: E402
from lib.processing_lock import release as release_processing_lock  # noqa: E402
from lib.processing_lock import try_acquire as try_acquire_processing_lock  # noqa: E402
from lib.service_health import gather_service_health  # noqa: E402
from lib.state import extract_tiktok_id, load_state, seen_ids, unflag  # noqa: E402
from lib.status_sheet import build_status_rows  # noqa: E402
from lib.telegram_links_archive import append_link, update_processing_note  # noqa: E402
from lib.wavespeed_balance import (  # noqa: E402
    DASHBOARD_URL as WAVESPEED_DASHBOARD_URL,
)
from lib.wavespeed_balance import WaveSpeedBalanceError, get_balance  # noqa: E402

MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".mov", ".m4v", ".webm",
}
STATUS_CACHE_SECONDS = 5
WAVESPEED_BALANCE_CACHE_SECONDS = 60
YES_DECISIONS = {"yes", "approve", "approved"}
NO_DECISIONS = {"no", "reject", "rejected", "regenerate"}


class LinkRequest(BaseModel):
    url: str


class DecisionRequest(BaseModel):
    stage: str
    decision: str


class ProvidersRequest(BaseModel):
    avatar_frame_provider: str
    animation_provider: str
    wavespeed_enabled: bool


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _media_response(root: Path, candidate: Path) -> FileResponse:
    root = root.resolve()
    candidate = candidate.resolve()
    if not _is_relative_to(candidate, root):
        raise HTTPException(status_code=404, detail="media not found")
    if candidate.suffix.lower() not in MEDIA_EXTENSIONS:
        raise HTTPException(status_code=404, detail="media not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="media not found")
    return FileResponse(candidate)


def _work_media_url(cfg, item_id: str, raw_path: str | None) -> str:
    if not raw_path:
        return ""
    path = Path(raw_path).expanduser().resolve()
    run_root = (cfg.paths.work_dir / item_id).resolve()
    if not _is_relative_to(path, run_root) or path.suffix.lower() not in MEDIA_EXTENSIONS:
        return ""
    return f"/api/media/work/{quote(item_id)}/{quote(path.name)}"


def _out_media_url(cfg, raw_path: str | None) -> str:
    if not raw_path:
        return ""
    path = Path(raw_path).expanduser().resolve()
    out_root = cfg.paths.out_dir.resolve()
    if not _is_relative_to(path, out_root) or path.suffix.lower() not in MEDIA_EXTENSIONS:
        return ""
    rel = path.relative_to(out_root).as_posix()
    return f"/api/media/out/{quote(rel)}"


def _looks_like_tiktok_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith(("http://", "https://")) and "tiktok.com" in t


def _pending_conflict(cfg) -> None:
    try:
        pending_id = find_pending_id(cfg.paths.work_dir)
    except PendingApprovalError as exc:
        if "no pending approval" in str(exc) or "work dir does not exist" in str(exc):
            return
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(
        status_code=409,
        detail=f"pending approval already outstanding for {pending_id}",
    )


def _provider_values(config) -> dict:
    return {
        "avatar_frame_provider": config.avatar_frame.provider,
        "animation_provider": config.animation.provider,
        "wavespeed_enabled": config.wavespeed.enabled,
    }


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    config_path = Path(config_path)
    static_dir = Path(__file__).resolve().parent / "static"
    status_cache: dict[str, object] = {"expires": 0.0, "rows": []}
    balance_cache: dict[str, object] = {"expires": 0.0, "payload": None}
    job_manager = JobManager()
    dashboard_logger = get_logger("dashboard")

    app = FastAPI(title="avatar-pipeline dashboard", version="0.1.0")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def cfg():
        try:
            return load_config(config_path)
        except ConfigError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    def provider_payload():
        try:
            effective = load_config(config_path)
            base = load_config(config_path, apply_overrides=False)
            overrides = load_overrides(config_path)
        except (ConfigError, ConfigOverrideError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "effective": _provider_values(effective),
            "base": _provider_values(base),
            "overridden": {
                "avatar_frame_provider": "provider" in overrides.get("avatar_frame", {}),
                "animation_provider": "provider" in overrides.get("animation", {}),
                "wavespeed_enabled": "enabled" in overrides.get("wavespeed", {}),
            },
            "overlay_exists": overrides_path(config_path).exists(),
            "options": {
                "avatar_frame_provider": list(VALID_AVATAR_FRAME_PROVIDERS),
                "animation_provider": list(VALID_ANIMATION_PROVIDERS),
            },
        }

    def status_rows(current_cfg):
        now = time.monotonic()
        if now < float(status_cache["expires"]):
            return list(status_cache["rows"])
        rows = build_status_rows(current_cfg)
        for row in rows:
            row["output_media_url"] = _out_media_url(current_cfg, row.get("output_path"))
        status_cache["rows"] = rows
        status_cache["expires"] = now + STATUS_CACHE_SECONDS
        return rows

    def invalidate_status_cache() -> None:
        status_cache["expires"] = 0.0

    def wavespeed_balance_payload(*, force: bool = False):
        now = time.monotonic()
        cached_payload = balance_cache.get("payload")
        if not force and cached_payload is not None and now < float(balance_cache["expires"]):
            return cached_payload

        current_cfg = cfg()
        api_key_env = current_cfg.wavespeed.api_key_env
        configured = bool(os.environ.get(api_key_env))
        payload = {
            "ok": False,
            "enabled": current_cfg.wavespeed.enabled,
            "configured": configured,
            "balance": None,
            "currency": "USD",
            "detail": "",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dashboard_url": WAVESPEED_DASHBOARD_URL,
            "api_key_env": api_key_env,
        }
        if not configured:
            payload["detail"] = f"{api_key_env} is not set"
        else:
            try:
                payload["balance"] = get_balance(current_cfg)
                payload["ok"] = True
            except WaveSpeedBalanceError as exc:
                payload["detail"] = str(exc)

        balance_cache["payload"] = payload
        balance_cache["expires"] = now + WAVESPEED_BALANCE_CACHE_SECONDS
        return payload

    def job_response(record):
        return JSONResponse(status_code=http_status.HTTP_202_ACCEPTED, content=record.to_dict())

    def enqueue_prepare(current_cfg, tiktok_id: str, url: str, *, archive: bool, name: str):
        try:
            try_acquire_processing_lock(current_cfg.paths.work_dir, tiktok_id)
        except LockHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        def run_job():
            try:
                archived_ok = False
                if archive:
                    try:
                        append_link(current_cfg.paths.telegram_links_archive, url)
                        archived_ok = True
                    except Exception as exc:  # noqa: BLE001 - archive is best-effort
                        dashboard_logger.warning(
                            "could not append dashboard link to archive (non-fatal): %s",
                            exc,
                        )
                work = current_cfg.paths.work_dir / tiktok_id
                run_logger = get_logger("worker", work / "run.log")
                result, code = worker.run_prepare(current_cfg, tiktok_id, url, work, run_logger)
                processing_note = result.get("processing_note")
                if archive and archived_ok and processing_note:
                    try:
                        update_processing_note(
                            current_cfg.paths.telegram_links_archive,
                            url,
                            str(processing_note),
                        )
                    except Exception as exc:  # noqa: BLE001 - archive is best-effort
                        dashboard_logger.warning(
                            "could not update dashboard link processing note (non-fatal): %s",
                            exc,
                        )
                invalidate_status_cache()
                return result, code
            finally:
                release_processing_lock(current_cfg.paths.work_dir)

        return job_manager.enqueue(name, run_job, run_id=tiktok_id)

    def enqueue_decision(current_cfg, tiktok_id: str, stage: str, decision: str):
        normalized = decision.strip().lower()
        if normalized in YES_DECISIONS:
            is_yes = True
        elif normalized in NO_DECISIONS:
            is_yes = False
        else:
            raise HTTPException(status_code=422, detail="decision must be yes or no")

        try:
            pending_id = find_pending_id(current_cfg.paths.work_dir)
            pending = load_pending(current_cfg.paths.work_dir, tiktok_id)
        except PendingApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if pending_id != tiktok_id:
            raise HTTPException(
                status_code=409,
                detail=f"pending approval is for {pending_id}, not {tiktok_id}",
            )
        if pending["stage"] != stage:
            raise HTTPException(
                status_code=409,
                detail=f"pending stage is {pending['stage']}, not {stage}",
            )

        try:
            try_claim_approval(current_cfg.paths.work_dir, tiktok_id, "dashboard")
        except ApprovalLockHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        def run_job():
            try:
                work = current_cfg.paths.work_dir / tiktok_id
                run_logger = get_logger("worker", work / "run.log")
                if is_yes and stage == "frame":
                    worker._notify(
                        current_cfg,
                        f"{tiktok_id}: dashboard approval received — generating "
                        "the avatar still, processing... please wait.",
                        dashboard_logger,
                    )
                    result, code = worker.run_generate_avatar(
                        current_cfg, tiktok_id, work, run_logger
                    )
                elif is_yes and stage == "avatar":
                    worker._notify(
                        current_cfg,
                        f"{tiktok_id}: dashboard approval received — animating "
                        "with the configured provider, processing... please wait.",
                        dashboard_logger,
                    )
                    result, code = worker.run_animate(current_cfg, tiktok_id, work, run_logger)
                elif not is_yes and stage == "frame":
                    worker._notify(
                        current_cfg,
                        f"{tiktok_id}: dashboard rejected the extracted frame — "
                        "clearing this attempt without marking the link processed.",
                        dashboard_logger,
                    )
                    result, code = worker.run_reject_frame(
                        current_cfg, tiktok_id, work, run_logger
                    )
                else:
                    worker._notify(
                        current_cfg,
                        f"{tiktok_id}: dashboard requested another avatar still — "
                        "regenerating, processing... please wait.",
                        dashboard_logger,
                    )
                    result, code = worker.run_regenerate(
                        current_cfg, tiktok_id, work, run_logger
                    )
                invalidate_status_cache()
                return result, code
            finally:
                release_approval_lock(current_cfg.paths.work_dir, tiktok_id)

        job_name = f"decision:{stage}:{'yes' if is_yes else 'no'}"
        return job_manager.enqueue(job_name, run_job, run_id=tiktok_id)

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health():
        current_cfg = cfg()
        return {"status": "ok", "port": current_cfg.dashboard.port}

    @app.get("/api/status")
    def status():
        current_cfg = cfg()
        rows = status_rows(current_cfg)
        counts: dict[str, int] = {}
        for row in rows:
            state = str(row.get("status", "unknown"))
            counts[state] = counts.get(state, 0) + 1
        return {
            "rows": rows,
            "counts": counts,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    @app.get("/api/pending")
    def pending():
        current_cfg = cfg()
        try:
            item_id = find_pending_id(current_cfg.paths.work_dir)
            data = load_pending(current_cfg.paths.work_dir, item_id)
        except PendingApprovalError as exc:
            return {"pending": None, "detail": str(exc)}
        data = dict(data)
        data["ref_video_url"] = _work_media_url(current_cfg, item_id, data.get("ref_video_path"))
        data["frame1_url"] = _work_media_url(current_cfg, item_id, data.get("frame1_path"))
        data["avatar_frame_url"] = _work_media_url(
            current_cfg, item_id, data.get("avatar_frame_path")
        )
        return {"pending": data}

    @app.get("/api/services")
    def services():
        return gather_service_health(cfg())

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": job_manager.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        record = job_manager.get_job(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        return record

    @app.get("/api/config/providers")
    def get_providers():
        return provider_payload()

    @app.get("/api/wavespeed/balance")
    def get_wavespeed_balance(force: bool = Query(default=False)):
        return wavespeed_balance_payload(force=force)

    @app.put("/api/config/providers")
    def put_providers(request: ProvidersRequest):
        path = overrides_path(config_path)
        previous_exists = path.exists()
        try:
            previous = load_overrides(config_path)
        except ConfigOverrideError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            write_provider_overrides(
                config_path,
                avatar_frame_provider=request.avatar_frame_provider,
                animation_provider=request.animation_provider,
                wavespeed_enabled=request.wavespeed_enabled,
            )
            load_config(config_path)
        except (ConfigError, ConfigOverrideError) as exc:
            if previous_exists:
                write_overrides(config_path, previous)
            else:
                clear_overrides(config_path)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return provider_payload()

    @app.delete("/api/config/providers")
    def delete_providers():
        clear_overrides(config_path)
        return provider_payload()

    @app.post("/api/links")
    def submit_link(request: LinkRequest):
        url = request.url.strip()
        if not _looks_like_tiktok_url(url):
            raise HTTPException(status_code=422, detail="url must be a TikTok URL")
        current_cfg = cfg()
        _pending_conflict(current_cfg)
        tiktok_id = extract_tiktok_id(url)
        return job_response(
            enqueue_prepare(current_cfg, tiktok_id, url, archive=True, name="prepare:link")
        )

    @app.post("/api/queue/run-next")
    def run_next():
        current_cfg = cfg()
        _pending_conflict(current_cfg)
        pick_next.ensure_local_copy(current_cfg.paths.numbers_sheet, dashboard_logger)
        urls = pick_next.read_urls(current_cfg.paths.numbers_sheet)
        state = load_state(current_cfg.paths.processed_json)
        nxt = pick_next.select_next(urls, seen_ids(state))
        if nxt is None:
            return Response(status_code=http_status.HTTP_204_NO_CONTENT)
        return job_response(
            enqueue_prepare(
                current_cfg,
                nxt["id"],
                nxt["url"],
                archive=False,
                name="prepare:run-next",
            )
        )

    @app.post("/api/pending/{item_id}/decision")
    def decide_pending(item_id: str, request: DecisionRequest):
        return job_response(enqueue_decision(cfg(), item_id, request.stage, request.decision))

    @app.post("/api/flagged/{item_id}/unflag")
    def unflag_item(item_id: str):
        current_cfg = cfg()
        try:
            unflag(current_cfg.paths.processed_json, item_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="flagged id not found") from exc
        invalidate_status_cache()
        return {"status": "unflagged", "id": item_id}

    @app.get("/api/logs/{name}")
    def logs(name: str, lines: int = Query(default=200, ge=1, le=1000)):
        try:
            return tail_log(cfg(), name, lines=lines)
        except LogTailError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/media/work/{item_id}/{filename}")
    def work_media(item_id: str, filename: str):
        if "/" in item_id or "/" in filename:
            raise HTTPException(status_code=404, detail="media not found")
        current_cfg = cfg()
        root = current_cfg.paths.work_dir / item_id
        return _media_response(root, root / filename)

    @app.get("/api/media/out/{relpath:path}")
    def out_media(relpath: str):
        current_cfg = cfg()
        root = current_cfg.paths.out_dir
        return _media_response(root, root / relpath)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local avatar-pipeline dashboard.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    loaded_cfg = load_config(args.config)
    port = args.port or int(os.environ.get("DASHBOARD_PORT", loaded_cfg.dashboard.port))

    import uvicorn

    uvicorn.run(create_app(args.config), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
