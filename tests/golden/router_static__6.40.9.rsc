# MikroTik QuickSet Plus 0.1.0
# Target: RouterOS 6.40.9 (v6 dialect)
# Identity: PV-R1
# ---- IDENTITY ----
/system identity
set name=PV-R1
# ---- BRIDGE / INTERFACE LISTS ----
# Bridge bridge
/interface bridge
add name=bridge protocol-mode=rstp
/interface bridge port
add bridge=bridge interface=ether1
add bridge=bridge interface=ether2
add bridge=bridge interface=ether3
add bridge=bridge interface=ether4
# ---- IP ADDRESSING / WAN / DHCP ----
# LAN address
/ip address
add address=192.168.88.1/24 interface=bridge
# WAN: static address on ether5
add address=203.0.113.10/29 interface=ether5
/ip route
add dst-address=0.0.0.0/0 gateway=203.0.113.9
# DHCP server
/ip pool
add name=dhcp_pool ranges=192.168.88.10-192.168.88.254
/ip dhcp-server
add name=dhcp1 interface=bridge address-pool=dhcp_pool lease-time=30m disabled=no
/ip dhcp-server network
add address=192.168.88.0/24 gateway=192.168.88.1 dns-server=192.168.88.1
# ---- FIREWALL / NAT ----
# Firewall: input (traffic to the router itself)
/ip firewall filter
add chain=input action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=input action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=input action=accept comment="quickset: accept ICMP" protocol=icmp
add chain=input action=accept comment="quickset: accept to local loopback" dst-address=127.0.0.1
add chain=input action=accept comment="quickset: accept from LAN" in-interface=bridge
add chain=input action=drop comment="quickset: drop all not coming from LAN" in-interface=!bridge
# Firewall: forward (traffic through the router)
add chain=forward action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=forward action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=forward action=drop comment="quickset: drop all from WAN not DSTNATed" connection-state=new connection-nat-state=!dstnat in-interface=ether5
# NAT
/ip firewall nat
add chain=srcnat action=masquerade comment="quickset: masquerade to WAN" out-interface=ether5
# ---- DNS / NTP / CLOCK / SNMP / DISCOVERY / IPV6 / LOGGING ----
# DNS servers from static WAN
/ip dns
set servers=1.1.1.1
# LAN clients use the router as DNS server
set allow-remote-requests=yes
# Hardening block
/snmp community
set [ find default=yes ] disabled=yes
add addresses=0.0.0.0/0 name=PVIP
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
