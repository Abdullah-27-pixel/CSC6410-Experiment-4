#!/usr/bin/env python3
"""CSC 6410 Week 4: measure traffic on four pod-0 core-facing links.

Use inside the professor's existing Mininet CLI; this file does not create a network.
At mininet>:
    py __import__('measure_ecmp').run_all(net)
Then, for the failure experiment, manually enter `link a00 c00 down` and run:
    py __import__('measure_ecmp').run_failure(net)
Finally enter `link a00 c00 up` to restore the link.

Method: take cumulative OpenFlow 1.3 TX byte counters immediately before
and after a 20-second fixed-rate UDP iperf run; report differences and shares.
The OpenFlow counters include all transmitted bytes (including possible control
traffic); this script does not claim to identify individual hashed flows.
"""
import csv
import datetime
import os
import re
import subprocess
from pathlib import Path

CORE_PORTS = [('a00', 3, 'c00'), ('a00', 4, 'c01'),
              ('a01', 3, 'c10'), ('a01', 4, 'c11')]
OUT_DIR = Path('/tmp/csc6410_exp4')
DESTINATION = '10.3.0.2'  # professor's h300 address, only inside Mininet
SECONDS = 20
MBPS_PER_FLOW = 1


def port_tx_bytes(switch, port):
    """Read cumulative transmitted bytes from the specified OpenFlow port."""
    result = subprocess.run(
        ['ovs-ofctl', '-O', 'OpenFlow13', 'dump-ports', switch, str(port)],
        capture_output=True, text=True, check=True)
    match = re.search(r'tx\s+pkts=\s*\d+,\s*bytes=\s*(\d+)', result.stdout)
    if not match:
        raise RuntimeError(f'Cannot parse TX bytes for {switch} port {port}: {result.stdout[:300]}')
    return int(match.group(1)), result.stdout


def sample_ports():
    """Return counters in core-link order and raw OpenFlow output."""
    readings = []
    raw = []
    for switch, port, core in CORE_PORTS:
        count, text = port_tx_bytes(switch, port)
        readings.append(count)
        raw.append(f'--- {switch} port {port} toward {core} ---\n{text}')
    return readings, '\n'.join(raw)


def measure_one(net, flows, repeat, scenario):
    """Run N UDP client streams, record before/after counters, and one result row."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    label = f'{scenario}_N{flows}_run{repeat}'
    before, raw_before = sample_ports()
    # The receiver was started manually on h300; h000 runs the client here.
    command = (f'iperf -c {DESTINATION} -u -b {MBPS_PER_FLOW}M '
               f'-P {flows} -t {SECONDS} > {OUT_DIR / (label + "_iperf.txt")} 2>&1; '
               f'printf "CLIENT_EXIT=%s\\n" "$?"')
    print(f'\n>>> {label}: sending {flows} parallel UDP streams for {SECONDS}s', flush=True)
    answer = net.get('h000').cmd(command)
    after, raw_after = sample_ports()
    # Persist exact counter evidence even if the values are unexpected.
    (OUT_DIR / (label + '_before.txt')).write_text(raw_before, encoding='utf-8')
    (OUT_DIR / (label + '_after.txt')).write_text(raw_after, encoding='utf-8')
    if 'CLIENT_EXIT=0' not in answer:
        print('WARNING: iperf may have failed. Check:',
              OUT_DIR / (label + '_iperf.txt'), 'client response:', answer, flush=True)
    deltas = [max(0, a - b) for a, b in zip(after, before)]
    total = sum(deltas)
    if not total:
        raise RuntimeError(f'No TX bytes increased during {label}; check receiver and topology')
    shares = [100.0 * x / total for x in deltas]
    busiest_share = max(shares)
    max_mean = 4.0 * max(deltas) / total
    print('Core link         Before bytes   After bytes   Delta bytes   Share', flush=True)
    for (switch, port, core), b, a, d, share in zip(CORE_PORTS, before, after, deltas, shares):
        print(f'{switch}-eth{port} -> {core:<3} {b:>12} {a:>13} {d:>13} {share:>7.2f}%', flush=True)
    print(f'TOTAL DELTA={total}, BUSIEST={busiest_share:.2f}%, MAX/MEAN={max_mean:.3f}', flush=True)
    row = {'scenario': scenario, 'flows': flows, 'run': repeat,
           'timestamp': datetime.datetime.now().isoformat(timespec='seconds'),
           'total_bytes': total, 'busiest_share_percent': round(busiest_share, 3),
           'max_mean': round(max_mean, 4), 'iperf_exit_ok': 'CLIENT_EXIT=0' in answer}
    for (sw, port, core), b, a, delta, share in zip(CORE_PORTS, before, after, deltas, shares):
        prefix = f'{sw}_port{port}_to_{core}'
        row[f'{prefix}_before'] = b
        row[f'{prefix}_after'] = a
        row[f'{prefix}_delta'] = delta
        row[f'{prefix}_share_percent'] = round(share, 3)
    return row


def save_results(rows, filename):
    """Save the full per-run dataset without replacing earlier raw evidence."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / filename
    with output.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f'CSV saved: {output}', flush=True)


def run_all(net):
    """Required normal condition: 4, 8, 16, 32 streams; 3 repetitions each."""
    rows = []
    for flows in (4, 8, 16, 32):
        for repeat in (1, 2, 3):
            rows.append(measure_one(net, flows, repeat, 'normal'))
            save_results(rows, 'normal_results.csv')
    print('All 12 normal runs complete. Raw OpenFlow and iperf output is in', OUT_DIR, flush=True)


def run_failure(net):
    """Required failure condition: after the user disables link a00-c00 in CLI."""
    # Do not change topology automatically; user must verify it is actually down.
    row = measure_one(net, 16, 1, 'link_a00_c00_down')
    save_results([row], 'link_failure_results.csv')
    print('Failure run completed. IMPORTANT: restore with: link a00 c00 up', flush=True)
