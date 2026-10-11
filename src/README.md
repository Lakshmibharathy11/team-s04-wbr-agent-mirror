# Source

The runner, numeric verifier, captured analyst adapter and period guard enforce report acceptance before presentation. `report_renderer.py` is the offline HTML renderer for the verified runner payload. It separates confirmed findings from review items and withholds claims with unavailable or mismatched evidence. Data tools remain in `tools/`; analyst planning and replay remain in `agent/`.

The renderer does not call AWS, establish causes or certify arbitrary prose. See `docs/DEMO_HANDOFF_2026-10-11.md` for the offline report builder and pending verification.
