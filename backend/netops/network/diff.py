from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from hier_config import HConfig, WorkflowRemediation, get_hconfig
from hier_config import Platform as HierPlatform

from netops.enums import Platform
from netops.network.base import ConfigDiff

HIER_CONFIG_PLATFORMS: Mapping[Platform, HierPlatform] = MappingProxyType(
    {
        Platform.CISCO_IOSXE: HierPlatform.CISCO_IOS,
        Platform.ARISTA_EOS: HierPlatform.ARISTA_EOS,
        Platform.HUAWEI_VRP: HierPlatform.HUAWEI_VRP,
        Platform.JUNIPER_JUNOS: HierPlatform.JUNIPER_JUNOS,
        Platform.ELTEX_ESR: HierPlatform.CISCO_IOS,
        Platform.ELTEX_MES: HierPlatform.CISCO_IOS,
        Platform.YADRO_KORNFE: HierPlatform.GENERIC,
        Platform.LINUX_SERVER: HierPlatform.GENERIC,
    }
)


class HierConfigDiffEngine:
    def compare(self, platform: Platform, running: str, intended: str) -> ConfigDiff:
        hier_platform = HIER_CONFIG_PLATFORMS[platform]
        running_config = get_hconfig(hier_platform, running)
        intended_config = get_hconfig(hier_platform, intended)
        workflow = WorkflowRemediation(running_config, intended_config)
        return ConfigDiff(
            remediation=_as_patch(workflow.remediation_config),
            rollback=_as_patch(workflow.rollback_config),
            unauthorized_lines=tuple(running_config.difference(intended_config).dump_simple()),
            missing_lines=tuple(intended_config.difference(running_config).dump_simple()),
        )


def _as_patch(config: HConfig) -> str:
    # exit после каждой секции, чтобы патч можно было вставлять из любого режима.
    lines = config.dump_simple(sectional_exiting=True)
    return "\n".join(lines) + "\n" if lines else ""
