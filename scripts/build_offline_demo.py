"""Build offline report examples from saved captures; no model/data query imports.

python scripts/build_offline_demo.py --output demo/review
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.replay import replay
from src.report_renderer import render_report
from src.wbr_contract import ContractError


def load_s1(filename):
    saved = json.loads((ROOT / 'runs' / filename).read_text(encoding='utf-8-sig'))
    scenario = next(s for s in saved['results'] if s['scenario'] == 'S1_category_drop')
    return saved, scenario, scenario['runs'][0]


def report(run, *, week, scenario, source_commit, mode):
    try:
        return replay(run, week, renderer=lambda p: render_report(
            p, week=week, scenario=scenario, source_commit=source_commit, mode=mode)).rendered
    except ContractError:
        # Preserve diagnostic tool metadata while withholding every draft claim.
        payload = {'status': 'rejected', 'tool_results': [], 'claims': [],
                   'removed_claims': run.get('report_draft', {}).get('claims', []),
                   'warnings': ['Saved capture rejected by the period/contract boundary.']}
        # Valid tool envelopes are normalized only for presentation; bad schemas remain hidden.
        from src.analyst_adapter import normalize_captured_envelope
        for env in run.get('evidence_log', {}).values():
            try:
                payload['tool_results'].append(normalize_captured_envelope(env))
            except ContractError:
                pass
        return render_report(payload, week=week, scenario=scenario,
                             source_commit=source_commit, mode=mode)


def synthetic_failure(run, week):
    changed = deepcopy(run)
    failed = 0
    for env in changed['evidence_log'].values():
        if env['tool_name'] == 'get_weekly_metric' and env['parameters'].get('metric') == 'avg_review_score':
            env.update(status='error', data={}, error={'code': 'SIMULATED_TOOL_FAILURE', 'message': 'Synthetic review-score query failure'}, warnings=[])
            failed += 1
    if not failed:
        raise ContractError('capture has no review-score call to simulate')
    draft = changed['report_draft']
    draft['draft_status'] = 'PARTIAL'
    draft['unavailable'] = [{'metric': 'avg_review_score', 'reason': 'Synthetic tool failure'}]
    # Call/transcript histories no longer describe this derived example; never reuse them.
    changed['calls'], changed['transcript'] = [], []
    return changed


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    earlier, scenario, run = load_s1('eval_20261010_115418.json')
    failed, bad_scenario, bad_run = load_s1('eval_20261010_212231.json')
    specs = [
        ('captured-s1.html', run, scenario['week'], 'Earlier S1 capture — original scenario grader PASS', earlier['commit'], 'SAVED CAPTURE REPLAY · planted scenario · current conservative verifier'),
        ('synthetic-failure.html', synthetic_failure(run, scenario['week']), scenario['week'], 'S5-style failure illustration derived from earlier S1', earlier['commit'], 'SYNTHETIC FAILURE REPLAY · not an official S5 run · no new evaluation'),
        ('rejected-week.html', bad_run, bad_scenario['week'], 'Known failed S1 — 12 off-week tool results', failed['commit'], 'KNOWN FAILED CAPTURE · original evidence preserved'),
    ]
    for name, capture, week, label, commit, mode in specs:
        (output / name).write_text(report(capture, week=week, scenario=label,
                                       source_commit=commit, mode=mode), encoding='utf-8')
    (output / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>S04 offline review demo</title><style>body{max-width:650px;margin:40px auto;padding:20px;font:18px/1.6 system-ui}a{display:block;padding:16px;border:1px solid #bbb;border-radius:10px;margin:15px 0;color:#0756a3}</style><h1>S04 offline review demo</h1><p>Saved and synthetic examples. No live model or AWS evaluation.</p><a href="captured-s1.html">Earlier S1 capture · current bridge PARTIAL</a><a href="synthetic-failure.html">Synthetic review-tool failure · PARTIAL</a><a href="rejected-week.html">Known wrong-week capture · REJECTED</a><p>Scenario grading does not certify claims. Corrected live behavior and full golden-data acceptance remain unverified.</p></html>''', encoding='utf-8')
    return [name for name, *_ in specs]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'demo' / 'review')
    args = parser.parse_args()
    for filename in build(args.output):
        print(args.output / filename)
