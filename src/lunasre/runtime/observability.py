"""Observability (L8) — OpenTelemetry tracing for agent actions.

Every MCP tool call, A2A delegation, and IC run is wrapped in an OTel span, so an
incident investigation shows up as one trace (with tokens/latency/attributes).

Exporter is configurable (the L8 "abstract through your own interface" point):
- default: ConsoleSpanExporter (visible in logs; zero dependency).
- LUNASRE_OTLP_ENDPOINT set: OTLPSpanExporter → Arize Phoenix / Datadog / Grafana.
- tests: install_memory_exporter() captures spans in-process for assertions.

This is the OpenTelemetry GenAI direction; Phoenix (Docker, OTLP receiver) is the
documented production exporter swap — one env var, no code change.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_PROVIDER: TracerProvider | None = None
_MEMORY_EXPORTER: InMemorySpanExporter | None = None


def _provider() -> TracerProvider:
    """Lazily build the tracer provider with the configured exporter."""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    provider = TracerProvider(resource=Resource.create({"service.name": "lunasre"}))
    endpoint = os.environ.get("LUNASRE_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    elif os.environ.get("LUNASRE_TRACE_CONSOLE") == "1":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    _PROVIDER = provider
    trace.set_tracer_provider(provider)
    return provider


def tracer():
    return trace.get_tracer("lunasre", tracer_provider=_provider())


@contextmanager
def span(name: str, **attrs: Any):
    """Context manager that opens an OTel span with attributes.

    Usage:
        with span("mcp.tool_call", tool="grep", agent="dbops-agent"):
            ...
    """
    with tracer().start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(f"lunasre.{k}", v)
        yield sp


def install_memory_exporter() -> InMemorySpanExporter:
    """For tests: route spans to an in-memory exporter and return it.

    Resets the provider so spans are captured fresh.
    """
    global _PROVIDER, _MEMORY_EXPORTER
    _MEMORY_EXPORTER = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "lunasre-test"}))
    provider.add_span_processor(SimpleSpanProcessor(_MEMORY_EXPORTER))
    _PROVIDER = provider
    trace.set_tracer_provider(provider)
    return _MEMORY_EXPORTER
