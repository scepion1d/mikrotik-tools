# 50-services.rsc fixture -- references $adminCidrs from vars.rsc.
:global adminCidrs

/ip/service
    set winbox  address=$adminCidrs
    set ssh     address=$adminCidrs
