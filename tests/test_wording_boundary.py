"""Offline integration checks independent of postcheck and custom verifier trust."""
from pathlib import Path
import importlib.util
import json
import pytest
from src.analyst_adapter import run_analyst_week
from test_period_binding import WEEK, envelope, captured, keep

@pytest.mark.parametrize('statement', [
    'Delivery improvements may have contributed to the revenue change.',
    'Late deliveries likely lowered reviews.',
    'Slower delivery may explain the drop.',
    'Other includes SP.',
    'SP is part of Other.',
])
def test_permissive_verifier_cannot_certify_causal_or_containment_prose(statement):
    run = captured(envelope())
    run['report_draft']['claims'][0]['statement'] = statement
    out = run_analyst_week(WEEK, lambda _: run,
                           expected_tool_names=['get_weekly_metric'], verifier=keep)
    assert out.status == 'partial'
    assert out.claims[0]['verifier_outcome'] == 'flag'

def test_neutral_description_can_reach_test_verifier():
    run = captured(envelope())
    run['report_draft']['claims'][0]['statement'] = 'SP accounts for 46.28% of the total change.'
    out = run_analyst_week(WEEK, lambda _: run,
                           expected_tool_names=['get_weekly_metric'], verifier=keep)
    assert out.status == 'partial'

def test_demo_viewer_rejects_wrong_week_and_withholds_claims(capsys):
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('viewer',root/'scripts/show_run.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    batch=json.loads((root/'runs/eval_20261010_212231.json').read_text(encoding='utf-8-sig'))
    module.show(batch['results'][0],batch)
    output=capsys.readouterr().out
    assert 'REJECTED' in output and 'claims withheld' in output
    assert 'week=2018-08-20' in output
    assert 'which includes' not in output
