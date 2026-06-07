"""Service registry — the distributed-system topology.

Each service is a YAML file under `.devagent/registry/services/`. The schema is designed for
50+ services from day one (ownership, SLAs, produced/consumed APIs, events, databases) even
when only one is present. Used for cross-service dependency lookups and (in V2) service-level
blast radius."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REGISTRY_DIR = ".devagent/registry/services"

SAMPLE_SERVICE = """\
name: checkout-service
team: payments
tech_stack: [python, fastapi, postgresql]
sla_tier: critical              # critical | high | standard
compliance_zones: [pci-dss]
apis:
  produces:
    - spec: ./openapi.yaml
      version: "1.0.0"
  consumes:
    - service: inventory-service
      version_pin: ">=1.4.0,<2.0.0"
events:
  produces: [order.created]
  consumes: [payment.confirmed]
databases:
  owned: [orders, order_items]
"""


@dataclass
class Service:
    name: str
    team: str = ""
    sla_tier: str = "standard"
    root: str = ""                    # repo-relative dir this service owns (for file→service mapping)
    produces_specs: list[str] = field(default_factory=list)  # produced OpenAPI spec paths
    tech_stack: list[str] = field(default_factory=list)
    compliance_zones: list[str] = field(default_factory=list)
    consumes: list[dict] = field(default_factory=list)        # [{service, version_pin}]
    events_produces: list[str] = field(default_factory=list)
    events_consumes: list[str] = field(default_factory=list)
    dbs_owned: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def consumes_names(self) -> list[str]:
        return [c.get("service", "") for c in self.consumes if c.get("service")]


def _parse(data: dict) -> Service:
    apis = data.get("apis", {}) or {}
    events = data.get("events", {}) or {}
    dbs = data.get("databases", {}) or {}
    produces_specs = [str(p.get("spec")) for p in (apis.get("produces", []) or [])
                      if isinstance(p, dict) and p.get("spec")]
    return Service(
        name=str(data.get("name", "?")),
        team=str(data.get("team", "")),
        sla_tier=str(data.get("sla_tier", "standard")),
        root=str(data.get("root", "")),
        produces_specs=produces_specs,
        tech_stack=list(data.get("tech_stack", []) or []),
        compliance_zones=list(data.get("compliance_zones", []) or []),
        consumes=list(apis.get("consumes", []) or []),
        events_produces=list(events.get("produces", []) or []),
        events_consumes=list(events.get("consumes", []) or []),
        dbs_owned=list(dbs.get("owned", []) or []),
        raw=data,
    )


def registry_dir(root: Path) -> Path:
    return root / REGISTRY_DIR


def load_services(root: Path) -> dict[str, Service]:
    d = registry_dir(root)
    if not d.exists():
        return {}
    services: dict[str, Service] = {}
    for p in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("name"):
            svc = _parse(data)
            services[svc.name] = svc
    return services


def write_sample(root: Path) -> Path:
    d = registry_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "checkout-service.yaml"
    if not p.exists():
        p.write_text(SAMPLE_SERVICE, encoding="utf-8")
    return p


def downstream_consumers(services: dict[str, Service], name: str) -> list[str]:
    """Services that consume `name` — i.e. who breaks if `name` changes its API."""
    return sorted(s.name for s in services.values() if name in s.consumes_names)
