/interface/list
    add name=iac.list.wan comment="WAN uplink"
    add name=iac.list.lan comment="LAN"
    add name=iac.list.mgmt comment="Management"

/ip/dns
    set allow-remote-requests=yes servers=1.1.1.1,9.9.9.9

/ip/firewall/filter
    add chain=input action=accept connection-state=established,related comment="iac.fw.in.1 -- est/rel"
    add chain=input action=accept protocol=icmp                         comment="iac.fw.in.2 -- icmp"
    add chain=input action=drop                                         comment="iac.fw.in.last -- drop rest"

/ip/dhcp-server/lease
    add address=192.168.10.3 mac-address=78:86:2E:53:EC:AD lease-time=0s comment="iac.lease.surface -- Surface"
