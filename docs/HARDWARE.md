# Installation hardware reference

Reference date: **03/10/2026**. This document records equipment information
provided by the owner in the project conversation, including photographs of the
inverter and battery labels. It is not a live inventory or a record of settings
read directly from the devices on that date.

Keep label specifications, owner-reported settings and unknown details separate
when updating this document. Device serial numbers, credentials and network
identifiers are intentionally omitted.

## Inverter

**Manufacturer:** ANENJI

**Exact model on the photographed label:** `ANJ-HHS-11KW-48V-WIFI`

| Specification | Label value / known detail |
| --- | --- |
| Type | Inverter/charger with solar charging and Wi-Fi monitoring |
| Rated output | 11,000 W / 11,000 VA |
| Battery input | 48 V DC, labelled 220 A |
| AC output | Single-phase, 230 V AC, 50/60 Hz, 47.8 A |
| AC charger input | Single-phase, 230 V AC, 50/60 Hz, 60 A |
| AC charger DC output | 54 V DC |
| Maximum AC charging current | 120 A; label states default 60 A |
| Maximum combined PV + AC charging current | 160 A |
| Solar inputs | PV1 and PV2; dual MPPT reported by the owner |
| Maximum PV array power | 5,500 W × 2 |
| Minimum solar voltage | 60 V DC |
| Maximum PV open-circuit voltage (Voc) | 500 V DC |
| Maximum solar input current | 27 A × 2 |
| Maximum MPPT charging current | 160 A |
| Operating temperature | −10 to 55°C |
| Ingress protection | IP21, indoor use |

Earlier conversation and Home Assistant entity names also use
`ANJ-11KW-48V-WIFI-P`. Use the photographed **HHS model** when identifying the
physical inverter or requesting its documentation; an integration's entity name
does not establish that another hardware variant has identical specifications.

The 500 V value is a maximum open-circuit rating, not an operating target.
Panel/string compatibility cannot be established from these inverter values
alone, particularly without panel Voc and its temperature coefficient.

### Previously reported configuration

These values came from the owner and earlier project work. **They have not been
rechecked against the current inverter configuration for this document.**

| Setting / behavior | Previously reported state |
| --- | --- |
| Battery charging limit | 120 A |
| Grid charging of the battery | Disabled; owner selected PV-only charging |
| Lithium battery communication mode | L2 / Li2, reported working via RS485 |
| Return to battery use after entering grid mode | Owner described waiting for approximately 40% SOC |

The 40% threshold informs the dashboard's **Waiting to charge** presentation.
That label is a UI interpretation, not a status read directly from the BMS.
Nameplate charging voltages/currents are specifications, not instructions to
replace the configured battery communication or charge settings.

## Battery

**Manufacturer:** ANENJI

**Model on the photographed label:** `ANJ-314AH-S`

| Specification | Label value / known detail |
| --- | --- |
| Chemistry | LiFePO₄ |
| Nominal voltage | 51.2 V |
| Rated capacity | 314 Ah |
| Nominal energy | 16.07 kWh |
| Charge voltage | 58.4 V |
| Standard charging current | 100 A |
| Maximum charging current | 200 A |
| Maximum discharging current | 200 A |
| Discharge cutoff voltage | 44.8 V |
| Communication | RS485 / CAN |
| Charging temperature | 0 to 65°C |
| Discharging temperature | −10 to 65°C |
| Working humidity | Less than 90% RH |
| Working altitude | At or below 2,000 m |
| Ingress protection | IP21 |
| Cell arrangement | 16 cells in series (16S), reported by the owner |
| Battery management | Built-in BMS, reported by the owner |

The battery's photographed communication ports are labelled **INV, CAN, HC,
RS485-1 and RS485-2**. That photograph was taken **before installation**, so it
does not establish which port or cable is connected now. Cable pinout and the
active BMS protocol have not been documented here.

The battery LCD has shown pack voltage (`PACK V`), pack current (`PACK I`),
remaining capacity in Ah (`PACK RM`), SOC and protection counters. Those photos
are historical snapshots, not current readings or proof of measured usable
capacity. **A** is current; **Ah** is charge capacity. The website's battery
current remains **A (average)** and must not be relabelled as Ah. A measured BMS
remaining-capacity field is not currently established as an available input to
this dashboard.

## Solar panels and strings

- Initially, the owner described approximately **9–10 panels**, with one panel
  disconnected because the PV voltage rose too high. This is an earlier estimate,
  not a verified current count.
- Initially **PV1** was connected and **PV2** was unused.
- The owner later connected **PV2**. The overview and History now track each
  channel's available power, voltage and current separately.
- The inverter input specifications above do not identify the installed panels.

The following remain **unknown**:

| Information to obtain | Current knowledge |
| --- | --- |
| Panel manufacturer and exact model | Not provided |
| Rated power per panel | Not provided |
| Open-circuit voltage (Voc) and operating voltage (Vmp) | Not provided |
| Short-circuit current (Isc) and operating current (Imp) | Not provided |
| Voc temperature coefficient | Not provided |
| Current panel count on PV1 and PV2 | Not confirmed |
| Series/parallel wiring and number of strings on each input | Not confirmed |
| Panel orientation and shading | Not documented |

Do not infer panel specifications from a dashboard voltage reading. PV1 and PV2
are separate inputs: their voltages are displayed separately rather than added.
The generic **PV voltage (legacy)** series preserves older history; it is not a
third physical solar input. The dashboard's Solar total uses the existing
aggregate sensor, rather than assuming it equals the sum of channel readings.

## Monitoring equipment and software

The project uses an **EyeBond/Eybond Wi-Fi monitoring dongle**, **Home Assistant**
and the **EyeBond Local** integration. The exact dongle hardware model has not
been confirmed from a label. Current inverter firmware, battery BMS firmware,
Home Assistant version and installed integration version are not established by
this document.

See [Architecture and maintenance](ARCHITECTURE.md) for data collection, entity
mapping, derived readings, history storage and measurement limitations.

## Updating this reference

When panel labels, wiring details, firmware or settings become available, update
the relevant section with the source and date. Preserve the distinction between
a nameplate limit and an actual configured value. Avoid committing serial
numbers, account details, private addresses, credentials or raw diagnostic dumps.
