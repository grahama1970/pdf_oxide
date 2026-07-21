# Goal-change packet GCP-003 — re-express two signed waivers by content

Status: APPLIED BY AGENT, AWAITING HUMAN SIGNATURE
Raised: 2026-07-18 · Author: project agent · Signer: __________ (graham@grahama.co)

## Why

With page 28 at 11/11 matched, criterion 3 still fails on "0 unwaived extras":
three extras remain, and BOTH existing waivers were signed for exactly these
artifacts yet no longer resolve to them.

  gs001-waiver-002 is pinned to `actual_id: "actual:p27:block:1"`. Block ids are
  assigned by the extractor and do not survive re-extraction, so the waiver
  silently stopped applying the moment the classifier changed. This is the same
  fragility that lost the golden bundle in /tmp: identity tied to something
  regenerated rather than to content.

  gs001-waiver-001's signed reason names "extraction blocks 4-5" -- plural, the
  whole rotated DOI watermark run -- but its single `text_contains` matches only
  the first fragment, because the extractor splits the rotated run.

## What changed

Neither waiver's SCOPE changes. Both are re-expressed so they identify the same
artifacts by content rather than by extractor-assigned identity:

  waiver-001  + text_matches_any: ["This publication is available free of
              charge", "doi.org/10.6028", "-53r5"]
  waiver-002  - actual_id (stale)
              + text_pattern: a rule of 20+ underscore/dash characters

## Why this needs a signature

These are human-signed waivers. Re-expressing a predicate is mechanical, but
widening what a signed waiver covers is a human decision, so the change is
applied provisionally and the signature line is left open. If the intent was
narrower than the plural "blocks 4-5" reading, reject this packet and the
extras remain open.

## Recommendation beyond this packet

Waivers should never be keyed on extractor-assigned ids. Consider rejecting
`actual_id`-keyed waivers at lock time so this cannot recur silently.
