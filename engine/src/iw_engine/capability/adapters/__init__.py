"""The 8 tool adapters. Each is a class with `provider`, `intents`, `effect`, `normalize`.
`default_adapters()` wires the full set; the CapabilityLayer resolves an intent to its
adapter.
"""
from .appd import AppDAdapter
from .artifactory import ArtifactoryAdapter
from .cmdb import CmdbAdapter
from .git import GitAdapter
from .ocp import OcpAdapter
from .prometheus import PrometheusAdapter
from .servicenow import ServiceNowAdapter
from .splunk import SplunkAdapter

ALL_ADAPTERS = [
    PrometheusAdapter, SplunkAdapter, AppDAdapter, ServiceNowAdapter,
    CmdbAdapter, OcpAdapter, ArtifactoryAdapter, GitAdapter,
]


def default_adapters() -> list:
    return [cls() for cls in ALL_ADAPTERS]


__all__ = [
    "ALL_ADAPTERS",
    "AppDAdapter",
    "ArtifactoryAdapter",
    "CmdbAdapter",
    "GitAdapter",
    "OcpAdapter",
    "PrometheusAdapter",
    "ServiceNowAdapter",
    "SplunkAdapter",
    "default_adapters",
]
