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
# WAN: PPPoE client on ether5
/interface pppoe-client
add name=pppoe-out1 interface=ether5 user="user@isp" password=s3cr3t use-peer-dns=yes add-default-route=yes disabled=no
/interface list member
add interface=pppoe-out1 list=WAN
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
add chain=input action=accept comment="quickset: accept from LAN" in-interface-list=LAN
add chain=input action=drop comment="quickset: drop all not coming from LAN" in-interface-list=!LAN
# Firewall: forward (traffic through the router)
add chain=forward action=fasttrack-connection comment="quickset: fasttrack" connection-state=established,related hw-offload=yes
add chain=forward action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=forward action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=forward action=drop comment="quickset: drop all from WAN not DSTNATed" connection-state=new connection-nat-state=!dstnat in-interface-list=WAN
# NAT
/ip firewall nat
add chain=srcnat action=masquerade comment="quickset: masquerade to WAN" out-interface-list=WAN
# MSS clamping (SYN packets are never fasttracked)
/ip firewall mangle
add chain=forward action=change-mss comment="quickset: clamp MSS to path MTU" new-mss=clamp-to-pmtu passthrough=yes protocol=tcp tcp-flags=syn out-interface-list=WAN
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
