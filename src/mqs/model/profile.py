"""Top-level configuration profile."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mqs.model.bridge import BridgeConfig, LanConfig
from mqs.model.firewall import FirewallConfig
from mqs.model.services import ServicesConfig
from mqs.model.wan import WanConfig


class Mode(StrEnum):
    ROUTER = "router"
    BRIDGE_AP = "bridge-ap"
    CUSTOM = "custom"


class DeviceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str = ""
    identity: str = "MikroTik"
    mode: Mode = Mode.CUSTOM
    # Port names keyed by default-name; see mqs.model.ports.
    interface_names: dict[str, str] = Field(default_factory=dict)
    wan: WanConfig = Field(default_factory=WanConfig)
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    lan: LanConfig = Field(default_factory=LanConfig)
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
