"""Presentation regressions: rejected evidence and raw drafts never become findings."""
import json
from pathlib import Path
import socket

import pytest

from src.report_renderer import render_report
from src.wbr_contract import ContractError
from scripts.build_offline_demo import build

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('offline renderer attempted networking')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def payload():
    p = json.loads((ROOT / 'fixtures/renderer_input_complete.json').read_text())
    for env in p['tool_results']:
        env['parameters'].update(week='2018-08-13', compare_week='2018-08-06')
    return p


def render(p):
    return render_report(p, week='2018-08-13', scenario='SYNTHETIC',
                         source_commit='unit-fixture', mode='SYNTHETIC UNIT FIXTURE')


def test_complete_findings_link_to_evidence(payload):
    html = render(payload)
    assert '<span class="badge">PARTIAL</span>' in html
    assert 'href="#evidence-0"' in html
    assert 'Synthetic revenue declined' in html


def test_wrong_week_cannot_revive_keep_claim(payload):
    payload['tool_results'][0]['parameters']['week'] = '2018-08-20'
    html = render(payload)
    assert '<span class="badge">REJECTED</span>' in html
    assert 'Synthetic revenue declined' not in html
    assert '2018-08-20' in html


def test_missing_comparison_is_rejected(payload):
    del payload['tool_results'][0]['parameters']['compare_week']
    assert '<span class="badge">REJECTED</span>' in render(payload)


def test_unavailable_evidence_never_displays_claim(payload):
    env = payload['tool_results'][0]
    env.update(status='error', data={}, error={'code': 'FAIL', 'message': 'SECRET_PROVIDER_TEXT'})
    html = render(payload)
    assert '<span class="badge">PARTIAL</span>' in html
    assert 'Synthetic revenue declined' not in html
    assert 'SECRET_PROVIDER_TEXT' not in html
    assert 'Tool failed' in html


def test_review_claim_not_in_confirmed_section(payload):
    payload['claims'][0]['requires_human_review'] = True
    html = render(payload)
    assert '<span class="badge">PARTIAL</span>' in html
    confirmed = html.split('<h2>Confirmed findings</h2>')[1].split('<h2>Findings requiring human review</h2>')[0]
    assert 'Synthetic revenue declined' not in confirmed
    assert 'Synthetic revenue declined' in html


def test_raw_draft_and_removed_statement_never_leak(payload):
    payload['analyst_draft'] = {'claims': [{'statement': 'DRAFT_SECRET'}]}
    payload['analyst_transcript'] = ['TRANSCRIPT_SECRET']
    payload['removed_claims'] = [{'statement': 'REMOVED_SECRET'}]
    html = render(payload)
    assert all(marker not in html for marker in ('DRAFT_SECRET', 'TRANSCRIPT_SECRET', 'REMOVED_SECRET'))


def test_all_dynamic_text_escaped(payload):
    payload['claims'][0]['statement'] = '<script>alert("unsafe")</script>'
    payload['warnings'] = ['<img src=x onerror=alert(1)>']
    html = render(payload)
    assert '<script>' not in html and '<img src=x' not in html
    assert '&lt;script&gt;' in html


def test_missing_verifier_outcome_rejected(payload):
    del payload['claims'][0]['verifier_outcome']
    with pytest.raises(ContractError, match='verifier outcomes'):
        render(payload)


def test_unknown_citation_withheld(payload):
    payload['claims'][0]['supporting_evidence'] = ['unknown']
    assert 'Synthetic revenue declined' not in render(payload)


def test_demo_replays_real_and_derived_examples_without_mutating_capture(tmp_path):
    source = ROOT / 'runs/eval_20261010_115418.json'
    before = source.read_bytes()
    assert len(build(tmp_path)) == 3
    assert source.read_bytes() == before
    earlier = (tmp_path / 'captured-s1.html').read_text()
    failure = (tmp_path / 'synthetic-failure.html').read_text()
    rejected = (tmp_path / 'rejected-week.html').read_text()
    assert '<span class="badge">PARTIAL</span>' in earlier
    assert '<span class="badge">PARTIAL</span>' in failure
    assert 'not an official S5 run' in failure and 'Tool failed' in failure
    assert '<span class="badge">REJECTED</span>' in rejected
    bad = json.loads((ROOT / 'runs/eval_20261010_212231.json').read_text())
    for claim in bad['results'][0]['runs'][0]['report_draft']['claims']:
        assert claim['statement'] not in rejected
    assert '2018-08-20' in rejected


def test_uncited_error_and_warning_cannot_be_complete(payload):
    from copy import deepcopy
    extra = deepcopy(payload['tool_results'][0])
    extra.update(request_id='uncited', status='error', evidence_refs=[], data={},
                 error='SECRET', warnings=['SMALL_SAMPLE'])
    payload['tool_results'].append(extra)
    html = render(payload)
    assert '<span class="badge">PARTIAL</span>' in html
    header = html.split('</header>')[0]
    assert 'SMALL_SAMPLE' in header
    assert 'partial or unavailable' in header
    assert 'SECRET' not in html


def test_runner_warning_visible_in_header(payload):
    payload['warnings'] = ['analyst draft period does not match run week']
    html = render(payload)
    assert payload['warnings'][0] in html.split('</header>')[0]
    assert '<span class="badge">PARTIAL</span>' in html
