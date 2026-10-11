"""Offline HTML presentation of verifier output; never renders raw analyst drafts."""
from html import escape
import json
from collections.abc import Mapping

from .period_guard import period_problem
from .wbr_contract import ContractError
from .wbr_runner import validate_tool_envelope, validate_claim


def text(value):
    return escape(str(value), quote=True)


def render_report(payload, *, week, scenario, source_commit, mode):
    """Render a runner payload with evidence links and explicit review/failure states.

    This function does not verify prose. Its input must come from the shared runner.
    Defensive period/citation checks prevent presentation from reviving bad evidence.
    """
    if not isinstance(payload, Mapping) or payload.get('status') not in {'complete', 'partial', 'rejected'}:
        raise ContractError('renderer requires an explicit runner status')
    rejected = payload['status'] == 'rejected'
    envelopes = [validate_tool_envelope(e) for e in payload.get('tool_results', [])]
    by_ref, cards = {}, []
    period_bad = False
    for index, env in enumerate(envelopes):
        original = env.get('source_envelope', env)
        problem = period_problem(original, week)
        period_bad |= bool(problem)
        anchor = f'evidence-{index}'
        refs = list(env['evidence_refs'])
        if 'source_envelope' in env:
            refs.append(env['request_id'])
        for ref in refs:
            if ref in by_ref:
                raise ContractError('renderer evidence references must be unique')
            by_ref[ref] = (anchor, env['status'] == 'ok' and not problem)
        p = env['parameters']
        filters = json.dumps({k: p[k] for k in ('metric', 'dimension', 'state', 'category', 'filters') if k in p}, sort_keys=True)
        provenance = original.get('evidence_refs', [])
        provenance = [{k: r[k] for k in ('query_id', 'backend', 'table', 'executed_at') if k in r}
                      if isinstance(r, Mapping) else r for r in provenance]
        warning = f'<p class="warning">{text(problem)}</p>' if problem else ''
        failure = '<p class="warning">Tool failed. Values are unavailable; affected claims cannot be confirmed.</p>' if env['status'] == 'error' else ''
        # Do not display provider exception text or arbitrary model-generated data.
        data = env['data'] if isinstance(env['data'], Mapping) and env['status'] != 'error' else {}
        metric = {k: data[k] for k in ('metric', 'current', 'previous', 'change', 'change_type') if k in data}
        values = f'<pre>{text(json.dumps(metric, sort_keys=True))}</pre>' if metric else ''
        cards.append(f'<article id="{anchor}" class="evidence"><h3>{text(env["tool_name"])} · {text(env["status"].upper())}</h3>'
                     f'<p class="mono">{text(env["request_id"])}</p><p>Week {text(p.get("week", "missing"))} · comparison {text(p.get("compare_week", "missing"))}</p>'
                     f'<p>Filters: {text(filters)}</p>{warning}{failure}{values}'
                     f'<details><summary>Query provenance</summary><pre>{text(json.dumps(provenance, indent=2, sort_keys=True))}</pre></details></article>')
    confirmed, review = [], []
    withheld = len(payload.get('removed_claims', []))
    for raw in payload.get('claims', []):
        claim = validate_claim(raw)
        outcome = raw.get('verifier_outcome')
        if outcome not in {'keep', 'flag', 'downgrade', 'remove'}:
            raise ContractError('renderer requires verifier outcomes for every claim')
        refs = claim['supporting_evidence']
        supported = bool(refs) and all(ref in by_ref and by_ref[ref][1] for ref in refs)
        if rejected or period_bad or outcome == 'remove' or not supported:
            withheld += 1
            continue
        links = ' '.join(f'<a href="#{by_ref[ref][0]}">{text(ref)}</a>' for ref in refs)
        card = (f'<article class="claim"><p>{text(claim["statement"])}</p>'
                f'<p class="meta">{text(claim["question_id"])} · {text(claim["claim_type"])} · confidence {text(claim["confidence"])}</p>'
                f'<p class="meta">Evidence: {links}</p></article>')
        if outcome == 'keep' and not claim['requires_human_review']:
            confirmed.append(card)
        else:
            review.append(card[:-10] + f'<p class="warning">{text(raw.get("verifier_reason", "Human review required"))}</p></article>')
    # No independently reviewed mandatory-coverage validator is available.
    # Never turn a caller's complete flag into whole-report certification.
    status = 'REJECTED' if rejected or period_bad else 'PARTIAL'
    warnings = ''.join(f'<li>{text(w)}</li>' for w in payload.get('warnings', []))
    badge_items = list(dict.fromkeys([str(w) for w in payload.get('warnings', [])]
                                    + [w for e in envelopes for w in e['warnings']]))
    badge_warnings = ''.join(f'<li>{text(w)}</li>' for w in badge_items)
    if any(e['status'] != 'ok' for e in envelopes):
        badge_warnings += '<li>Some tool evidence is partial or unavailable.</li>'
    badge_warnings = '<ul>' + badge_warnings + '</ul>' if badge_warnings else ''
    message = ('Claims withheld: evidence periods or report contract were rejected.' if status == 'REJECTED' else
               'Some findings or evidence require review. This report is not certified complete.' if status == 'PARTIAL' else
               'The selected claims passed the configured verifier. Completion does not establish causation or certify live model reliability.')
    confirmed_html = ''.join(confirmed) or '<p>No confirmed findings are available.</p>'
    review_html = ''.join(review) or '<p>No additional review findings are shown.</p>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>S04 weekly business review · {text(status)}</title><style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f8;color:#17263a;font:17px/1.55 system-ui,sans-serif}}main{{max-width:900px;margin:auto;padding:24px 18px}}header{{background:#12243d;color:white;padding:26px;border-radius:18px}}h1{{font-size:clamp(25px,5vw,38px);line-height:1.2}}h2{{margin-top:32px}}h3{{font-size:18px}}.badge{{display:inline-block;background:#fff;color:#12243d;border-radius:8px;padding:6px 12px;font-weight:800}}.claim,.evidence,.notice{{background:white;border:1px solid #ccd5df;padding:18px;border-radius:12px;margin:12px 0}}.notice{{border-left:6px solid #9b5700}}.warning{{color:#8b3b00;font-weight:650}}.meta{{font-size:14px}}.mono,pre{{font-family:ui-monospace,monospace;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;font-size:13px}}a{{color:#0756a3;overflow-wrap:anywhere}}summary{{cursor:pointer;padding:10px 0}}footer{{font-size:14px;margin:30px 0}}@media print{{body{{background:white}}main{{max-width:none}}header{{color:black;background:white;border:1px solid #aaa}}details{{display:block}}}}
</style></head><body><main><header><p>TEAM S04 · OLIST · WEEKLY BUSINESS REVIEW</p><h1>Evidence before explanation</h1><span class="badge">{status}</span>{badge_warnings}<p>{text(scenario)} · requested week {text(week)}</p></header>
<div class="notice"><strong>{text(mode)}</strong><p>{text(message)}</p><p>Source revision: <span class="mono">{text(source_commit)}</span></p><p>Offline presentation: no model or AWS calls.</p></div>
<h2>Confirmed findings</h2>{confirmed_html}<h2>Findings requiring human review</h2><p>These statements remain unconfirmed. They are shown for review, not as established conclusions.</p>{review_html}
<h2>Unavailable or withheld</h2><p>{withheld} claim(s) withheld. Removed statement text and raw analyst drafts are not displayed.</p><ul>{warnings}</ul>
<h2>Evidence and periods</h2>{''.join(cards) or '<p>No tool evidence available.</p>'}
<footer>Public Olist data and explicitly labeled synthetic scenarios. Observations do not prove causes. Full golden-data acceptance and live corrected-model evaluation remain separate checks.</footer></main></body></html>'''
