from pathlib import Path

import pytest

import tools.ai_nlp_tester as tester


def test_ai_nlp_tester_golden_mode_does_not_initialize_llm(monkeypatch, tmp_path):
    output = tmp_path / "golden.md"

    class FailingLLM:
        def __init__(self, *args, **kwargs):
            raise AssertionError("golden mode must not initialize LLM")

    class DummyBed:
        table = {}
        flow_names = []
        has_ds = False

    monkeypatch.setattr(tester, "LLM", FailingLLM)
    monkeypatch.setattr(tester, "TestBed", DummyBed)
    monkeypatch.setattr(tester, "run_golden", lambda bed: "# golden")
    monkeypatch.setattr(tester.sys, "argv", ["ai_nlp_tester.py", "--golden", "-o", str(output)])

    tester.main()

    assert output.read_text(encoding="utf-8") == "# golden"
