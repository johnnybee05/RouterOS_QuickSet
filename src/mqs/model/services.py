"""Services / hardening configuration."""

from __future__ import annotations

from importlib.resources import files
from ipaddress import IPv4Network

from pydantic import BaseModel, ConfigDict, field_validator


def _load_default() -> str:
    resource = files("mqs.model").joinpath("resources/hardening_default_v7.rsc")
    return resource.read_text(encoding="utf-8").replace("\r\n", "\n")


DEFAULT_HARDENING_V7: str = _load_default()


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Raw RouterOS script (v7 dialect). Single source of truth for DNS, SSH port,
    # SNMP community, NTP servers and timezone; form fields are bound to its lines.
    hardening_text: str = DEFAULT_HARDENING_V7
    # Use `find` by topics instead of numeric indexes in /system logging.
    robust_logging: bool = False
    # /ip dns allow-remote-requests: the router answers DNS for the LAN, and the DHCP server
    # hands out the bridge address instead of the DNS servers above. Takes effect only with
    # the basic filter, which keeps the resolver closed to WAN.
    dns_remote_requests: bool = False
    # Optional extensions (default off, so the company standard never changes silently).
    api_ssl_certificate: str | None = None
    management_subnet: IPv4Network | None = None

    @field_validator("hardening_text")
    @classmethod
    def _normalise_newlines(cls, value: str) -> str:
        return value.replace("\r\n", "\n")

    @property
    def hardening_is_default(self) -> bool:
        return self.hardening_text.strip() == DEFAULT_HARDENING_V7.strip()

    def reset_hardening(self) -> ServicesConfig:
        return self.model_copy(update={"hardening_text": DEFAULT_HARDENING_V7})
