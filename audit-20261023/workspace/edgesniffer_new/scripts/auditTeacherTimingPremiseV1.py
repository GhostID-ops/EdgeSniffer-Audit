"""Descriptive recalculation from preserved assessment rows; never an economics test."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path


def analyze(path):
    raw = path.read_bytes()
    doc = json.loads(raw)
    rows = doc['eventDrivenReplay']['rows']
    selected = [r['selectedStateToTeacherMs'] for r in rows if r.get('selectedStateToTeacherMs') is not None]
    first = [r['firstCrossingToTeacherMs'] for r in rows if r.get('firstCrossingToTeacherMs') is not None]
    crossings = [r['crossingStateN'] for r in rows]
    return dict(schema='TEACHER_TIMING_PREMISE_DIAGNOSTIC_V1',
        source_sha256=hashlib.sha256(raw).hexdigest(), assessment_rows=len(rows),
        unique_mints=len({r['mint'] for r in rows}), selected_lead_rows=len(selected),
        selected_lead_median_ms=statistics.median(selected) if selected else None,
        selected_lead_ge_1000ms=sum(x >= 1000 for x in selected),
        first_crossing_rows=len(first), missing_crossing_rows=len(rows)-len(first),
        first_crossing_lead_median_ms=statistics.median(first) if first else None,
        first_crossing_lead_ge_1000ms=sum(x >= 1000 for x in first),
        above_threshold_states=sum(crossings), largest_episode_crossings=max(crossings, default=0),
        limitations=['Teacher-selected mint histories; market-wide false-alert rate unavailable.',
                    'Receipt lead is not chain-order or executable price evidence.',
                    'Repeated states/mints are dependent observations.',
                    'No return, profitability, or calibrated probability computed.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps(result))
