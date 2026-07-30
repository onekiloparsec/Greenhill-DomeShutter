#!/usr/bin/env python3
"""
Turn Conform Universal's JSON results into a CI pass/fail with a readable summary.

ConformU writes a machine-readable results file shaped like:

    {"ErrorCount": 0, "IssueCount": 0, "ConfigurationAlertCount": 0,
     "TimingIssuesCount": 0, "TimingCount": 15,
     "Errors": [], "Issues": [], "ConfigurationAlerts": [], "Timings": [...]}

This is a second line of defence, not the primary gate. ConformU's exit code is
the error count (observed: 0 on a clean pass, 1 and 2 with one and two errors),
so each conformu invocation is its own workflow step and a non-zero exit fails
the build on its own. This script exists so the failing members are named in the
log instead of buried in an uploaded artifact.

Note: the `alpacaprotocol` command does NOT write a results file even when
--resultsfile is given -- only a log. Its verdict therefore comes from the exit
code alone, and this script reports on whatever JSON files do exist.

Usage: check_conformance.py <results-dir>
"""
import json
import os
import sys

# Categories that fail the build. Timing issues are reported but not fatal: the
# runner is a shared VM and the simulator adds latency the real board does not,
# so timing here measures CI noise more than driver behaviour.
FATAL = ('ErrorCount', 'IssueCount', 'ConfigurationAlertCount')
DETAIL_KEYS = {'ErrorCount': 'Errors', 'IssueCount': 'Issues',
               'ConfigurationAlertCount': 'ConfigurationAlerts'}


def describe(entry):
    if isinstance(entry, dict):
        key = entry.get('Key') or entry.get('key') or ''
        value = entry.get('Value') or entry.get('value') or ''
        return f'{key}: {value}'.strip(': ')
    return str(entry)


def check(path):
    name = os.path.basename(path)
    try:
        with open(path) as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f'::error::Could not read Conform results {name}: {exc}')
        return False

    failed = False
    for count_key in FATAL:
        count = report.get(count_key, 0) or 0
        if count:
            failed = True
            print(f'::error::{name}: {count_key} = {count}')
            for entry in report.get(DETAIL_KEYS[count_key], []) or []:
                print(f'    - {describe(entry)}')

    timing = report.get('TimingIssuesCount', 0) or 0
    if timing:
        print(f'::warning::{name}: {timing} timing issue(s) — not failing the '
              f'build, CI latency is not the driver')

    if not failed:
        print(f'{name}: PASS  (0 errors, 0 issues, 0 configuration alerts, '
              f'{report.get("TimingCount", 0)} members timed)')
    return not failed


def main():
    if len(sys.argv) != 2:
        print('usage: check_conformance.py <results-dir>', file=sys.stderr)
        return 2

    results_dir = sys.argv[1]
    reports = sorted(f for f in os.listdir(results_dir) if f.endswith('.json')) \
        if os.path.isdir(results_dir) else []
    if not reports:
        # The interface-conformance command always writes one, so none at all
        # means Conform did not get far enough -- a failure however it exited.
        print(f'::error::No Conform results files found in {results_dir}. '
              f'Conform did not complete; check the uploaded logs.')
        return 1

    if not any(r.startswith('conformance') for r in reports):
        print('::error::No conformance results file. The ASCOM interface '
              'conformance run did not complete.')
        return 1

    ok = True
    for report in reports:
        ok = check(os.path.join(results_dir, report)) and ok
    print()
    print('ASCOM conformance: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
