# Relevance re-test of every open ticket — `origin/main` @ 217a1e6c, 2026-08-02

Each ticket's own predicate was re-run against current `main` using a wheel
built from that commit, driving the real source PDF the ticket names. The
question asked was only: **does the described defect still reproduce?**

Result: **all 7 reproduce. None is stale. Nothing was closed.**

## #2 — page-45 annotation bugs — LIVE (regressed)

All six boxes ticked with 2026-06-04 receipts. Re-run: items 2, 3, 5, 6 hold;
**item 1 regressed**. The merged AC-1 control list is back to 8 separate list
regions where the receipt recorded exactly 1 (`actual:p45:block:10`).

## #3 — nested list segmentation under AC-2 h — LIVE (changed shape)

Source: `NIST_SP_800-53r5.pdf` sha `fc63bcd6…`, page 45, `classify_blocks`.

The *originally described* defect is gone — `h.` no longer contains its
numbered children. But the ticket's expected shape is four separate list items,
and none of the four materialises:

```
[10] List len=394  'e. Require approvals ... f. Create, enable ... g. Monitor ... h. Notify account managers ... within:'
[11] List len=294  '1. [Assignment: ...] when accounts are no longer required; 2. ...; 3. ...'
```

`h.` is merged with `e/f/g`; `1/2/3` are merged with each other. The ticket's
own "nearby expected example" also no longer holds: `i.` is its own block [12],
but its children are merged into one block [13], not blocks 20/21/22.

Item-level segmentation is unmet. Keep open.

## #6 — flowchart shredded on 800-53Ar5 p46 — LIVE

Source: `NIST.SP.800-53Ar5.pdf` sha `75665570…`, page index 46 (2552 chars).
Of the five texts the ticket says survive nowhere, **three are still absent**:

```
Pre-Assessment                                              present
Review assessor findings                                    present
Ensure assessment plan is appropriately tailored            MISSING
Notify key organizational officials of impending assessment MISSING
Plans of Action and Milestones                              MISSING
```

Content loss confirmed. This is the S1 on the list. Keep open.

## #15 / #17 — paste / ttf-parser — LIVE but not actionable here

Both still reported by `cargo audit` (3 vulnerabilities remain). Both are
`unmaintained` with `Patched Versions: n/a`. `paste` is purely transitive;
`ttf-parser` is used directly *and* pulled by `fontdb`. Detailed evidence
commented on each. Keep open.

## #20 — no bbox_space stamped — LIVE

`contracts/bbox_space_v1.schema.json` exists, and
`contracts/annotation_decision_event_v1.schema.json` references it. But no
produced artifact carries the field:

```
artifacts/pdf-lab/annotation-calls/NASA_SP-2016-6105/annotation_call.json   bbox_space: absent
artifacts/pdf_lab/issue2_reverify_20260802/after_snapshot.json             bbox_space: absent
```

The contract exists; nothing emits it. Keep open.

## #21 — U+F0B7 + run-in heading — LIVE (reproduces exactly)

First tested against the wrong document. The ticket's artifact records
`pdf_sha256 b8e28d12…`, which is
`/mnt/storage12tb/extractor_corpus/engineering/12 NASA_SP-2016-6105 Rev 2.pdf`,
not the `8eeb4887…` copy in `inbox/government/`. Re-run against the correct
document, page 19:

```
block 2  type=Body  pua=1  ['U+F0B7']
  ' Technical Management Processes: The technical management processes are used to ...'
```

Every element of the ticket reproduces: the raw U+F0B7 codepoint, the `Body`
classification, and the bold run-in heading merged into the body run. 71 PUA
codepoints across pages 0-39. Keep open.

## does_not_prove

These runs establish only that each described defect is still present. They do
not diagnose root causes, do not establish when #2 item 1 regressed, and do not
assess exploitability for #15/#17. The #20 check sampled two artifacts, not
every artifact type the ticket lists.
