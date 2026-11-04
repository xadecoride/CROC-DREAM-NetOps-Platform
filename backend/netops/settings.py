from __future__ import annotations

import contextlib
import hmac
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from netops.constants import console_ports, const
from netops.enums import UserRole


def _c(dotted: str, cast=None):
    """default_factory, читающий значение из config/constants.yaml."""
    return lambda: cast(const(dotted)) if cast else const(dotted)


class ApiPrincipal(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    role: UserRole
    allowed_system: str | None = Field(default=None, exclude=True)



class AuthProfile(BaseModel):
    username: str = Field(min_length=1)
    password: SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NETOPS_",
        env_file=(".env", "backend/.env", str(Path(__file__).resolve().parent.parent / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default_factory=_c("app.name"))
    database_url: str = Field(default_factory=_c("services.database_url"))
    redis_url: str = Field(default_factory=_c("services.redis_url"))

    intent_repo_path: Path = Field(default_factory=_c("paths.intent_repo", Path))
    inventory_file: str = Field(default_factory=_c("paths.inventory_file"))
    templates_path: Path = Field(default_factory=_c("paths.templates", Path))
    diff_filters_path: Path | None = None
    normalization_rules_path: Path | None = None

    # "offline" emulates the lab with running-configs stored as files; the
    # Scrapli/Nornir driver will be registered here once it is ready.
    network_driver: Literal["offline", "scrapli"] = Field(default_factory=_c("network.driver"))
    offline_lab_path: Path = Field(default_factory=_c("paths.offline_lab", Path))

    commit_confirm_timeout_seconds: int = Field(default_factory=_c("network.commit_confirm_timeout_seconds"), ge=30, le=3600)
    max_ping_loss_percent: float = Field(default_factory=_c("network.max_ping_loss_percent"), ge=0, le=100)
    post_check_attempts: int = Field(default_factory=_c("network.post_check_attempts"), ge=1, le=60)
    post_check_interval_seconds: float = Field(default_factory=_c("network.post_check_interval_seconds"), ge=0)

    drift_scan_interval_seconds: int = Field(default_factory=_c("scheduling.drift_scan_interval_seconds"), ge=60)
    metrics_interval_seconds: int = Field(default_factory=_c("scheduling.metrics_interval_seconds"), ge=10)
    # Как часто заново проверять протоколы устройств и переписывать цели сборщиков.
    collector_targets_interval_seconds: int = Field(default_factory=_c("scheduling.collector_targets_interval_seconds"), ge=300)
    job_timeout_seconds: int = Field(default_factory=_c("scheduling.job_timeout_seconds"), ge=60)

    # {"<токен>": {"username": "alice", "role": "admin"}}
    api_tokens: dict[str, ApiPrincipal] = Field(default_factory=dict)
    # {"lab": {"username": "admin", "password": "admin"}}
    # Профили стендов задаются при подключении (Runtime Overlay) или через NETOPS_AUTH_PROFILES.
    auth_profiles: dict[str, AuthProfile] = Field(default_factory=dict)

    # LLM Risk Assistant settings (Multi-provider cascade: Ollama -> Groq -> Gemini -> MiMo -> Heuristic)
    # 0. Local Ollama / vLLM (Empty URL = disabled; no API key required)
    ollama_url: str = Field(default_factory=_c("llm.ollama.url"))
    ollama_model: str = Field(default_factory=_c("llm.ollama.model"))
    ollama_timeout_seconds: float = Field(default_factory=_c("llm.ollama.timeout_seconds"))

    # 1. Groq (Ultra-fast Qwen 3.8 27B / GPT-OSS 120B)
    groq_api_key: SecretStr | None = None
    groq_base_url: str = Field(default_factory=_c("llm.groq.base_url"))
    groq_model: str = Field(default_factory=_c("llm.groq.model"))
    groq_timeout_seconds: float = Field(default_factory=_c("llm.groq.timeout_seconds"))

    # 2. Google Gemini (Cloud Backup with fast 2.0s timeout)
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = Field(default_factory=_c("llm.gemini.base_url"))
    gemini_model: str = Field(default_factory=_c("llm.gemini.model"))
    gemini_timeout_seconds: float = Field(default_factory=_c("llm.gemini.timeout_seconds"))

    # 3. Team / Default provider (Xiaomi MiMo or custom proxy)
    llm_api_key: SecretStr | None = None
    llm_base_url: str = Field(default_factory=_c("llm.team.base_url"))
    llm_model: str = Field(default_factory=_c("llm.team.model"))
    llm_timeout_seconds: float = Field(default_factory=_c("llm.team.timeout_seconds"))

    # Custom dynamic LLM cascade configs: [{"name": "Groq", "base_url": "...", "model": "...", "api_key": "...", "timeout_seconds": 2.0}]
    llm_cascade: list[dict[str, object]] = Field(default_factory=list)

    # NetBox as the inventory source (empty URL = inventory.yaml of the intent repository).
    netbox_url: str = ""
    netbox_token: SecretStr | None = None

    # Containerlab remote management (for emergency hard reset)
    clab_host: str | None = None
    clab_port: int = Field(default_factory=_c("network.clab_ssh_port"))
    clab_user: str | None = None
    clab_password: SecretStr | None = None
    clab_key_path: str | None = None
    clab_topology_path: str = ""
    clab_port_map: dict[str, str] = Field(default_factory=dict)

    # Prometheus-compatible query API (Prometheus или VictoriaMetrics; empty URL = falls back to
    # the SSH CLI cache; no data is invented)
    prometheus_url: str = ""
    # SNMP v2c community для проверки агента и snmp-exporter. Пусто — v2c не проверяется
    # (значения «public» по умолчанию в коде нет).
    snmp_community: SecretStr | None = None

    # Telegram Incident Alerting & Broadcast
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str = ""
    telegram_chat_id: str | None = None
    telegram_chat_ids: list[int] = Field(default_factory=list)

    # GitHub / Git SoT repository metadata
    github_repo_url: str = ""

    # Web panel URLs & fabric defaults (human-configurable or trigger-populated)
    frontend_port: int = Field(default_factory=_c("console.owner_port"), ge=1, le=65535)
    public_web_url: str = ""
    default_fabric_id: str = Field(default_factory=_c("app.default_fabric_id"))
    default_domain: str = Field(default_factory=_c("app.default_domain"))

    # Browser origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in console_ports()
        ]
    )
    # Browser cookie session lifetime (one NOC shift by default).
    session_ttl_seconds: int = Field(default_factory=_c("session.ttl_seconds"), ge=60)


    @field_validator("intent_repo_path", "templates_path", "offline_lab_path", mode="after")
    @classmethod
    def _resolve_paths(cls, path: Path) -> Path:
        if path.is_absolute():
            return path
        if not path.exists():
            backend_dir = Path(__file__).resolve().parent.parent
            root_dir = backend_dir.parent
            if (root_dir / path).exists():
                return (root_dir / path).resolve()
            if (root_dir / path.name).exists():
                return (root_dir / path.name).resolve()
        return path

    # Повторы post-check не должны занимать больше половины таймера commit confirmed.
    @model_validator(mode="after")
    def _post_check_fits_confirm_timer(self) -> Self:
        window = (self.post_check_attempts - 1) * self.post_check_interval_seconds
        if window > self.commit_confirm_timeout_seconds / 2:
            raise ValueError(
                f"Post-check retries take {window:g}s, more than half of the "
                f"{self.commit_confirm_timeout_seconds}s commit-confirm timer"
            )
        if self.telegram_chat_id and not self.telegram_chat_ids:
            try:
                self.telegram_chat_ids = [int(self.telegram_chat_id.strip())]
            except ValueError:
                _ = None
        elif self.telegram_chat_ids and not self.telegram_chat_id:
            self.telegram_chat_id = str(self.telegram_chat_ids[0])
        import os
        import sys

        in_pytest = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
        if not in_pytest or "offline_lab_path" in self.model_fields_set:
            self.apply_runtime_overlay(respect_explicit=True)
        return self

    def apply_runtime_overlay(self, *, respect_explicit: bool = False) -> dict[str, object]:
        from netops.services.state import get_state_manager

        state = get_state_manager(self)
        explicit = self.model_fields_set if respect_explicit else set()
        if "auth_profiles" not in explicit:
            try:
                raw_profiles = state.get_db_state(
                    "custom_auth_profiles", {}, database_url=self.database_url
                )
                if isinstance(raw_profiles, dict):
                    for prof_name, creds in raw_profiles.items():
                        if isinstance(creds, dict) and creds.get("username") and creds.get("password"):
                            self.auth_profiles[prof_name] = AuthProfile(
                                username=str(creds["username"]),
                                password=SecretStr(str(creds["password"])),
                            )
            except Exception:
                _ = None

        try:
            data = state.get_db_state("runtime_settings", {}, database_url=self.database_url)
            if not isinstance(data, dict) or not data:
                return {}
            if data.get("network_driver") in ("offline", "scrapli") and "network_driver" not in explicit:
                self.network_driver = data["network_driver"]
            for secret_field in ("telegram_bot_token", "clab_password", "groq_api_key", "gemini_api_key"):
                if data.get(secret_field) and secret_field not in explicit:
                    setattr(self, secret_field, SecretStr(str(data[secret_field])))
            if data.get("telegram_bot_username") and "telegram_bot_username" not in explicit:
                self.telegram_bot_username = str(data["telegram_bot_username"]).lstrip("@")
            raw_cids: list[object] = []
            if data.get("telegram_chat_id") and "telegram_chat_id" not in explicit:
                self.telegram_chat_id = str(data["telegram_chat_id"])
                raw_cids.append(self.telegram_chat_id)
            if isinstance(data.get("telegram_chat_ids"), list) and "telegram_chat_ids" not in explicit:
                raw_cids.extend(data["telegram_chat_ids"])
            for raw_cid in raw_cids:
                with contextlib.suppress(ValueError):
                    cid = int(str(raw_cid).strip())
                    if cid not in self.telegram_chat_ids:
                        self.telegram_chat_ids.append(cid)
            for str_field in (
                "github_repo_url",
                "clab_host",
                "clab_user",
                "clab_topology_path",
                "public_web_url",
                "default_fabric_id",
                "default_domain",
            ):
                if data.get(str_field) and str_field not in explicit:
                    setattr(self, str_field, str(data[str_field]))
            for nullable_str_field in ("ollama_url", "prometheus_url"):
                if data.get(nullable_str_field) is not None and nullable_str_field not in explicit:
                    setattr(self, nullable_str_field, str(data[nullable_str_field]))
            if isinstance(data.get("clab_port_map"), dict) and "clab_port_map" not in explicit:
                self.clab_port_map = {
                    str(k): str(v) for k, v in data["clab_port_map"].items() if k and v
                }
            if isinstance(data.get("llm_cascade"), list) and "llm_cascade" not in explicit:
                self.llm_cascade = [
                    item for item in data["llm_cascade"] if isinstance(item, dict)
                ]
            for int_field in (
                "clab_port",
                "metrics_interval_seconds",
                "drift_scan_interval_seconds",
                "commit_confirm_timeout_seconds",
                "frontend_port",
            ):
                if data.get(int_field) is not None and int_field not in explicit:
                    with contextlib.suppress(TypeError, ValueError):
                        setattr(self, int_field, int(data[int_field]))
            min_confirm = int((self.post_check_attempts - 1) * self.post_check_interval_seconds * 2)
            if self.commit_confirm_timeout_seconds < min_confirm:
                self.commit_confirm_timeout_seconds = max(30, min_confirm)
            if (
                data.get("max_ping_loss_percent") is not None
                and "max_ping_loss_percent" not in explicit
            ):
                with contextlib.suppress(TypeError, ValueError):
                    self.max_ping_loss_percent = float(data["max_ping_loss_percent"])
            return data
        except Exception:
            return {}

    def save_runtime_overlay(self, updates: dict[str, object]) -> dict[str, object]:
        from netops.services.state import get_state_manager

        state = get_state_manager(self)
        loaded = state.get_db_state("runtime_settings", {}, database_url=self.database_url)
        existing: dict[str, object] = dict(loaded) if isinstance(loaded, dict) else {}
        for k, v in updates.items():
            if v is not None:
                existing[k] = v
        state.set_db_state("runtime_settings", existing, database_url=self.database_url)
        self.apply_runtime_overlay()
        from netops.services.stands import invalidate_stand_connection_cache

        invalidate_stand_connection_cache()
        return existing

    def authenticate(self, token: str) -> ApiPrincipal | None:
        candidate = token.encode()
        for known, principal in self.api_tokens.items():
            if hmac.compare_digest(known.encode(), candidate):
                return principal
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
