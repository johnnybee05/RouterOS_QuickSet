from ipaddress import IPv4Address, IPv4Network
from types import SimpleNamespace

from mqs.transport.netifs import (
    LocalInterface,
    interface_for,
    list_ipv4_interfaces,
    propose_free_address,
)


def fake_adapters():
    return [
        SimpleNamespace(name="{GUID-1}", nice_name="Ethernet", index=7, ips=[
            SimpleNamespace(ip="192.168.88.254", network_prefix=24),
            SimpleNamespace(ip=("fe80::1", 0, 7), network_prefix=64),
        ]),
        SimpleNamespace(name="lo", nice_name="", index=1, ips=[
            SimpleNamespace(ip="127.0.0.1", network_prefix=8),
        ]),
        SimpleNamespace(name="wifi0", nice_name="", index=None, ips=[
            SimpleNamespace(ip="169.254.10.20", network_prefix=16),
        ]),
    ]


def test_list_ipv4_interfaces():
    interfaces = list_ipv4_interfaces(fake_adapters)
    assert interfaces == [
        LocalInterface("Ethernet", IPv4Address("192.168.88.254"),
                       IPv4Network("192.168.88.0/24"), 7),
        LocalInterface("wifi0", IPv4Address("169.254.10.20"), IPv4Network("169.254.0.0/16"), None),
    ]
    assert interfaces[0].broadcast == IPv4Address("192.168.88.255")


def test_interface_for():
    interfaces = list_ipv4_interfaces(fake_adapters)
    assert interface_for(IPv4Address("192.168.88.1"), interfaces).name == "Ethernet"
    assert interface_for(IPv4Address("10.0.0.1"), interfaces) is None


def test_propose_free_address():
    iface = LocalInterface("Ethernet", IPv4Address("192.168.88.254"),
                           IPv4Network("192.168.88.0/24"))
    assert propose_free_address(iface) == IPv4Address("192.168.88.253")
    assert propose_free_address(iface, [IPv4Address("192.168.88.253")]) == \
        IPv4Address("192.168.88.252")
    tiny = LocalInterface("p2p", IPv4Address("10.0.0.1"), IPv4Network("10.0.0.0/30"))
    assert propose_free_address(tiny) == IPv4Address("10.0.0.2")
    assert propose_free_address(tiny, [IPv4Address("10.0.0.2")]) is None
    assert propose_free_address(
        LocalInterface("x", IPv4Address("10.0.0.0"), IPv4Network("10.0.0.0/31"))) is None
