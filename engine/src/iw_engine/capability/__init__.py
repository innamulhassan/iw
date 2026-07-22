"""Capability layer — governed, mockable adapters (fetch -> raw ; normalize -> ops)."""
from .layer import (
    Adapter,
    CapabilityCall,
    CapabilityLayer,
    Invocation,
    McpSource,
    MockSource,
    RestSource,
    RoutedSource,
    ScenarioSource,
    Source,
)
from .mapping import MappingSource, map_response
from .registry import CapabilityRegistry, CapabilitySpec, Policy
from .sources import (
    ProviderRoutedSource,
    build_provider_transports,
    provider_config,
)

__all__ = [
    "Adapter",
    "CapabilityCall",
    "CapabilityLayer",
    "CapabilityRegistry",
    "CapabilitySpec",
    "Invocation",
    "MappingSource",
    "McpSource",
    "MockSource",
    "Policy",
    "ProviderRoutedSource",
    "RestSource",
    "RoutedSource",
    "ScenarioSource",
    "Source",
    "build_provider_transports",
    "map_response",
    "provider_config",
]
