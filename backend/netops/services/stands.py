"""Multi-stand registry, per-stand SSH jump connection resolver, and secret encryption."""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.constants import const
from netops.enums import DeviceStatus
from netops.errors import ConflictError, NotFoundError
from netops.models import Device, Stand
from netops.security import decrypt_secret, encrypt_secret
from netops.services.state import get_state_manager
from netops.settings import Settings, get_settings

logger = logging.getLogger(__name__)

STANDS_REGISTRY_STATE_KEY = "stands_registry"


def _parse_port_map(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): (dict(v) if isinstance(v, dict) else str(v)) for k, v in raw.items() if k and v is not None}
    if isinstance(raw, str):
        with contextlib.suppress(Exception):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): (dict(v) if isinstance(v, dict) else str(v)) for k, v in parsed.items() if k and v is not None}
    return {}


def _serialize_stand_row(
    stand: Stand, stats: dict[str, int] | None = None, default_slug: str = "croc-dc1-pod1"
) -> dict[str, Any]:
    from netops.services.vitality import evaluate_stand_vitality

    s = stats or {"total_devices": 0, "in_sync": 0, "drifted": 0, "unreachable": 0}
    port_map = _parse_port_map(stand.port_map_json)
    row = {
        "id": stand.id,
        "slug": stand.slug,
        "name": stand.name,
        "site": stand.site or stand.slug,
        "driver": stand.driver or "scrapli",
        "jump_host": stand.jump_host,
        "jump_port": stand.jump_port or 22,
        "jump_user": stand.jump_user,
        "has_password": bool(stand.encrypted_password),
        "port_map": port_map,
        "topology_path": stand.topology_path,
        "intent_subpath": stand.intent_subpath,
        "queue_name": stand.queue_name or "default",
        "status": stand.status or "ONLINE",
        "description": stand.description,
        "is_default": stand.slug == default_slug,
        "total_devices": s["total_devices"],
        "in_sync": s["in_sync"],
        "drifted": s["drifted"],
        "unreachable": s["unreachable"],
        "created_at": stand.created_at.isoformat() if stand.created_at else None,
        "updated_at": stand.updated_at.isoformat() if stand.updated_at else None,
    }
    row.update(evaluate_stand_vitality(row, s))
    return row


_CONN_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CONN_CACHE_LOCK = threading.Lock()


def invalidate_stand_connection_cache() -> None:
    """Drop cached stand connections (called on every registry write)."""
    with _CONN_CACHE_LOCK:
        _CONN_CACHE.clear()


def resolve_stand_connection(
    stand_slug_or_host: str | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    """Resolve stand connection through a process-local TTL cache (registry read per SSH connect)."""
    eff = settings or get_settings()
    ck = ((stand_slug_or_host or "").strip(), str(eff.database_url))
    ttl = float(const("network.stand_connection_cache_ttl_seconds"))
    now = time.monotonic()
    with _CONN_CACHE_LOCK:
        hit = _CONN_CACHE.get(ck)
    if hit is not None and now - hit[0] < ttl:
        return {**hit[1], "port_map": dict(hit[1]["port_map"])}
    result = _resolve_stand_connection_uncached(stand_slug_or_host, eff)
    with _CONN_CACHE_LOCK:
        _CONN_CACHE[ck] = (now, result)
    return {**result, "port_map": dict(result["port_map"])}


def _resolve_stand_connection_uncached(
    stand_slug_or_host: str | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    """Resolve jump_host, jump_port, jump_user, decrypted jump_password, and port_map for a stand."""
    eff_settings = settings or get_settings()
    state = get_state_manager(eff_settings)
    registry = state.get_db_state(
        STANDS_REGISTRY_STATE_KEY, {}, database_url=eff_settings.database_url
    )
    if not isinstance(registry, dict):
        registry = {}

    key = (stand_slug_or_host or "").strip()
    if key.startswith("stand:"):
        key = key[6:].strip()

    entry: dict[str, Any] | None = None
    if key:
        entry = registry.get(key) if isinstance(registry.get(key), dict) else None
        if entry is None:
            for item in registry.values():
                if isinstance(item, dict) and (
                    item.get("jump_host") == key or item.get("site") == key
                ):
                    entry = item
                    break
    elif registry:
        rt_state = state.get_db_state(
            "runtime_settings", {}, database_url=eff_settings.database_url
        )
        active_slug = (
            str(rt_state["active_stand_slug"]).strip()
            if isinstance(rt_state, dict) and rt_state.get("active_stand_slug")
            else ""
        )
        default_slug = (
            active_slug
            or getattr(eff_settings, "default_fabric_id", "croc-dc1-pod1")
            or "croc-dc1-pod1"
        )
        if isinstance(registry.get(default_slug), dict):
            entry = registry[default_slug]

    if entry:
        enc_pw = entry.get("encrypted_password")
        plain_pw: str | None = None
        if enc_pw:
            with contextlib.suppress(Exception):
                plain_pw = decrypt_secret(str(enc_pw)) or None
        return {
            "slug": str(entry.get("slug") or key or ""),
            "driver": str(entry.get("driver") or "scrapli"),
            "jump_host": str(entry.get("jump_host") or ""),
            "jump_port": int(entry.get("jump_port") or 22),
            "jump_user": str(entry.get("jump_user") or "crocdream"),
            "jump_password": plain_pw,
            "port_map": _parse_port_map(entry.get("port_map")),
            "topology_path": entry.get("topology_path"),
            "intent_subpath": entry.get("intent_subpath"),
            "queue_name": str(entry.get("queue_name") or "default"),
        }

    rt = state.get_db_state("runtime_settings", {}, database_url=eff_settings.database_url)
    rt_dict = rt if isinstance(rt, dict) else {}
    raw_pw = rt_dict.get("clab_password") or (
        eff_settings.clab_password.get_secret_value() if eff_settings.clab_password else None
    )
    if raw_pw and str(raw_pw).startswith("enc:"):
        with contextlib.suppress(Exception):
            raw_pw = decrypt_secret(str(raw_pw))
    return {
        "slug": key or getattr(eff_settings, "default_fabric_id", "croc-dc1-pod1"),
        "driver": str(rt_dict.get("network_driver") or eff_settings.network_driver or "scrapli"),
        "jump_host": str(rt_dict.get("clab_host") or eff_settings.clab_host or ""),
        "jump_port": int(rt_dict.get("clab_port") or eff_settings.clab_port or 221),
        "jump_user": str(rt_dict.get("clab_user") or eff_settings.clab_user or "crocdream"),
        "jump_password": str(raw_pw) if raw_pw else None,
        "port_map": _parse_port_map(rt_dict.get("clab_port_map")),
        "topology_path": rt_dict.get("clab_topology_path") or eff_settings.clab_topology_path,
        "intent_subpath": None,
        "queue_name": "default",
    }


class StandService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def _default_slug(self) -> str:
        return getattr(self._settings, "default_fabric_id", "croc-dc1-pod1") or "croc-dc1-pod1"

    def _sync_registry_cache(self, stands: list[Stand] | None = None) -> dict[str, dict[str, Any]]:
        rows = (
            stands
            if stands is not None
            else list(self._session.scalars(select(Stand).order_by(Stand.id)).all())
        )
        registry: dict[str, dict[str, Any]] = {}
        for st in rows:
            registry[st.slug] = {
                "slug": st.slug,
                "name": st.name,
                "site": st.site or st.slug,
                "driver": st.driver or "scrapli",
                "jump_host": st.jump_host,
                "jump_port": st.jump_port or 22,
                "jump_user": st.jump_user or "crocdream",
                "encrypted_password": st.encrypted_password,
                "port_map": _parse_port_map(st.port_map_json),
                "topology_path": st.topology_path,
                "intent_subpath": st.intent_subpath,
                "queue_name": st.queue_name or "default",
                "status": st.status or "ONLINE",
            }
        state = get_state_manager(self._settings)
        state.set_db_state(
            STANDS_REGISTRY_STATE_KEY, registry, database_url=self._settings.database_url
        )
        invalidate_stand_connection_cache()
        return registry

    def _compute_device_stats(
        self, fabric_id: str | None = None
    ) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
        stmt = select(Device)
        if fabric_id:
            stmt = stmt.where(
                (Device.fabric_id == fabric_id) | (Device.site == fabric_id)
            )
        devices = list(self._session.scalars(stmt.limit(5000)).all())
        stats_by_slug: dict[str, dict[str, int]] = {}
        site_by_slug: dict[str, str] = {}
        default_slug = self._default_slug()
        for dev in devices:
            slug = (
                fabric_id
                if (fabric_id and fabric_id in {dev.fabric_id, dev.site})
                else (dev.fabric_id or dev.site or default_slug).strip()
            )
            if dev.site and slug not in site_by_slug:
                site_by_slug[slug] = dev.site
            bucket = stats_by_slug.setdefault(
                slug, {"total_devices": 0, "in_sync": 0, "drifted": 0, "unreachable": 0}
            )
            bucket["total_devices"] += 1
            if dev.status is DeviceStatus.IN_SYNC:
                bucket["in_sync"] += 1
            elif dev.status is DeviceStatus.DRIFT_DETECTED:
                bucket["drifted"] += 1
            elif dev.status is DeviceStatus.UNREACHABLE:
                bucket["unreachable"] += 1
        return stats_by_slug, site_by_slug

    def _ensure_discovered_stands(
        self, stats_by_slug: dict[str, dict[str, int]], site_by_slug: dict[str, str]
    ) -> list[Stand]:
        existing = {
            st.slug: st for st in self._session.scalars(select(Stand).order_by(Stand.id)).all()
        }
        default_slug = self._default_slug()
        rt = get_state_manager(self._settings).get_db_state(
            "runtime_settings", {}, database_url=self._settings.database_url
        )
        rt_dict = rt if isinstance(rt, dict) else {}

        changed = False
        slugs_to_ensure = list(dict.fromkeys([default_slug, *stats_by_slug.keys()]))
        for slug in slugs_to_ensure:
            if not slug or slug in existing:
                continue
            is_def = slug == default_slug
            raw_pw = (
                (
                    rt_dict.get("clab_password")
                    or (
                        self._settings.clab_password.get_secret_value()
                        if self._settings.clab_password
                        else None
                    )
                )
                if is_def
                else None
            )
            enc_pw = encrypt_secret(str(raw_pw)) if raw_pw else None
            pmap = rt_dict.get("clab_port_map") if is_def else None
            pmap_json = (
                json.dumps(pmap, ensure_ascii=False)
                if isinstance(pmap, dict) and pmap
                else None
            )
            stand = Stand(
                slug=slug,
                name=f"Основной ЦОД ({slug})" if is_def else f"Стенд {slug}",
                site=site_by_slug.get(slug) or slug,
                driver=str(
                    rt_dict.get("network_driver") or self._settings.network_driver or "scrapli"
                ),
                jump_host=(
                    str(rt_dict.get("clab_host") or self._settings.clab_host)
                    if is_def and (rt_dict.get("clab_host") or self._settings.clab_host)
                    else None
                ),
                jump_port=(
                    int(rt_dict.get("clab_port") or self._settings.clab_port or 221)
                    if is_def
                    else 22
                ),
                jump_user=(
                    str(rt_dict.get("clab_user") or self._settings.clab_user or "crocdream")
                    if is_def
                    else "crocdream"
                ),
                encrypted_password=enc_pw,
                port_map_json=pmap_json,
                topology_path=(
                    str(rt_dict.get("clab_topology_path") or self._settings.clab_topology_path)
                    if is_def
                    and (rt_dict.get("clab_topology_path") or self._settings.clab_topology_path)
                    else None
                ),
                queue_name="default" if is_def else f"stand:{slug}",
                status="ONLINE",
            )
            self._session.add(stand)
            existing[slug] = stand
            changed = True

        if changed:
            try:
                self._session.commit()
                for st in existing.values():
                    self._session.refresh(st)
            except Exception:
                self._session.rollback()
                stands = list(self._session.scalars(select(Stand)).all())
                existing = {st.slug: st for st in stands}
            self._sync_registry_cache(list(existing.values()))
        return list(existing.values())

    def list_stands(self, allowed_system: str | None = None) -> list[dict[str, Any]]:
        from netops.api.routes.devices import stand_allowed

        stats_by_slug, site_by_slug = self._compute_device_stats()
        stands = self._ensure_discovered_stands(stats_by_slug, site_by_slug)
        default_slug = self._default_slug()
        results: list[dict[str, Any]] = []
        for st in stands:
            if not stand_allowed(st.slug, st.site, allowed_system):
                continue
            results.append(
                _serialize_stand_row(st, stats_by_slug.get(st.slug), default_slug=default_slug)
            )
        return results

    def get_stand(self, slug: str) -> Stand:
        clean_slug = slug.strip()
        stand = self._session.scalar(select(Stand).where(Stand.slug == clean_slug))
        if stand is None:
            stats_by_slug, site_by_slug = self._compute_device_stats(fabric_id=clean_slug)
            self._ensure_discovered_stands(stats_by_slug, site_by_slug)
            stand = self._session.scalar(select(Stand).where(Stand.slug == clean_slug))
        if stand is None:
            raise NotFoundError(f"Stand {clean_slug!r} not found")
        return stand

    def create_or_update_stand(
        self,
        *,
        slug: str,
        name: str | None = None,
        site: str | None = None,
        driver: str | None = None,
        jump_host: str | None = None,
        jump_port: int | None = None,
        jump_user: str | None = None,
        password: str | None = None,
        port_map: dict[str, str] | str | None = None,
        topology_path: str | None = None,
        intent_subpath: str | None = None,
        queue_name: str | None = None,
        status: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        clean_slug = slug.strip()
        if not clean_slug:
            raise ValueError("Stand slug must not be empty")

        stand = self._session.scalar(select(Stand).where(Stand.slug == clean_slug))
        is_new = stand is None
        if stand is None:
            stand = Stand(
                slug=clean_slug,
                name=(name or clean_slug).strip(),
                site=(site or clean_slug).strip(),
                driver=(driver or "scrapli").strip(),
                jump_host=jump_host.strip() if jump_host else None,
                jump_port=int(jump_port) if jump_port else 22,
                jump_user=jump_user.strip() if jump_user else "crocdream",
                queue_name=(queue_name or "default").strip(),
                status=(status or "ONLINE").strip(),
            )
            self._session.add(stand)
        else:
            if name is not None:
                stand.name = name.strip()
            if site is not None:
                stand.site = site.strip() or None
            if driver is not None:
                stand.driver = driver.strip()
            if jump_host is not None:
                stand.jump_host = jump_host.strip() or None
            if jump_port is not None:
                stand.jump_port = int(jump_port)
            if jump_user is not None:
                stand.jump_user = jump_user.strip() or None
            if queue_name is not None:
                stand.queue_name = queue_name.strip() or "default"
            if status is not None:
                stand.status = status.strip()

        if password is not None and password.strip():
            stand.encrypted_password = encrypt_secret(password.strip())
        if port_map is not None:
            parsed_map = _parse_port_map(port_map)
            stand.port_map_json = (
                json.dumps(parsed_map, ensure_ascii=False) if parsed_map else None
            )
        if topology_path is not None:
            stand.topology_path = topology_path.strip() or None
        if intent_subpath is not None:
            stand.intent_subpath = intent_subpath.strip() or None
        if description is not None:
            stand.description = description.strip() or None
        if is_new and not stand.name:
            stand.name = clean_slug

        self._session.commit()
        self._session.refresh(stand)
        self._sync_registry_cache()

        stats_by_slug, _ = self._compute_device_stats(fabric_id=stand.slug)
        return _serialize_stand_row(
            stand, stats_by_slug.get(stand.slug), default_slug=self._default_slug()
        )

    def delete_stand(self, slug: str, *, force: bool = False) -> None:
        import shutil
        from pathlib import Path

        from netops.services.devices import DeviceService
        from netops.services.discovery import remove_device_from_sot
        from netops.services.discovery_profiles import _is_protected_repo_path

        clean_slug = slug.strip()
        stand = self._session.scalar(select(Stand).where(Stand.slug == clean_slug))
        if stand is None:
            raise NotFoundError(f"Stand {clean_slug!r} not found")
        stats_by_slug, _ = self._compute_device_stats(fabric_id=clean_slug)
        active_devices = (stats_by_slug.get(clean_slug) or {}).get("total_devices", 0)
        default_slug = self._default_slug()
        is_def = clean_slug == default_slug
        if not force and is_def and active_devices > 0:
            raise ConflictError(
                f"Cannot delete default stand {clean_slug!r} while it has {active_devices} active devices"
            )
        if not is_def:
            dev_svc = DeviceService(self._session)
            stand_devs = list(
                self._session.scalars(
                    select(Device)
                    .where((Device.fabric_id == clean_slug) | (Device.site == clean_slug))
                    .limit(5000)
                ).all()
            )
            for dev in stand_devs:
                hname = dev.hostname
                if dev.id is not None:
                    dev_svc.delete(dev.id)
                remove_device_from_sot(
                    hname,
                    self._settings.intent_repo_path,
                    self._settings.offline_lab_path,
                )
            repo_path = Path(self._settings.intent_repo_path)
            if not _is_protected_repo_path(repo_path):
                subpath = stand.intent_subpath or f"stands/{clean_slug}"
                stand_dir = (repo_path / subpath).resolve()
                stands_root = (repo_path / "stands").resolve()
                if stand_dir.is_dir() and stand_dir.is_relative_to(stands_root) and stand_dir != stands_root:
                    shutil.rmtree(stand_dir, ignore_errors=True)
        self._session.delete(stand)
        self._session.commit()
        state = get_state_manager(self._settings)
        rt = state.get_db_state("runtime_settings", {}, database_url=self._settings.database_url)
        if isinstance(rt, dict) and rt.get("active_stand_slug") == clean_slug:
            rt_copy = dict(rt)
            rt_copy["active_stand_slug"] = default_slug
            state.set_db_state(
                "runtime_settings", rt_copy, database_url=self._settings.database_url
            )
        self._sync_registry_cache()
