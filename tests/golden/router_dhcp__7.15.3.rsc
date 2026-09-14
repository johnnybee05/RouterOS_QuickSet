# MikroTik QuickSet Plus 0.1.0
# Target: RouterOS 7.15.3 (v7 dialect)
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
# Interface lists
/interface list
add name=WAN
add name=LAN
/interface list member
add interface=ether5 list=WAN
add interface=bridge list=LAN
# ---- IP ADDRESSING / WAN / DHCP ----
# LAN address
/ip address
add address=192.168.88.1/24 interface=bridge
# WAN: DHCP client on ether5
/ip dhcp-client
add interface=ether5 use-peer-dns=yes add-default-route=yes disabled=no
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
add addresses=::/0 name=PVIP
/ip neighbor discovery-settings
set discover-interface-list=!dynamic
/ipv6 settings
set disable-ipv6=yes
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
set enabled=yes
/system ntp client servers
add address=195.113.144.201
add address=195.113.144.238
# ---- IP SERVICES ----
/ip service
set ftp disabled=yes
set telnet disabled=yes
set www disabled=yes
set ssh port=122
set api disabled=yes
