# 10-interfaces.rsc fixture
/interface/list
    add comment="iac.list.wan -- WAN uplink" name=iac.list.wan
    add comment="iac.list.lan -- LAN" name=iac.list.lan

/interface/ethernet
    set [find default-name=ether1] name=iac.ether.wan \
        comment="iac.ether.wan -- WAN uplink"
