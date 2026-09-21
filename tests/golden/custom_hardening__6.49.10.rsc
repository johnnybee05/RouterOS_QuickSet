# MikroTik QuickSet Plus 0.1.0
# Target: RouterOS 6.49.10 (v6 dialect)
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
# Firewall: input (traffic to the router itself)
/ip firewall filter
add chain=input action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=input action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=input action=accept comment="quickset: accept ICMP" protocol=icmp
add chain=input action=accept comment="quickset: accept to local loopback" dst-address=127.0.0.1
add chain=input action=accept comment="quickset: accept from LAN" in-interface-list=LAN
add chain=input action=drop comment="quickset: drop all not coming from LAN" in-interface-list=!LAN
# Firewall: forward (traffic through the router)
add chain=forward action=accept comment="quickset: accept established,related,untracked" connection-state=established,related,untracked
add chain=forward action=drop comment="quickset: drop invalid" connection-state=invalid
add chain=forward action=drop comment="quickset: drop all from WAN not DSTNATed" connection-state=new connection-nat-state=!dstnat in-interface-list=WAN
# NAT
/ip firewall nat
add chain=srcnat action=masquerade comment="quickset: masquerade to WAN" out-interface-list=WAN
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
set servers=1.1.1.1,9.9.9.9
/snmp
set enabled=yes trap-community=PVIP trap-version=2
/system clock
set time-zone-name=Europe/Prague
/system logging
set [ find topics=info ] action=disk
set [ find topics=error ] action=disk
set [ find topics=warning ] action=disk
set [ find topics=critical ] action=disk
/system ntp client
set enabled=yes primary-ntp=195.113.144.201 secondary-ntp=195.113.144.238 server-dns-names=tik.cesnet.cz
/ip proxy
set enabled=no
# ---- IP SERVICES ----
/ip service
set ftp disabled=yes
set telnet disabled=yes
set www disabled=yes
set ssh port=2222
set api disabled=yes
# Management access restricted to the management subnet
set ssh address=10.10.0.0/24
set winbox address=10.10.0.0/24
set api-ssl address=10.10.0.0/24
