from __future__ import annotations

from typing import Any, Callable

TauSubagent = Callable[[str, dict[str, Any]], dict[str, Any]]
Save = Callable[[str, dict[str, Any]], None]


def tau_subagent_while_loop(
    *,
    tau_subagent: TauSubagent,
    save: Save,
    max_attempts: int = 3,
) -> dict[str, Any]:
    prior_review: dict[str, Any] | None = None
    attempt = 1
    while attempt <= max_attempts:
        patch = tau_subagent("coder", {"attempt": attempt, "prior_review": prior_review})
        save("patch", patch)
        if not patch.get("ok"):
            return {"status": "blocked_substrate", "attempt": attempt}

        review = tau_subagent("reviewer", {"attempt": attempt, "patch": patch})
        save("review", review)
        if review["status"] == "pass":
            return {"status": "reviewer_passed", "attempt": attempt}
        if review["status"] in {"blocked_substrate", "human_needed"}:
            return {"status": review["status"], "attempt": attempt}

        prior_review = review
        attempt += 1

    return {"status": "still_open", "attempt": max_attempts}

