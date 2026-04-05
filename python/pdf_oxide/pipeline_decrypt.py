"""Minimal PDF decryption preprocessing.

Detects encrypted PDFs and attempts decryption with:
1. Empty password (permissions-only encryption — most common case)
2. User-provided password (via PipelineConfig.decrypt_password)

Uses pikepdf if available; skips gracefully if not installed.
"""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from .pipeline_util import log

try:
    import pikepdf
except ImportError:
    pikepdf = None  # type: ignore[assignment]


def _is_encrypted(pdf_path: str) -> bool:
    """Check if a PDF requires a password to open."""
    if pikepdf is None:
        return False
    try:
        with pikepdf.open(pdf_path):
            return False  # Opens fine — not encrypted (or owner-only)
    except pikepdf._core.PasswordError:
        return True
    except Exception:
        return False


def _try_decrypt(pdf_path: str, password: str) -> Optional[str]:
    """Try to open *pdf_path* with *password* via pikepdf.

    Returns the password on success, None on failure.
    """
    if pikepdf is None:
        return None
    try:
        with pikepdf.open(pdf_path, password=password):
            return password
    except pikepdf._core.PasswordError:
        return None
    except Exception:
        return None


def _save_decrypted(pdf_path: str, password: str, dest: str) -> None:
    """Open with *password* and save an unencrypted copy to *dest*."""
    with pikepdf.open(pdf_path, password=password) as pdf:
        pdf.save(dest)


@contextmanager
def maybe_decrypt(
    pdf_path: str,
    password: Optional[str] = None,
) -> Generator[str, None, None]:
    """Context manager that yields a path to a readable (decrypted) PDF.

    If the PDF is not encrypted, yields the original path unchanged.
    If encrypted and decryptable, yields a temp file that is cleaned up on exit.
    If encrypted and NOT decryptable, raises ``RuntimeError``.

    Args:
        pdf_path: Path to the input PDF.
        password: Optional user-provided password.

    Yields:
        Path to use for downstream extraction (original or temp decrypted).
    """
    if pikepdf is None:
        # Can't detect encryption without pikepdf — pass through.
        yield pdf_path
        return

    if not _is_encrypted(pdf_path):
        yield pdf_path
        return

    # --- PDF is encrypted; try to decrypt ---
    log(f"PDF is encrypted: {Path(pdf_path).name}")

    # Strategy 1: empty password (permissions-only / owner-password encryption)
    found_pw = _try_decrypt(pdf_path, "")

    # Strategy 2: user-provided password
    if found_pw is None and password:
        found_pw = _try_decrypt(pdf_path, password)

    if found_pw is None:
        raise RuntimeError(
            f"Cannot decrypt {Path(pdf_path).name}. "
            "Provide a password via PipelineConfig.decrypt_password "
            "or decrypt the file manually before extraction."
        )

    # Save decrypted copy to a temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        _save_decrypted(pdf_path, found_pw, tmp_path)
        log(f"Decrypted with {'empty' if found_pw == '' else 'user-provided'} password → temp file")
        yield tmp_path
    finally:
        Path(tmp_path).unlink(missing_ok=True)
