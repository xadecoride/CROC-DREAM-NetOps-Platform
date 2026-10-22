from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from netops.enums import Platform
from netops.intent.lint import (
    IntentIssue,
    IntentValidationError,
    issues_from_validation_error,
    lint_fabric,
    lint_platforms,
)
from netops.intent.models import DeviceIntent, DiffFilters, Inventory, PlatformProfile

INVENTORY_FILE = "inventory.yaml"
DEVICES_DIR = "devices"
PLATFORMS_DIR = "platforms"
DIFF_FILTERS_FILE = "diff_filters.yaml"
_YAML_SUFFIXES = (".yaml", ".yml")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPO_PLATFORMS_DIR = _REPO_ROOT / "intent" / "platforms"
_DEFAULT_PLATFORMS_DIR = (
    Path("/data/intent/platforms")
    if Path("/data/intent/platforms").is_dir()
    else _REPO_PLATFORMS_DIR
)
_DEFAULT_DIFF_FILTERS_PATH = Path(__file__).resolve().parents[1] / "network" / "diff_filters.yaml"

ModelT = TypeVar("ModelT", bound=BaseModel)


def _issue_belongs_to_stand(issue: IntentIssue, hostnames: set[str]) -> bool:
    h = issue.hostname
    if not h and issue.source.startswith(f"{DEVICES_DIR}/"):
        h = Path(issue.source).stem
    if not h:
        return True
    return h in hostnames


@dataclass(frozen=True)
class IntentSnapshot:
    inventory: Inventory
    intents: Mapping[str, DeviceIntent] = field(default_factory=dict)
    platform_profiles: Mapping[str, PlatformProfile] = field(default_factory=dict)
    diff_filters: DiffFilters | None = None

    def intent_for(self, hostname: str) -> DeviceIntent | None:
        return self.intents.get(hostname)

    def platform_profile(self, platform: str | Platform) -> PlatformProfile | None:
        plat_key = getattr(platform, "value", str(platform)).lower()
        return self.platform_profiles.get(plat_key)


class IntentRepository:
    def __init__(self, root: Path, inventory_file: str = INVENTORY_FILE) -> None:
        self._root = root
        self._inventory_file = inventory_file
        self._platform_cache: dict[str, PlatformProfile] = {}
        self._diff_filters_cache: DiffFilters | None = None
        self._stand_hosts: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def inventory_file(self) -> str:
        return self._inventory_file

    def get_platform_profile(self, platform: str | Platform) -> PlatformProfile:
        plat_key = getattr(platform, "value", str(platform)).lower()
        if plat_key in self._platform_cache:
            return self._platform_cache[plat_key]
        pdir = self._platforms_dir()
        if pdir is not None and pdir.is_dir():
            for suffix in _YAML_SUFFIXES:
                cand = pdir / f"{plat_key}{suffix}"
                if cand.is_file():
                    raw = yaml.safe_load(cand.read_text(encoding="utf-8"))
                    profile = PlatformProfile.model_validate(raw)
                    self._platform_cache[plat_key] = profile
                    return profile
        raise KeyError(f"Platform profile '{plat_key}' not found in {pdir}")

    def load_platform_profiles(self) -> dict[str, PlatformProfile]:
        issues: list[IntentIssue] = []
        profiles = self._collect_platform_profiles(issues)
        if issues:
            raise IntentValidationError(issues)
        return profiles

    def load_diff_filters(self, path: Path | None = None) -> DiffFilters:
        if path is None and self._diff_filters_cache is not None:
            return self._diff_filters_cache
        target_path = path or self._diff_filters_file()
        if target_path is None or not target_path.is_file():
            raise FileNotFoundError("diff_filters.yaml not found")
        raw = yaml.safe_load(target_path.read_text(encoding="utf-8"))
        filters = DiffFilters.model_validate(raw)
        if path is None:
            self._diff_filters_cache = filters
        return filters

    def load(self) -> IntentSnapshot:
        inventory, intents, issues = self._collect()
        if issues or inventory is None:
            raise IntentValidationError(issues)
        return IntentSnapshot(
            inventory=inventory,
            intents=intents,
            platform_profiles=dict(self._platform_cache),
            diff_filters=self._diff_filters_cache,
        )


    def lint(self, fabric_id: str | None = None) -> list[IntentIssue]:
        return self._collect(fabric_id=fabric_id)[2]

    def load_inventory(self) -> Inventory:
        issues: list[IntentIssue] = []
        inventory = self._parse_inventory(issues)
        if inventory is None:
            raise IntentValidationError(issues)
        return inventory

    def load_device_intent(self, hostname: str) -> DeviceIntent | None:
        path = self._device_file(hostname)
        if path is None:
            return None
        issues: list[IntentIssue] = []
        intent = self._parse_file(path, DeviceIntent, issues)
        if intent is None:
            raise IntentValidationError(issues)
        return intent

    def _collect_device_intents(self, issues: list[IntentIssue]) -> dict[str, DeviceIntent]:
        intents: dict[str, DeviceIntent] = {}
        for path in self._device_files():
            intent = self._parse_file(path, DeviceIntent, issues)
            if intent is None:
                continue
            source = self._relative(path)
            if intent.hostname != path.stem:
                issues.append(
                    IntentIssue(
                        source,
                        "hostname",
                        f"Hostname {intent.hostname!r} does not match the file name",
                        intent.hostname,
                        "hostname_mismatch",
                    )
                )
            elif intent.hostname in intents:
                issues.append(
                    IntentIssue(
                        source,
                        "hostname",
                        "Device intent is declared in more than one file",
                        intent.hostname,
                        "duplicate_hostname",
                    )
                )
            else:
                intents[intent.hostname] = intent
        return intents

    def _collect(
        self, fabric_id: str | None = None
    ) -> tuple[Inventory | None, dict[str, DeviceIntent], list[IntentIssue]]:
        issues: list[IntentIssue] = []
        if not self._root.is_dir():
            issues.append(IntentIssue(".", "", f"Intent repository {self._root} does not exist"))
            return None, {}, issues

        inventory = self._parse_inventory(issues)
        intents = self._collect_device_intents(issues)
        platform_profiles = self._collect_platform_profiles(issues)
        if inventory is not None:
            issues.extend(
                lint_platforms(
                    platform_profiles,
                    inventory,
                    inventory_source=self._inventory_file,
                )
            )

        df_path = self._diff_filters_file()
        if df_path is not None and df_path.is_file():
            try:
                df_path.resolve().relative_to(self._root.resolve())
                is_local = True
            except ValueError:
                is_local = False
            if is_local:
                diff_filters = self._parse_file(df_path, DiffFilters, issues)
                if diff_filters is not None:
                    self._diff_filters_cache = diff_filters

        device_fabrics: dict[str, str] = {}

        if inventory is not None:
            device_fabrics = {
                device.hostname: (
                    getattr(device, "fabric_id", None)
                    or getattr(device, "site", None)
                    or "croc-dc1-pod1"
                )
                for device in inventory.devices
            }
            known = {device.hostname for device in inventory.devices}
            if self._inventory_file == "inventory.yaml":
                issues.extend(
                    IntentIssue(
                        f"{DEVICES_DIR}/{hostname}.yaml",
                        "hostname",
                        "Device is not declared in the inventory",
                        hostname,
                        "unknown_device",
                    )
                    for hostname in sorted(intents.keys() - known - self._stand_hosts)
                )

        if fabric_id is not None:
            scoped_hostnames = (
                {
                    d.hostname
                    for d in inventory.devices
                    if (
                        getattr(d, "fabric_id", None) == fabric_id
                        or getattr(d, "site", None) == fabric_id
                    )
                }
                if inventory is not None
                else set()
            )
            issues = [i for i in issues if _issue_belongs_to_stand(i, scoped_hostnames)]
            filtered_intents = [v for k, v in intents.items() if k in scoped_hostnames]
            issues.extend(lint_fabric(filtered_intents, fabric_id=fabric_id))
        else:
            fabrics_map: defaultdict[str, list[DeviceIntent]] = defaultdict(list)
            for hostname, intent in intents.items():
                fab = device_fabrics.get(hostname, "croc-dc1-pod1")
                fabrics_map[fab].append(intent)
            for fab, fab_intents in sorted(fabrics_map.items()):
                issues.extend(lint_fabric(fab_intents, fabric_id=fab))

        return inventory, intents, issues

    def _parse_inventory(self, issues: list[IntentIssue]) -> Inventory | None:
        path = self._root / self._inventory_file
        if not path.is_file():
            issues.append(IntentIssue(self._inventory_file, "", "Inventory file is missing"))
            return None
        inv = self._parse_file(path, Inventory, issues)
        if inv is None or self._inventory_file != INVENTORY_FILE:
            return inv
        stand_dirs = self._stand_device_dirs()
        if not stand_dirs:
            return inv
        by_host = {d.hostname: d for d in inv.devices}
        for sdir in stand_dirs:
            sinv_path = sdir.parent / INVENTORY_FILE
            if not sinv_path.is_file():
                continue
            sinv = self._parse_file(sinv_path, Inventory, [])
            if sinv is None:
                continue
            for sd in sinv.devices:
                base = by_host.get(sd.hostname)
                if base is not None:
                    merged = base.model_dump()
                    for k, v in sd.model_dump(exclude_none=True).items():
                        merged[k] = v
                    by_host[sd.hostname] = base.model_validate(merged)
                else:
                    by_host[sd.hostname] = sd
        return Inventory(devices=list(by_host.values()))

    def _parse_file(
        self, path: Path, model: type[ModelT], issues: list[IntentIssue]
    ) -> ModelT | None:
        source = self._relative(path)
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            issues.append(IntentIssue(source, "", f"Cannot read YAML: {exc}", code="yaml_error"))
            return None
        if data is None:
            issues.append(IntentIssue(source, "", "File is empty", code="empty_file"))
            return None
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            hostname = data.get("hostname") if isinstance(data, dict) else None
            issues.extend(
                issues_from_validation_error(
                    exc, source=source, hostname=hostname if isinstance(hostname, str) else None
                )
            )
            return None

    def _stand_device_dirs(self) -> list[Path]:
        """Stand overlay `stands/<slug>/devices` (live discovery); off for the tracked repo under pytest."""
        stands = self._root / "stands"
        if not stands.is_dir():
            return []
        if "PYTEST_CURRENT_TEST" in os.environ:
            try:
                if self._root.resolve().is_relative_to(_REPO_ROOT.resolve()):
                    return []
            except Exception:
                return []
        return sorted(d / DEVICES_DIR for d in stands.iterdir() if (d / DEVICES_DIR).is_dir())

    def _device_files(self) -> list[Path]:
        directory = self._root / DEVICES_DIR
        files = (
            {p.stem: p for p in directory.iterdir() if p.is_file() and p.suffix in _YAML_SUFFIXES}
            if directory.is_dir()
            else {}
        )
        root_stems = set(files)
        self._stand_hosts = set()
        for sdir in self._stand_device_dirs():
            for p in sdir.iterdir():
                if p.is_file() and p.suffix in _YAML_SUFFIXES:
                    files[p.stem] = p
                    if p.stem not in root_stems:
                        self._stand_hosts.add(p.stem)
        return sorted(files.values())

    def _device_file(self, hostname: str) -> Path | None:
        dirs = [
            *(d.resolve() for d in self._stand_device_dirs()),
            (self._root / DEVICES_DIR).resolve(),
        ]
        for directory in dirs:
            for suffix in _YAML_SUFFIXES:
                path = (directory / f"{hostname}{suffix}").resolve()
                if path.parent == directory and path.is_file():
                    return path
        return None

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self._root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def _platforms_dir(self) -> Path | None:
        cand = self._root / PLATFORMS_DIR
        if cand.is_dir():
            return cand
        cand_nested = self._root / "intent" / PLATFORMS_DIR
        if cand_nested.is_dir():
            return cand_nested
        if _DEFAULT_PLATFORMS_DIR.is_dir():
            return _DEFAULT_PLATFORMS_DIR
        return None

    def _diff_filters_file(self) -> Path | None:
        for cand in (
            self._root / DIFF_FILTERS_FILE,
            self._root / "network" / DIFF_FILTERS_FILE,
            _DEFAULT_DIFF_FILTERS_PATH,
        ):
            if cand.is_file():
                return cand
        return None

    def _collect_platform_profiles(
        self, issues: list[IntentIssue]
    ) -> dict[str, PlatformProfile]:
        profiles: dict[str, PlatformProfile] = {}
        pdir = self._platforms_dir()
        if pdir is None or not pdir.is_dir():
            return profiles
        for path in sorted(pdir.iterdir()):
            if path.is_file() and path.suffix in _YAML_SUFFIXES:
                profile = self._parse_file(path, PlatformProfile, issues)
                if profile is not None:
                    plat_key = str(getattr(profile.platform, "value", profile.platform)).lower()
                    profiles[plat_key] = profile
                    self._platform_cache[plat_key] = profile
        return profiles

