"""The 9 read/telemetry tool adapters wired into `ALL_ADAPTERS`. Each is a class with
`provider`, `intents`, `effect`, `normalize`. `default_adapters()` wires the full set; the
CapabilityLayer resolves an intent to its adapter. (A 10th, `RemediationAdapter`, is the
reserved A2A write-side adapter — deliberately NOT in the default catalogue; it degrades
clean-empty until A2A is wired, so it is not counted among the live tool set here.)
"""
from .appd import AppDAdapter
from .artifactory import ArtifactoryAdapter
from .bigpanda import BigPandaAdapter
from .cmdb import CmdbAdapter
from .git import GitAdapter
from .ocp import OcpAdapter
from .prometheus import PrometheusAdapter
from .servicenow import ServiceNowAdapter
from .splunk import SplunkAdapter

ALL_ADAPTERS = [
    PrometheusAdapter, SplunkAdapter, AppDAdapter, ServiceNowAdapter,
    CmdbAdapter, OcpAdapter, ArtifactoryAdapter, GitAdapter, BigPandaAdapter,
]


def default_adapters() -> list:
    return [cls() for cls in ALL_ADAPTERS]


__all__ = [
    "ALL_ADAPTERS",
    "AppDAdapter",
    "ArtifactoryAdapter",
    "BigPandaAdapter",
    "CmdbAdapter",
    "GitAdapter",
    "OcpAdapter",
    "PrometheusAdapter",
    "ServiceNowAdapter",
    "SplunkAdapter",
    "default_adapters",
]
