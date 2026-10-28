# vakr

*vakr* (Old Norse: wakeful, alert, vigilant) is an open-source, Linux-native network performance and reachability monitor. It is built to take over the jobs of MRTG and SmokePing:

- interface traffic, packet, and error history without artificial reset spikes;
- SmokePing-style latency, jitter, and loss graphs;
- SNMP discovery with persistent interface identity across `ifIndex` changes;
- modern web dashboards and a terminal UI;
- collection that keeps running through database outages and network partitions.

> **Status:** MVP acceptance (milestone M6). Released packages install, collect, and serve the web UI. Acceptance on a production network, including a 14-day parallel run beside MRTG and SmokePing, is still to come.

## Documents

- [Features](docs/features.md): what vakr does today, area by area, what is planned, and how it compares with MRTG and SmokePing.
- [Installation](docs/operations/install.md)
- [Blueprint](tool-blueprint.md): the authoritative requirements, architecture, test plan, and roadmap. Start with [section 27, the MVP definition](tool-blueprint.md#27-mvp-definition).
- [Changelog](CHANGELOG.md)
- [md2html](tools/md2html/README.md): the portable Markdown-to-HTML tool that publishes these documents.

Rendered documentation: <https://robert-patrick-texas.github.io/vakr/>

## MVP at a glance

| | |
|---|---|
| Scale | 100 Cisco IOS XE devices, about 1,600 active interfaces |
| Protocols | ICMP (5-probe groups) and SNMPv2c |
| Retention | 30 days at native resolution plus 400 days of 15-minute rollups |
| Positions | One required; two (east/west) for collection that survives a partition |
| Platform | Ubuntu 26.04 LTS `.deb`, self-updating from GitHub releases |

## Verifying releases

Release packages are verified before they are installed. Each GitHub release includes `SHA256SUMS`, signed with the project's release key (`SHA256SUMS.sig`), and `vakr self-update` checks that signature against the key in [`packaging/release-signers`](packaging/release-signers) before installing anything. Before the first manual install, compare the key's fingerprint with this value:

```
SHA256:RNCDpt4jEx+n6vgGCTaKQv8ku7TKS//0u2XOij0G5cQ  vakr-release (ED25519)
```

See the [installation guide](docs/operations/install.md) for the verification commands.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
