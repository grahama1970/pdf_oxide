from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _load_page_dag_module():
    module_path = REPO / "scripts/pdf_lab/run_page_second_pass_dag.py"
    spec = importlib.util.spec_from_file_location("run_page_second_pass_dag_cargo_counts", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_run_validation_commands_accepts_cargo_with_one_matching_test_and_empty_bins(
    tmp_path: Path, monkeypatch
) -> None:
    dag = _load_page_dag_module()

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "running 1 test\n"
                "test extractors::block_classifier::tests::test_expected_name ... ok\n\n"
                "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 4543 filtered out\n\n"
                "running 0 tests\n"
                "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(dag.subprocess, "run", fake_run)

    validation = dag.run_validation_commands(
        ["cargo test test_expected_name # src/extractors/block_classifier.rs"],
        cwd=tmp_path,
        required_test_files=["src/extractors/block_classifier.rs"],
    )

    assert validation["ok"] is True
    assert validation["errors"] == []
    assert validation["test_files"] == ["src/extractors/block_classifier.rs"]
