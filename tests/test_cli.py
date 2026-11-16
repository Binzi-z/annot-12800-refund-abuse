"""End-to-end CLI tests, including rule override and alert explainability."""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "native_events.jsonl")
STREAM = os.path.join(ROOT, "data", "events.jsonl")


def run_cli(src: str, out: str, *extra: str):
    return subprocess.run(
        [sys.executable, "-I", os.path.join(ROOT, "engine.py"), src, out, *extra],
        capture_output=True,
        text=True,
    )


def test_engine_runs_on_legacy_stream(tmp_path):
    out = tmp_path / "alerts.json"
    r = run_cli(STREAM, str(out))
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "alerts" in doc
    assert doc["alerts"] == []
    assert doc["engine_rule_version"]


def test_native_fixture_cli_alerts_have_all_required_ids(tmp_path):
    out = tmp_path / "alerts.json"
    r = run_cli(FIXTURE, str(out))
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert {a["order_id"] for a in doc["alerts"]} == {
        "ORD-9001",
        "ORD-9002",
        "ORD-9007",
    }
    for alert in doc["alerts"]:
        assert alert["account"]
        assert alert["order_id"]
        assert alert["refund_ids"]
        assert alert["ticket_ids"]
        assert alert["rule_version"]
        assert alert["rule_params"]


def test_rule_override_file_tunes_without_release(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps(
            {
                "rule_version": "1.0.0",
                "params": {"near_full_ratio": 0.7},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "alerts.json"
    r = run_cli(FIXTURE, str(out), "--rules", str(rules))
    assert r.returncode == 0, r.stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["rule_config"]["near_full_ratio"] == 0.7
    assert doc["rule_meta"]["source"] == str(rules)
    assert "ORD-9008" in {a["order_id"] for a in doc["alerts"]}


def test_explain_old_alert_file(tmp_path):
    out = tmp_path / "alerts.json"
    r = run_cli(FIXTURE, str(out))
    assert r.returncode == 0, r.stderr
    r2 = subprocess.run(
        [
            sys.executable,
            "-I",
            os.path.join(ROOT, "engine.py"),
            "--explain",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert r2.returncode == 0, r2.stderr
    report = json.loads(r2.stdout)
    assert report["all_interpretable"] is True
    assert report["alert_count"] == 3
    assert all(e["has_window_evidence"] for e in report["explanations"])


def test_event_file_is_not_modified(tmp_path):
    before = open(STREAM, "rb").read()
    out = tmp_path / "alerts.json"
    r = run_cli(STREAM, str(out))
    assert r.returncode == 0, r.stderr
    after = open(STREAM, "rb").read()
    assert after == before
