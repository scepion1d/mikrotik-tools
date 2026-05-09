# rsc/

This directory is intentionally empty in the public tooling repo.

The expected layout per site:

```
rsc/<site>/
├── <site>.rsc           orchestrator (entry point)
├── vars.rsc             non-sensitive tunables
├── secrets.rsc          credentials (gitignored)
├── helpers/
│   └── log.rsc
└── modules/
    ├── 10-interfaces.rsc
    ├── 20-wifi.rsc
    ├── 30-ip.rsc
    ├── 40-firewall.rsc
    ├── 50-services.rsc
    └── 60-system.rsc
```

See the top-level [README.md](../README.md) for the bundle/diff/deploy flow.
