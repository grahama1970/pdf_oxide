from __future__ import annotations

from typing import Any, Callable

Step = Callable[..., dict[str, Any]]


def page46_while_loop(
    *,
    extract: Step,
    reviewer_subagent: Step,
    coder_subagent: Step,
    validate: Step,
    save: Step,
    max_attempts: int = 3,
) -> dict[str, Any]:
    code_root = save("prepare_code_root", {})["code_root"]
    attempt = 1
    while attempt <= max_attempts:
        evidence = extract(code_root=code_root, attempt=attempt)
        review = reviewer_subagent(evidence=evidence, attempt=attempt)
        save("review", review)
        if review["status"] == "pass":
            return {"status": "reviewer_passed", "attempt": attempt}
        if review["status"] in {"blocked_substrate", "human_needed"}:
            return {"status": review["status"], "attempt": attempt}

        patch = coder_subagent(code_root=code_root, evidence=evidence, review=review, attempt=attempt)
        save("patch", patch)
        if not patch.get("ok"):
            return {"status": "blocked_substrate", "attempt": attempt}

        validation = validate(code_root=code_root, patch=patch, attempt=attempt)
        save("validation", validation)
        attempt += 1

    return {"status": "still_open", "attempt": max_attempts}

