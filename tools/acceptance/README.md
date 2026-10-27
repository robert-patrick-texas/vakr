# acceptance

A portable report that turns a parallel run's field bundles into acceptance evidence: does the new monitor's 5-minute traffic agree with MRTG's, and does its latency and loss agree with SmokePing's?

For vakr these are [MVP-ACC-03 and MVP-ACC-04](../../tool-blueprint.md#276-mvp-acceptance-criteria). The rules (tolerances, window length, alignment search, minimum windows) are in `acceptance.toml` and in [Appendix E.34](../../tool-blueprint.md#e34-parallel-run-comparison-mvp-acc-03-mvp-acc-04). How the operator collects the bundles is in [field data: the parallel run](../../docs/operations/field-data.md#the-parallel-run-with-mrtg-and-smokeping), and the method is explained in [the parallel-run comparison](../../docs/development/parallel-run.md).

## Requirements

[uv](https://docs.astral.sh/uv/). The entry script declares Python 3.15 and no dependencies as [PEP 723](https://peps.python.org/pep-0723/) inline metadata, so `uv run` installs nothing. rrdtool is not needed: the field kit records `rrdtool fetch` output on the production host, and this tool parses the text.

## Usage

```sh
# Decrypt and extract the operator's bundles first
uv run tools/fieldkit/field_inbox.py process

# Write parallel-run-report.md and parallel-run-report.json into the inbox
uv run tools/acceptance/acceptance_report.py

# Somewhere else (pseudonymized bundles only), from other bundles, or as a gate (exit 1 unless both criteria pass)
uv run tools/acceptance/acceptance_report.py --output docs/acceptance --bundle vakr-field-... --gate
make acceptance-report ACCEPTANCE_OUT=docs/acceptance
```

| Option | Default | Meaning |
|---|---|---|
| `--inbox DIR` | `inbox` in `acceptance.toml` (the repository's `inbox/`) | Where `field_inbox.py` extracted the bundles |
| `--bundle ID` | every bundle with comparison data | Read only these bundles (repeat the option) |
| `--output DIR` | the inbox | Where the two reports go. A report from clear or mixed bundles may only go under the inbox, which git ignores (E.34 decision 11) |
| `--stem NAME` | `parallel-run-report` | Report file names |
| `--config TOML` | `acceptance.toml` beside the script | The rules |
| `--gate` | off | Exit 1 unless both criteria pass |

Exit status: 0 the reports were written (with `--gate`, and both criteria pass), 1 a criterion did not pass with `--gate`, 2 a usage or configuration error.

## What it reads

Every `inbox/<bundle-id>/` that holds any of these members (the field kit's `mrtg`, `smokeping`, and `acceptance` profiles write them):

| Member | Content |
|---|---|
| `mrtg/targets.csv`, `mrtg/summary.json` | Each MRTG target: kind (`ifindex`, `ifname`, `ifdescr`, `ip`, `mac`, or not comparable), host aliases, counter width, `MaxBytes`/`AbsMax`, `Options`, log format; the host MRTG runs on |
| `mrtg/logs/*.log` | MRTG's rateup log, as written: the counter line and the rows of the window |
| `mrtg/rrd/*.info.txt`, `*.fetch.txt` | `rrdtool info` (kept lines) and `rrdtool fetch AVERAGE` output for `LogFormat: rrdtool` |
| `smokeping/targets.csv`, `smokeping/summary.json`, `smokeping/rrd/*.fetch.txt` | Each probed host from each vantage point, and its RRD rows: loss, median, ping1..N |
| `acceptance/export.json`, `devices.csv`, `interfaces.csv`, `rates/*.csv`, `icmp/*.csv` | The application's export: every interface sample (interval, in/out bit/s, `ifIndex`, flags) and every ICMP cycle (per-probe RTTs) |

Bundles from consecutive days overlap; rows are merged so each window, sample, and cycle counts once. The MRTG and SmokePing bundle can come from a different host than the application's; pseudonymized bundles join when they were made with the same pseudonym key, and the report says so when they were not.

## Files

| File | Role |
|---|---|
| `acceptance_report.py` | Entry point and command line |
| `acceptance.toml` | The rules |
| `acc_config.py` | Reading and checking the rules |
| `acc_sources.py` | MRTG `.log`, `rrdtool info`, and `rrdtool fetch` parsers |
| `acc_vakr.py`, `acc_bundles.py` | The application's export, and merging bundles |
| `acc_match.py` | Matching targets to devices and interfaces |
| `acc_series.py` | Integrals of interval rates, for exact window averages |
| `acc_traffic.py`, `acc_traffic_run.py`, `acc_explain.py` | MVP-ACC-03: MRTG's window model, phase estimate, comparison, explanation categories |
| `acc_latency.py`, `acc_latency_run.py` | MVP-ACC-04: pooling, medians, loss consistency |
| `acc_format.py`, `acc_render.py`, `acc_json.py` | The Markdown and JSON reports |
| `acc_testkit.py`, `test_acc_*.py`, `testdata/` | Tests with known answers and recorded formats |

## Test

```sh
uv run pytest tools/acceptance
```

The tests build bundles whose MRTG data is computed from known traffic by an independent model of rateup's windows, so the comparison must find MRTG's poll phase and agree everywhere; injected faults must each get their own explanation. The parsers are tested against recorded MRTG and rrdtool output. A test proves the tool imports nothing from vakr.

## Reuse in another project

Copy the directory, edit `acceptance.toml`, and produce the application export in the format above (`acc_vakr.py` documents it). The module names start with `acc_` so they do not collide with other tools on the same import path.

## License

Apache License 2.0, like the rest of the repository. See [LICENSE](../../LICENSE).
