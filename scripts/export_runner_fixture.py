"""Turn a saved evaluation run into a fixture for demo/run_real_olist_acceptance.py.
No AWS calls: it only reads a saved runs/*.json file.

    python scripts/export_runner_fixture.py runs/eval_YYYYMMDD_HHMMSS.json S1 PLANTED_REAL_OLIST
    python scripts/export_runner_fixture.py runs/eval_YYYYMMDD_HHMMSS.json S5 REAL_OLIST_INCOMPLETE

The answer key is left empty: it must come from the verifier owner, never from the run.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.integration import acceptance_fixture  # noqa: E402


def main(path: str, scenario: str, label: str) -> None:
    saved = json.loads(Path(path).read_text())
    result = next((r for r in saved["results"] if r["scenario"].split("_")[0] == scenario), None)
    if result is None or not result["runs"]:
        raise SystemExit(f"{scenario} not found in {path}")
    fixture = acceptance_fixture(result["runs"][0], label)
    fixture["source"] = {"file": str(path), "scenario": result["scenario"], "commit": saved.get("commit")}
    out = ROOT / "fixtures" / f"analyst_{scenario}_{label.lower()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"Saved {out}  ({len(fixture['tool_results'])} tool results, {len(fixture['claims'])} claims)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
