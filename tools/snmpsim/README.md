# walk2rec: SNMP walks to snmpsim data files

`walk2rec.py` converts net-snmp walk output into an [snmpsim](https://pypi.org/project/snmpsim/) `.snmprec` data file. It is how a real device captured in the field becomes a simulator that tests can poll.

It is a portable tool (blueprint section 29): it imports nothing from vakr, uses only the Python standard library, and takes everything project-specific from its arguments.

## Input

Walk files of one device, in the form `snmpbulkwalk -v2c -On -Ot -Oe` prints them: numeric OIDs, TimeTicks as bare numbers, and enumerations as numbers. vakr's field kit writes one file per subtree (`system.txt`, `ifTable.txt`, `ifXTable.txt`, `ipAddressTable.txt`, `ipAddrTable.txt`); see [field data](../../docs/operations/field-data.md). Labels such as `up(1)` and `Timeticks: (123) 0:00:01.23` from walks without `-Oe` or `-Ot` are accepted too.

Walks contain no communities. If the capture must leave your hands, convert the scrubbed copy from the field bundle.

## Usage

```console
$ python tools/snmpsim/walk2rec.py inbox/rtr1/snmp/*.txt -o tests/simulators/snmp/data/rtr1.snmprec
walk2rec: 412 records from 5 files -> tests/simulators/snmp/data/rtr1.snmprec (0 skipped)
```

| Option | Meaning |
|---|---|
| `-o FILE` | Write the `.snmprec` file; without it the records go to standard output. |
| `--hex-hint OID` | Treat `STRING` values under this OID prefix as colon-hex octets. This is needed when the walking host had the MIB loaded and a DISPLAY-HINT printed bytes as text. Repeatable. The default is `ifPhysAddress`. |
| `--strict` | Exit 1 if any record could not be converted. Skipped records are always listed on standard error with file and line. |

Records are merged across files, de-duplicated (the last file wins), and sorted in numeric OID order, as snmpsim requires. snmpsim selects a data file by community, so name the file `<community>.snmprec` only in a temporary directory at test time, never in the repository.

## Value mapping

| Walk value | `.snmprec` tag |
|---|---|
| `INTEGER: 1`, `INTEGER: up(1)` | `2` |
| `STRING: text` | `4`, or `4x` (hex) when the text has a newline, a `\|`, or non-ASCII characters |
| `""` | `4` with an empty value |
| `Hex-STRING: 00 1A`, `BITS: 40 00 rx(1)` | `4x` |
| `OID: .1.3.6...` | `6` |
| `IpAddress: 192.0.2.1` | `64` |
| `Counter32`, `Gauge32`, `Counter64` | `65`, `66`, `70` |
| bare number, `Timeticks: (n) ...` | `67` |
| `No Such Object`, `No Such Instance`, `No more variables` | dropped |
| anything else (for example `Opaque: Float`) | skipped and reported |

## Tests

```console
$ uv run pytest tools/snmpsim
```

The fixture in `testdata/rtr-sample` uses only documentation addresses (RFC 5737, RFC 3849) and `example.net` names.
