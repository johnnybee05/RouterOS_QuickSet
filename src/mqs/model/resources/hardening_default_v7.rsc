/snmp community
set [ find default=yes ] disabled=yes
add addresses=::/0 name=PVIP
/ip neighbor discovery-settings
set discover-interface-list=!dynamic
/ipv6 settings
set disable-ipv6=yes
/ip dns
set servers=192.168.200.200,8.8.8.8
/ip service
set ftp disabled=yes
set telnet disabled=yes
set www disabled=yes
set ssh port=122
set api disabled=yes
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
