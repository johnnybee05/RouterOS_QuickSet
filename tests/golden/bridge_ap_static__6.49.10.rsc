# MikroTik QuickSet Plus 0.6.0
# Target: RouterOS 6.49.10 (v6 dialect)
# Identity: PV-AP1
# ---- IDENTITY ----
/system identity
set name=PV-AP1
# ---- BRIDGE / INTERFACE LISTS ----
# Bridge bridge1
/interface bridge
add name=bridge1 protocol-mode=none
/interface bridge port
add bridge=bridge1 interface=ether1
add bridge=bridge1 interface=ether2
add bridge=bridge1 interface=ether3
add bridge=bridge1 interface=ether4
add bridge=bridge1 interface=ether5
# Interface lists
/interface list
add name=LAN
/interface list member
add interface=bridge1 list=LAN
# ---- IP ADDRESSING / WAN / DHCP ----
# LAN address
/ip address
add address=192.168.88.10/24 interface=bridge1
# Default route through the LAN gateway
/ip route
add dst-address=0.0.0.0/0 gateway=192.168.88.1
# ---- DNS / NTP / CLOCK / SNMP / DISCOVERY / IPV6 / LOGGING ----
# DNS: the router answers no remote requests
/ip dns
set allow-remote-requests=no
# Hardening block
/snmp community
set [ find default=yes ] disabled=yes
add addresses=0.0.0.0/0 name=PVIP
/ip neighbor discovery-settings
set discover-interface-list=!dynamic
/system package
disable ipv6
/ip dns
set servers=192.168.200.200,8.8.8.8
/snmp
set enabled=yes trap-community=PVIP trap-version=2
/system clock
set time-zone-name=Europe/Prague
/system logging
set 0 action=disk
set 1 action=disk
set 2 action=disk
set 3 action=disk
/system ntp client
set enabled=yes primary-ntp=195.113.144.201 secondary-ntp=195.113.144.238
# ---- IP SERVICES ----
/ip service
set ftp disabled=yes
set telnet disabled=yes
set www disabled=yes
set ssh port=122
set api disabled=yes
