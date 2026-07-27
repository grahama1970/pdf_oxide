Additional live evidence from the follow-up reduced competition:

- Command family: `skills/ask/run.sh compete <pdf-oxide page456 compact request> --handler webgpt --handler webgemini --browser-tab-lifecycle fresh-temporary --execute --poll-timeout-seconds 900 --poll-interval-seconds 5 --json`
- Run directory: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_retry2-working-lanes-20260727T150546Z/pdf-oxide-creator-reviewer-mvp-page456-retry2-working-lanes-20260727T150546Z`
- Result: `status=BLOCKED`, `blocked_reason=browser_tab_lifecycle_failed`, `failure_code=browser_window_create_failed`
- Receipt: `browser-tab-lifecycle.json`
- Specific failing command: `/home/graham/workspace/experiments/agent-skills_issue1029_runtime/skills/surf/run.sh window.new https://chatgpt.com/ --json --unfocused`
- Observed: command timed out after 60.064 seconds with return code 124 before Tau execution launched.

This is the same fresh browser lifecycle failure family as the original #1032 report: the Ask compete runtime cannot reliably create or bound owned browser resources and therefore cannot reliably emit a terminal join scorecard for the pdf-oxide competition.
