from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from netops.errors import PipelineError
from netops.intent.models import InventoryDevice
from netops.intent.repository import IntentRepository
from netops.network.base import (
    ConfigCollector,
    ConfigDeployer,
    ConfigRenderer,
    Credentials,
    DeviceTarget,
    DiffEngine,
    HealthProbe,
)
from netops.network.diff import HierConfigDiffEngine
from netops.network.normalization import ConfigNormalizer
from netops.network.offline import OfflineLab
from netops.network.rendering import JinjaConfigRenderer
from netops.settings import Settings


@dataclass(frozen=True)
class Toolchain:
    intents: IntentRepository
    renderer: ConfigRenderer
    normalizer: ConfigNormalizer
    diff_engine: DiffEngine
    collector: ConfigCollector
    deployer: ConfigDeployer
    health_probe: HealthProbe
    credentials: Mapping[str, Credentials]
    confirm_timeout_seconds: int = 180
    max_ping_loss_percent: float = 20.0
    post_check_attempts: int = 1
    post_check_interval_seconds: float = 0.0

    def target_for(self, device: InventoryDevice) -> DeviceTarget:
        credentials = self.credentials.get(device.auth_profile)
        if credentials is None:
            raise PipelineError(f"Auth profile {device.auth_profile!r} is not configured")
        return DeviceTarget(
            hostname=device.hostname,
            platform=device.platform,
            host=str(device.management_ip),
            port=device.management_port,
            credentials=credentials,
        )


def build_toolchain(settings: Settings) -> Toolchain:
    # Драйвер Scrapli/Nornir регистрируется здесь под своим значением NETOPS_NETWORK_DRIVER.
    match settings.network_driver:
        case "offline":
            lab = OfflineLab(settings.offline_lab_path)
            collector: ConfigCollector = lab
            deployer: ConfigDeployer = lab
            health_probe: HealthProbe = lab
        case "scrapli":
            from netops.network.scrapli_driver import ScrapliNetworkDriver  # noqa: PLC0415

            driver = ScrapliNetworkDriver()
            collector = driver
            deployer = driver
            health_probe = driver

    return Toolchain(
        intents=IntentRepository(settings.intent_repo_path),
        renderer=JinjaConfigRenderer(settings.templates_path),
        normalizer=ConfigNormalizer.from_file(settings.normalization_rules_path),
        diff_engine=HierConfigDiffEngine(),
        collector=collector,
        deployer=deployer,
        health_probe=health_probe,
        credentials={
            name: Credentials(profile.username, profile.password.get_secret_value())
            for name, profile in settings.auth_profiles.items()
        },
        confirm_timeout_seconds=settings.commit_confirm_timeout_seconds,
        max_ping_loss_percent=settings.max_ping_loss_percent,
        post_check_attempts=settings.post_check_attempts,
        post_check_interval_seconds=settings.post_check_interval_seconds,
    )
