# Standalone verification scripts

These predate the pytest suite. They are runnable programs, not pytest tests --
they call `sys.exit()` at module level, which breaks pytest collection if they
sit under `tests/`.

    ml_01..04   Still authoritative. They load the real InsightFace model, which
                pytest does not, so they cover ground nothing else does.

    auth        Superseded by tests/test_api.py and tests/test_units.py
    attendance  Superseded by tests/test_flows.py + tests/test_edge_cases.py
    analytics   Superseded by tests/test_refactor.py + tests/test_api.py

The superseded three are kept for now as a cross-check on the pytest suite, but
they are technical debt: two suites covering one thing will drift, and when they
disgree you cannot tell which is right. Retire them once you trust pytest.

Run:  python checks/test_ml_01_preprocessing.py
