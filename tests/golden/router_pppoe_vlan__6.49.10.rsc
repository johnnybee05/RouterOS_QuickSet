# MikroTik QuickSet Plus 0.7.0
# Target: RouterOS 6.49.10 (v6 dialect)
# Identity: PV-R1
# ---- IDENTITY ----
/system identity
set name=PV-R1
# ---- BRIDGE / INTERFACE LISTS ----
# WAN VLAN 848 on ether5
/interface vlan
add name=vlan848 interface=ether5 vlan-id=848 disabled=no
# Bridge bridge1
/interface bridge
add name=bridge1 protocol-mode=none
/interface bridge port
add bridge=bridge1 interface=ether1
add bridge=bridge1 interface=ether2
add bridge=bridge1 interface=ether3
add bridge=bridge1 interface=ether4
# Interface lists
/interface list
add name=WAN
add name=LAN
/interface list member
add interface=ether5 list=WAN
add interface=vlan848 list=WAN
add interface=bridge1 list=LAN
# ---- IP ADDRESSING / WAN / DHCP ----
# LAN address
/ip address
add address=192.168.88.1/24 interface=bridge1
# WAN: PPPoE client on vlan848
/interface pppoe-client
add name=pppoe-out1 interface=vlan848 user="cetin@dsl" password=s3cr3t use-peer-dns=yes add-default-route=yes disabled=no
/interface list member
add interface=pppoe-out1 list=WAN
# DHCP server
/ip pool
add name=dhcp_pool ranges=192.168.88.10-192.168.88.254
/ip dhcp-server
add name=dhcp1 interface=bridge1 address-pool=dhcp_pool lease-time=30m disabled=no
/ip dhcp-server network
add address=192.168.88.0/24 gateway=192.168.88.1 dns-server=192.168.200.200,8.8.8.8
# ---- FIREWALL / NAT ----
# Firewall: replace the rules of an earlier QuickSet run
/ip firewall filter
remove [ find where comment~"^quickset: " ]
# Firewall: input (traffic to the router itself)
add chain=input action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=input action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=input action=accept comment="quickset: accept ICMP" protocol=icmp
add chain=input action=accept comment="quickset: accept to local loopback" dst-address=127.0.0.1
add chain=input action=accept comment="quickset: accept from LAN" in-interface-list=LAN
add chain=input action=accept comment="quickset: accept api-ssl from WAN" protocol=tcp dst-port=8729 in-interface-list=WAN
add chain=input action=accept comment="quickset: accept SSH from WAN" protocol=tcp dst-port=122 in-interface-list=WAN
add chain=input action=accept comment="quickset: accept Winbox from WAN" protocol=tcp dst-port=8291 in-interface-list=WAN
add chain=input action=accept comment="quickset: accept SNMP from WAN" protocol=udp dst-port=161 in-interface-list=WAN
add chain=input action=accept comment="quickset: accept discovery from WAN" protocol=udp dst-port=5678 in-interface-list=WAN
add chain=input action=drop comment="quickset: drop all not coming from LAN" in-interface-list=!LAN
# Firewall: forward (traffic through the router)
add chain=forward action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=forward action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=forward action=drop comment="quickset: drop all from WAN not DSTNATed" connection-state=new connection-nat-state=!dstnat in-interface-list=WAN
# Firewall: keep the rules above those already on the device
:if ([ :len [ /ip firewall filter find where dynamic=no and !(comment~"^quickset: ") ] ] > 0) do={ /ip firewall filter move [ find where dynamic=no and comment~"^quickset: " ] destination=[ :pick [ /ip firewall filter find where dynamic=no and !(comment~"^quickset: ") ] 0 ] }
# NAT
/ip firewall nat
remove [ find where comment~"^quickset: " ]
add chain=srcnat action=masquerade comment="quickset: masquerade to WAN" out-interface-list=WAN
# MSS clamping (SYN packets are never fasttracked)
/ip firewall mangle
remove [ find where comment~"^quickset: " ]
add chain=forward action=change-mss comment="quickset: clamp MSS to path MTU" new-mss=clamp-to-pmtu passthrough=yes protocol=tcp tcp-flags=syn out-interface-list=WAN
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
