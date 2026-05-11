# 60-system.rsc fixture -- references $routerName + $adminPass.
:global adminPass
:global routerName

/system/identity
    set name=$routerName

/user
    set [find name=admin] password=$adminPass comment="Default admin"
