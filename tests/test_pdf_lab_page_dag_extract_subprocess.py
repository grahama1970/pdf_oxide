from pathlib import Path

import importlib.util


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/run_page_second_pass_dag.py"
    spec = importlib.util.spec_from_file_location("run_page_second_pass_dag_extract_subprocess_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_extract_page_for_repo_code_root_uses_subprocess_to_avoid_pyo3_reinit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dag = _load_module()
    calls = []

    def fake_extract_subprocess(pdf_path, page_number, ledger_path, apply_mode, repo, timeout_s):
        calls.append(
            {
                "pdf_path": pdf_path,
                "page_number": page_number,
                "ledger_path": ledger_path,
                "apply_mode": apply_mode,
                "repo": repo,
                "timeout_s": timeout_s,
            }
        )
        return {"marker": "subprocess"}

    monkeypatch.setattr(dag, "extract_page_subprocess", fake_extract_subprocess)

    result = dag.extract_page_for_code_root(
        tmp_path / "doc.pdf",
        27,
        tmp_path / "ledger.json",
        "release",
        dag.REPO,
        page_extract_timeout_s=None,
    )

    assert result == {"marker": "subprocess"}
    assert calls == [
        {
            "pdf_path": tmp_path / "doc.pdf",
            "page_number": 27,
            "ledger_path": tmp_path / "ledger.json",
            "apply_mode": "release",
            "repo": dag.REPO,
            "timeout_s": 300.0,
        }
    ]
