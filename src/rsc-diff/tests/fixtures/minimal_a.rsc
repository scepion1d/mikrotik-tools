/interface/list
    add name=iac.list.wan comment="WAN"
    add name=iac.list.lan comment="LAN"

/ip/dns
    set allow-remote-requests=yes

/ip/firewall/filter
    add chain=input action=accept connection-state=established,related comment="iac.fw.in.1 -- est/rel"
    add chain=input action=drop                                          comment="iac.fw.in.last -- drop rest"

/ip/dhcp-server/lease
    add address=192.168.10.2 mac-address=00:E0:4C:45:C0:5F lease-time=0s comment="iac.lease.usbeth -- USB Ethernet"
