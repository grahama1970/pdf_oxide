from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/prepare_isolated_code_root.py"
    spec = importlib.util.spec_from_file_location("prepare_isolated_code_root_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_isolated_code_root_includes_cargo_bench_targets() -> None:
    mod = _load_module()

    assert ".gitignore" in mod.DEFAULT_INCLUDE_PATHS
    assert "Cargo.toml" in mod.DEFAULT_INCLUDE_PATHS
    assert "benches" in mod.DEFAULT_INCLUDE_PATHS


def test_prepare_isolated_code_root_creates_clean_git_baseline(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    (source / "python/pdf_oxide/__pycache__").mkdir(parents=True)
    (source / "scripts/pdf_lab").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "artifacts/pdf_lab").mkdir(parents=True)
    (source / "python/pdf_oxide/extract_for_pdflab.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "python/pdf_oxide/__pycache__/extract_for_pdflab.pyc").write_bytes(b"pyc")
    (source / "python/pdf_oxide/pdf_oxide.abi3.so").write_bytes(b"binary extension")
    (source / "scripts/pdf_lab/snapshot_current_extraction.py").write_text("VALUE = 2\n", encoding="utf-8")
    (source / "tests/test_fix.py").write_text("def test_fix():\n    assert True\n", encoding="utf-8")
    (source / "artifacts/pdf_lab/generated.json").write_text("{}\n", encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")

    manifest = mod.prepare_isolated_code_root(
        source_root=source,
        dest_root=dest,
        include_paths=["python", "scripts/pdf_lab", "tests", "artifacts", "pyproject.toml", "missing"],
    )

    assert manifest["schema"] == "pdf_lab.second_pass.isolated_code_root.v1"
    assert manifest["clean"] is True
    assert manifest["missing_include_paths"] == ["missing"]
    assert (dest / "python/pdf_oxide/extract_for_pdflab.py").is_file()
    assert (dest / "scripts/pdf_lab/snapshot_current_extraction.py").is_file()
    assert (dest / "tests/test_fix.py").is_file()
    assert (dest / "pyproject.toml").is_file()
    assert not (dest / "python/pdf_oxide/__pycache__/extract_for_pdflab.pyc").exists()
    assert not (dest / "python/pdf_oxide/pdf_oxide.abi3.so").exists()
    assert not (dest / "artifacts/pdf_lab/generated.json").exists()
    assert (dest / ".pdf_lab_isolated_code_root.json").is_file()
    assert subprocess.check_output(["git", "-C", str(dest), "status", "--short"], text=True).strip() == ""
    assert subprocess.check_output(["git", "-C", str(dest), "rev-list", "--count", "HEAD"], text=True).strip() == "1"


def test_prepare_isolated_code_root_honors_explicit_binary_file_include(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    extension_path = source / "python/pdf_oxide/pdf_oxide.abi3.so"
    extension_path.parent.mkdir(parents=True)
    extension_path.write_bytes(b"binary extension")

    manifest = mod.prepare_isolated_code_root(
        source_root=source,
        dest_root=dest,
        include_paths=["python/pdf_oxide/pdf_oxide.abi3.so"],
    )

    copied_extension = dest / "python/pdf_oxide/pdf_oxide.abi3.so"
    assert copied_extension.read_bytes() == b"binary extension"
    assert manifest["copied_files"] == ["python/pdf_oxide/pdf_oxide.abi3.so"]
    assert manifest["skipped_paths"] == []
    assert manifest["clean"] is True


def test_prepare_isolated_code_root_auto_includes_cargo_workspace_members(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    (source / "src").mkdir(parents=True)
    (source / "benches").mkdir(parents=True)
    (source / "pdf_oxide_mcp/src").mkdir(parents=True)
    (source / "pdf_oxide_cli/src").mkdir(parents=True)
    (source / "Cargo.toml").write_text(
        "\n".join(
            [
                "[workspace]",
                'members = [".", "pdf_oxide_mcp", "pdf_oxide_cli"]',
                "",
                "[package]",
                'name = "root_crate"',
                'version = "0.1.0"',
                'edition = "2021"',
                "",
                "[lib]",
                'path = "src/lib.rs"',
                "",
                "[[bench]]",
                'name = "loadable_bench"',
                "harness = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source / "src/lib.rs").write_text("pub fn root() {}\n", encoding="utf-8")
    (source / "benches/loadable_bench.rs").write_text("fn main() {}\n", encoding="utf-8")
    for member in ("pdf_oxide_mcp", "pdf_oxide_cli"):
        (source / member / "Cargo.toml").write_text(
            "\n".join(
                [
                    "[package]",
                    f'name = "{member}"',
                    'version = "0.1.0"',
                    'edition = "2021"',
                    "",
                    "[lib]",
                    'path = "src/lib.rs"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (source / member / "src/lib.rs").write_text(f"pub fn {member}() {{}}\n", encoding="utf-8")

    manifest = mod.prepare_isolated_code_root(
        source_root=source,
        dest_root=dest,
        include_paths=["Cargo.toml", "src"],
    )

    assert manifest["auto_included_workspace_members"] == ["pdf_oxide_mcp", "pdf_oxide_cli"]
    assert manifest["auto_included_cargo_targets"] == ["benches"]
    assert (dest / "pdf_oxide_mcp/Cargo.toml").is_file()
    assert (dest / "pdf_oxide_cli/Cargo.toml").is_file()
    assert (dest / "benches/loadable_bench.rs").is_file()
    metadata = subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=dest,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert metadata.returncode == 0, metadata.stderr


def test_prepare_isolated_code_root_refuses_existing_destination_without_force(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    (source / "tests").mkdir(parents=True)
    (source / "tests/test_fix.py").write_text("def test_fix():\n    assert True\n", encoding="utf-8")
    dest.mkdir()

    try:
        mod.prepare_isolated_code_root(source_root=source, dest_root=dest, include_paths=["tests"])
    except FileExistsError as exc:
        assert "destination already exists" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")

    manifest = mod.prepare_isolated_code_root(
        source_root=source,
        dest_root=dest,
        include_paths=["tests"],
        force=True,
    )

    assert manifest["clean"] is True
    assert (dest / "tests/test_fix.py").is_file()


def test_prepare_isolated_code_root_rejects_unsafe_include_paths(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    (source / "tests").mkdir(parents=True)
    (source / "tests/test_fix.py").write_text("def test_fix():\n    assert True\n", encoding="utf-8")

    unsafe_cases = [
        (str(tmp_path / "outside"), "include path must be relative"),
        ("../outside", "include path must not traverse outside source root"),
        (".", "include path must name a file or directory below source root"),
    ]
    for include, expected_error in unsafe_cases:
        try:
            mod.prepare_isolated_code_root(source_root=source, dest_root=dest, include_paths=[include], force=True)
        except ValueError as exc:
            assert expected_error in str(exc)
        else:
            raise AssertionError(f"expected ValueError for include path {include!r}")


def test_prepare_isolated_code_root_skips_symlink_escapes(tmp_path: Path) -> None:
    mod = _load_module()
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    outside = tmp_path / "outside.txt"
    outside.write_text("do not copy\n", encoding="utf-8")
    (source / "tests").mkdir(parents=True)
    (source / "tests/test_fix.py").write_text("def test_fix():\n    assert True\n", encoding="utf-8")
    (source / "tests/external_link.py").symlink_to(outside)

    manifest = mod.prepare_isolated_code_root(source_root=source, dest_root=dest, include_paths=["tests"])

    assert manifest["clean"] is True
    assert "tests/external_link.py" in manifest["skipped_paths"]
    assert (dest / "tests/test_fix.py").is_file()
    assert not (dest / "tests/external_link.py").exists()
    grep = subprocess.run(
        ["git", "-C", str(dest), "grep", "-n", "do not copy", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert grep.returncode == 1
    assert grep.stdout == ""
