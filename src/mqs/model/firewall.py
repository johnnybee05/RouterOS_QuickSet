"""Firewall and FastTrack configuration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FastTrackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Requested only; emitted solely when the target version and hardware support it.
    hw_offload: bool = False
    # The user answered "no traffic shaping (simple queues / HTB) is in use".
    no_shaping_confirmed: bool = False


class FirewallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basic_filter: bool = True
    fasttrack: FastTrackConfig = Field(default_factory=FastTrackConfig)
