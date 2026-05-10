# Minimal fixture used by tests/test_parser.py.
/interface/list
    add comment="iac.list.wan -- WAN" name=iac.list.wan
    add comment="iac.list.lan -- LAN" name=iac.list.lan

/ip/dns
    set allow-remote-requests=yes

/ip/firewall/filter
    add comment="iac.fw.filter.input.1 -- est/rel" chain=input action=accept \
        connection-state=established,related,untracked
    add comment="iac.fw.filter.input.2 -- ICMP" chain=input action=accept \
        protocol=icmp
