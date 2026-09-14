# MikroTik QuickSet Plus 0.1.0
# Target: RouterOS 6.49.10 (v6 dialect)
# Identity: PV-RB4011
# ---- IDENTITY ----
/system identity
set name=PV-RB4011
# ---- BRIDGE / INTERFACE LISTS ----
# Bridge bridge
/interface bridge
add name=bridge protocol-mode=rstp
/interface bridge port
add bridge=bridge interface=ether2
add bridge=bridge interface=ether3
add bridge=bridge interface=ether4
add bridge=bridge interface=ether5
add bridge=bridge interface=ether6
add bridge=bridge interface=ether7
add bridge=bridge interface=ether8
add bridge=bridge interface=ether9
add bridge=bridge interface=ether10
add bridge=bridge interface=sfp-sfpplus1
# Bridge HW offload
set [ find interface=ether2 ] hw=yes
set [ find interface=ether3 ] hw=yes
set [ find interface=ether4 ] hw=yes
set [ find interface=ether5 ] hw=yes
set [ find interface=ether6 ] hw=yes
set [ find interface=ether7 ] hw=yes
set [ find interface=ether8 ] hw=yes
set [ find interface=ether9 ] hw=yes
set [ find interface=ether10 ] hw=yes
set [ find interface=sfp-sfpplus1 ] hw=no
# Interface lists
/interface list
add name=WAN
add name=LAN
/interface list member
add interface=ether1 list=WAN
add interface=bridge list=LAN
# ---- IP ADDRESSING / WAN / DHCP ----
# LAN address
/ip address
add address=192.168.88.1/24 interface=bridge
# WAN: DHCP client on ether1
/ip dhcp-client
add interface=ether1 use-peer-dns=yes add-default-route=yes disabled=no
# DHCP server
/ip pool
add name=dhcp_pool ranges=192.168.88.10-192.168.88.254
/ip dhcp-server
add name=dhcp1 interface=bridge address-pool=dhcp_pool lease-time=30m disabled=no
/ip dhcp-server network
add address=192.168.88.0/24 gateway=192.168.88.1 dns-server=192.168.88.1
# ---- FIREWALL / NAT ----
# Firewall: input
/ip firewall filter
add chain=input action=accept connection-state=established,related,untracked
add chain=input action=drop connection-state=invalid
add chain=input action=accept protocol=icmp
add chain=input action=accept in-interface-list=LAN
add chain=input action=drop in-interface-list=WAN
# Firewall: forward
add chain=forward action=accept connection-state=established,related
add chain=forward action=drop connection-state=invalid
add chain=forward action=drop connection-state=new connection-nat-state=!dstnat in-interface-list=WAN
# NAT
/ip firewall nat
add chain=srcnat action=masquerade out-interface-list=WAN
# ---- DNS / NTP / CLOCK / SNMP / DISCOVERY / IPV6 / LOGGING ----
# LAN clients use the router as DNS server
/ip dns
set allow-remote-requests=yes
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
