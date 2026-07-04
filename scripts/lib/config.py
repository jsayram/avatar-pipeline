"""Typed, validated access to config.yaml (see config.example.yaml).

Every path, threshold, endpoint, provider choice, retry count, schedule and
clip length is config-driven — scripts never hardcode them. Relative paths are
resolved against the directory containing the config file, so n8n can invoke
the scripts from any working directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config_overrides import ConfigOverrideError, load_overrides, merge_overrides

VALID_AVATAR_FRAME_PROVIDERS = ("local_comfyui", "wavespeed_seedream", "mock")
VALID_ANIMATION_PROVIDERS = ("local_comfyui", "wavespeed", "mock")
VALID_NOTIFICATION_PROVIDERS = ("macos", "email", "none")


class ConfigError(Exception):
    """Raised when config.yaml is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class Paths:
    numbers_sheet: Path
    work_dir: Path
    out_dir: Path
    processed_json: Path
    done_csv: Path
    lora_path: Path
    avatar_reference: Path
    workflows_dir: Path
    status_sheet: Path
    image_out_dir: Path
    video_out_dir: Path
    telegram_links_archive: Path


@dataclass(frozen=True)
class Endpoints:
    comfyui_url: str
    gate_url: str
    n8n_url: str


@dataclass(frozen=True)
class Identity:
    cosine_min: float
    sample_fps: int
    max_retries: int


@dataclass(frozen=True)
class Video:
    base_model: str
    wan_model: str
    max_clip_seconds: int
    seed: int | None
    cookies_file: Path | None  # yt-dlp --cookies <file>, for TikTok's login-gated
                                # "sensitive content" posts (see docs/TIKTOK-COOKIES.md)


@dataclass(frozen=True)
class Animation:
    provider: str
    fallback_to_local_on_cloud_error: bool


@dataclass(frozen=True)
class AvatarFrame:
    provider: str
    wavespeed_model: str
    size: str
    prompt: str
    identity_references: tuple[Path, ...]


@dataclass(frozen=True)
class WaveSpeed:
    enabled: bool
    api_base: str
    api_key_env: str
    model: str
    poll_interval_seconds: float
    timeout_seconds: int
    character_orientation: str  # "image" (10s clip cap) | "video" (30s cap) — Kling Motion Control
    keep_original_sound: bool
    prompt: str
    negative_prompt: str


@dataclass(frozen=True)
class ComfyUISettings:
    poll_interval_seconds: float
    image_timeout_seconds: int
    video_timeout_seconds: int


@dataclass(frozen=True)
class Schedule:
    cron: str


@dataclass(frozen=True)
class Notifications:
    enabled: bool
    provider: str


@dataclass(frozen=True)
class Telegram:
    enabled: bool
    bot_token_env: str
    chat_id: str
    max_approval_attempts: int


@dataclass(frozen=True)
class Cleanup:
    enabled: bool  # delete disposable per-attempt scratch files (raw pre-strip
                   # animation attempts, sampled gate-check frames) from work/<id>/
                   # once a run reaches ANY terminal outcome. These are pure
                   # duplicates of what's already safely kept in out-pipe/
                   # video-out/ (published or failed-and-kept) — nothing is lost.
                   # Small essentials (run.log, ref.mp4, frame stills) are kept
                   # for audit/debugging. Default true: this is most of a run's
                   # disk footprint (often 90%+) and accumulates forever otherwise.


@dataclass(frozen=True)
class Dashboard:
    port: int
    log_tail_lines: int
    launchd_labels: dict[str, str]


@dataclass(frozen=True)
class Config:
    base_dir: Path  # directory of the config file; anchor for relative paths
    paths: Paths
    endpoints: Endpoints
    identity: Identity
    video: Video
    avatar_frame: AvatarFrame
    animation: Animation
    wavespeed: WaveSpeed
    comfyui: ComfyUISettings
    schedule: Schedule
    notifications: Notifications
    telegram: Telegram
    cleanup: Cleanup
    dashboard: Dashboard


def _section(data: dict, name: str) -> dict:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"config section '{name}' must be a mapping, got {type(value).__name__}")
    return value


def _resolve(base_dir: Path, raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (base_dir / p).resolve()


def load_config(config_path: str | Path, *, apply_overrides: bool = True) -> Config:
    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        raise ConfigError(
            f"config file not found: {config_path}\n"
            "Copy config.example.yaml to config.yaml and edit the paths."
        )
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping at top level")

    base_dir = config_path.resolve().parent
    if apply_overrides:
        try:
            data = merge_overrides(data, load_overrides(config_path))
        except ConfigOverrideError as exc:
            raise ConfigError(str(exc)) from exc

    p = _section(data, "paths")
    required_paths = (
        "numbers_sheet", "work_dir", "out_dir", "processed_json",
        "done_csv", "lora_path", "avatar_reference",
    )
    missing = [k for k in required_paths if not p.get(k)]
    if missing:
        raise ConfigError(f"config paths section is missing: {', '.join(missing)}")
    numbers_sheet = _resolve(base_dir, p["numbers_sheet"])
    out_dir = _resolve(base_dir, p["out_dir"])
    default_status_sheet = numbers_sheet.parent / "links_status.csv"
    paths = Paths(
        numbers_sheet=numbers_sheet,
        work_dir=_resolve(base_dir, p["work_dir"]),
        out_dir=out_dir,
        processed_json=_resolve(base_dir, p["processed_json"]),
        done_csv=_resolve(base_dir, p["done_csv"]),
        lora_path=_resolve(base_dir, p["lora_path"]),
        avatar_reference=_resolve(base_dir, p["avatar_reference"]),
        workflows_dir=_resolve(base_dir, p.get("workflows_dir", "./comfyui")),
        status_sheet=_resolve(base_dir, p["status_sheet"]) if p.get("status_sheet")
        else default_status_sheet,
        image_out_dir=_resolve(base_dir, p["image_out_dir"]) if p.get("image_out_dir")
        else out_dir / "image-out",
        video_out_dir=_resolve(base_dir, p["video_out_dir"]) if p.get("video_out_dir")
        else out_dir / "video-out",
        telegram_links_archive=_resolve(base_dir, p["telegram_links_archive"])
        if p.get("telegram_links_archive")
        else numbers_sheet.parent / "linksThroughTelegram.numbers",
    )

    e = _section(data, "endpoints")
    endpoints = Endpoints(
        comfyui_url=str(e.get("comfyui_url", "http://localhost:8188")).rstrip("/"),
        gate_url=str(e.get("gate_url", "http://localhost:8189")).rstrip("/"),
        n8n_url=str(e.get("n8n_url", "http://localhost:5678")).rstrip("/"),
    )

    i = _section(data, "identity")
    identity = Identity(
        cosine_min=float(i.get("cosine_min", 0.88)),
        sample_fps=int(i.get("sample_fps", 1)),
        max_retries=int(i.get("max_retries", 2)),
    )
    if not 0.0 < identity.cosine_min <= 1.0:
        raise ConfigError(f"identity.cosine_min must be in (0, 1], got {identity.cosine_min}")
    if identity.max_retries < 0:
        raise ConfigError("identity.max_retries must be >= 0")

    v = _section(data, "video")
    seed = v.get("seed")
    cookies_file = v.get("cookies_file")
    video = Video(
        base_model=str(v.get("base_model", "flux1-schnell")),
        wan_model=str(v.get("wan_model", "wan2.2-animate")),
        max_clip_seconds=int(v.get("max_clip_seconds", 8)),
        seed=None if seed is None else int(seed),
        cookies_file=_resolve(base_dir, cookies_file) if cookies_file else None,
    )
    if video.max_clip_seconds <= 0:
        raise ConfigError("video.max_clip_seconds must be > 0")

    af = _section(data, "avatar_frame")
    identity_references_raw = af.get("identity_references", [])
    if identity_references_raw is None:
        identity_references_raw = []
    if not isinstance(identity_references_raw, list):
        raise ConfigError("avatar_frame.identity_references must be a list of paths")
    if any(not isinstance(raw, str) or not raw for raw in identity_references_raw):
        raise ConfigError("avatar_frame.identity_references entries must be non-empty path strings")
    avatar_frame = AvatarFrame(
        provider=str(af.get("provider", "local_comfyui")),
        wavespeed_model=str(af.get("wavespeed_model", "bytedance/seedream-v4.5/edit") or ""),
        size=str(af.get("size", "") or ""),
        prompt=str(af.get("prompt", "") or ""),
        identity_references=tuple(
            _resolve(base_dir, raw) for raw in identity_references_raw
        ),
    )
    if avatar_frame.provider not in VALID_AVATAR_FRAME_PROVIDERS:
        raise ConfigError(
            f"avatar_frame.provider must be one of {VALID_AVATAR_FRAME_PROVIDERS}, "
            f"got {avatar_frame.provider!r}"
        )
    if len(avatar_frame.identity_references) > 9:
        raise ConfigError(
            "avatar_frame.identity_references supports at most 9 images "
            "(Seedream accepts 10 total images and image 1 is the source frame)"
        )

    a = _section(data, "animation")
    animation = Animation(
        provider=str(a.get("provider", "local_comfyui")),
        fallback_to_local_on_cloud_error=bool(a.get("fallback_to_local_on_cloud_error", True)),
    )
    if animation.provider not in VALID_ANIMATION_PROVIDERS:
        raise ConfigError(
            f"animation.provider must be one of {VALID_ANIMATION_PROVIDERS}, "
            f"got {animation.provider!r}"
        )

    w = _section(data, "wavespeed")
    wavespeed = WaveSpeed(
        enabled=bool(w.get("enabled", False)),
        api_base=str(w.get("api_base", "https://api.wavespeed.ai")).rstrip("/"),
        api_key_env=str(w.get("api_key_env", "WAVESPEED_API_KEY")),
        model=str(w.get("model", "") or ""),
        poll_interval_seconds=float(w.get("poll_interval_seconds", 5)),
        timeout_seconds=int(w.get("timeout_seconds", 1800)),
        character_orientation=str(w.get("character_orientation", "image")),
        keep_original_sound=bool(w.get("keep_original_sound", True)),
        prompt=str(w.get("prompt", "") or ""),
        negative_prompt=str(w.get("negative_prompt", "") or ""),
    )
    if wavespeed.character_orientation not in ("image", "video"):
        raise ConfigError(
            f"wavespeed.character_orientation must be 'image' or 'video', "
            f"got {wavespeed.character_orientation!r}"
        )

    c = _section(data, "comfyui")
    comfyui = ComfyUISettings(
        poll_interval_seconds=float(c.get("poll_interval_seconds", 2)),
        image_timeout_seconds=int(c.get("image_timeout_seconds", 900)),
        video_timeout_seconds=int(c.get("video_timeout_seconds", 21600)),
    )

    s = _section(data, "schedule")
    schedule = Schedule(cron=str(s.get("cron", "0 2 * * *")))

    n = _section(data, "notifications")
    notifications = Notifications(
        enabled=bool(n.get("enabled", True)),
        provider=str(n.get("provider", "macos")),
    )
    if notifications.provider not in VALID_NOTIFICATION_PROVIDERS:
        raise ConfigError(
            f"notifications.provider must be one of {VALID_NOTIFICATION_PROVIDERS}, "
            f"got {notifications.provider!r}"
        )

    t = _section(data, "telegram")
    telegram = Telegram(
        enabled=bool(t.get("enabled", False)),
        bot_token_env=str(t.get("bot_token_env", "TELEGRAM_BOT_TOKEN")),
        chat_id=str(t.get("chat_id", "") or ""),
        max_approval_attempts=int(t.get("max_approval_attempts", 3)),
    )
    if telegram.max_approval_attempts < 1:
        raise ConfigError("telegram.max_approval_attempts must be >= 1")

    c = _section(data, "cleanup")
    cleanup = Cleanup(enabled=bool(c.get("enabled", True)))

    d = _section(data, "dashboard")
    raw_labels = d.get("launchd_labels", {})
    if raw_labels is None:
        raw_labels = {}
    if not isinstance(raw_labels, dict):
        raise ConfigError("dashboard.launchd_labels must be a mapping")
    default_labels = {
        "n8n": "com.jramirez.avatar.n8n",
        "gate": "com.jramirez.avatar.gate",
        "dashboard": "com.jramirez.avatar.dashboard",
    }
    launchd_labels = {
        **default_labels,
        **{str(k): str(v) for k, v in raw_labels.items() if v},
    }
    dashboard = Dashboard(
        port=int(d.get("port", 8190)),
        log_tail_lines=int(d.get("log_tail_lines", 200)),
        launchd_labels=launchd_labels,
    )
    if dashboard.port <= 0:
        raise ConfigError("dashboard.port must be > 0")
    if dashboard.log_tail_lines < 1:
        raise ConfigError("dashboard.log_tail_lines must be >= 1")

    return Config(
        base_dir=base_dir,
        paths=paths,
        endpoints=endpoints,
        identity=identity,
        video=video,
        avatar_frame=avatar_frame,
        animation=animation,
        wavespeed=wavespeed,
        comfyui=comfyui,
        schedule=schedule,
        notifications=notifications,
        telegram=telegram,
        cleanup=cleanup,
        dashboard=dashboard,
    )
