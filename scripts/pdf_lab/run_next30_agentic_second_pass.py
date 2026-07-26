#!/usr/bin/env python3
"""Run agentic visual second-pass review over the NIST next-30 packet.

Each page gets a fresh release extraction snapshot, a refreshed bbox overlay,
the exact prompt payload, raw model response, parsed JSON review, and local
validation result. The batch summary is deliberately evidence-first: it does not
claim closure from model text alone.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
SCILLM_URL = "http://localhost:4001"
CALLER = "pdf-oxide-next30-agentic-second-pass"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_scillm_key_from_docker() -> str | None:
    if shutil.which("docker") is None:
        return None
    try:
        ps = subprocess.run(
            ["docker", "ps", "--filter", "name=scillm-proxy", "--format", "{{.Names}}"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for container in [line.strip() for line in ps.stdout.splitlines() if line.strip()]:
        try:
            key = subprocess.run(
                ["docker", "exec", container, "printenv", "SCILLM_MASTER_KEY"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if key.returncode == 0 and key.stdout.strip():
            return key.stdout.strip()
    return None


def resolve_scillm_api_key() -> dict[str, str | None]:
    for name in ("SCILLM_API_KEY", "SCILLM_MASTER_KEY", "LITELLM_MASTER_KEY", "MASTER_KEY"):
        value = os.environ.get(name)
        if value:
            return {"api_key": value, "source": f"env:{name}"}
    docker_key = read_scillm_key_from_docker()
    if docker_key:
        return {"api_key": docker_key, "source": "docker:scillm-proxy:SCILLM_MASTER_KEY"}
    proxy_key = os.environ.get("SCILLM_PROXY_KEY")
    if proxy_key:
        return {"api_key": proxy_key, "source": "env:SCILLM_PROXY_KEY"}
    return {"api_key": None, "source": "unavailable"}


def image_part(path: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def block_summary(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for block in blocks:
        out.append({
            "id": block.get("id"),
            "page": block.get("page"),
            "type": block.get("type") or block.get("blockType"),
            "source_type": block.get("source_type"),
            "semantic_role": block.get("semantic_role"),
            "parent_id": block.get("parent_id"),
            "label": block.get("label"),
            "target_page": block.get("target_page"),
            "dot_leader": block.get("dot_leader"),
            "bbox": block.get("bbox"),
            "text": str(block.get("text") or ""),
        })
    return out


def render_overlay(page_png: Path, blocks: list[dict[str, Any]], out_path: Path) -> None:
    img = Image.open(page_png).convert("RGBA")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:
        font = None
    colors = {
        "header_footer_noise": (128, 128, 128, 175),
        "running_header": (128, 128, 128, 175),
        "running_footer": (128, 128, 128, 175),
        "paragraph_block": (20, 170, 80, 210),
        "list": (50, 120, 220, 210),
        "section_heading": (20, 90, 220, 210),
        "reference": (160, 70, 210, 210),
        "table": (230, 40, 40, 230),
        "footnote": (245, 170, 25, 230),
    }
    width, height = img.size

    def draw_box(bbox: list[Any], color: tuple[int, int, int, int], label: str, *, line_width: int = 3) -> None:
        if len(bbox) != 4:
            return
        x0, y0, x1, y1 = [float(v) for v in bbox]
        box = [x0 * width, y0 * height, x1 * width, y1 * height]
        draw.rectangle(box, outline=color, width=line_width)
        text_width = draw.textlength(label, font=font) if font else len(label) * 7
        label_y0 = max(0, box[1] - 14)
        draw.rectangle([box[0], label_y0, box[0] + text_width + 4, box[1]], fill=color)
        draw.text((box[0] + 2, label_y0), label, fill=(0, 0, 0, 255), font=font)

    for block in blocks:
        bbox = block.get("bbox") or []
        color = colors.get(str(block.get("type") or block.get("blockType")), (245, 170, 25, 220))
        label = f"{str(block.get('id', '')).split(':')[-1]}:{block.get('type') or block.get('blockType')}"
        draw_box(bbox, color, label)
        if (block.get("type") or block.get("blockType")) != "table":
            continue
        rows = ((block.get("raw") or {}).get("rows") or [])
        for row_index, row in enumerate(rows):
            cells = row.get("cells") if isinstance(row, dict) else []
            if not isinstance(cells, list):
                continue
            for column_index, cell in enumerate(cells):
                if not isinstance(cell, dict):
                    continue
                cell_bbox = cell.get("bbox") or []
                if len(cell_bbox) != 4:
                    continue
                role = cell.get("role") or "table_cell"
                draw_box(
                    cell_bbox,
                    (20, 160, 220, 235),
                    f"r{row_index}c{column_index}:{role}",
                    line_width=2,
                )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path)


def current_extraction(pdf: Path, ledger: Path, page: int) -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import snapshot_current_extraction as snap  # noqa: PLC0415

    return snap._extract_page(pdf, page - 1, ledger, "release")


def prompt_for_page(page: int, blocks: list[dict[str, Any]], packet_question: str) -> str:
    counts = dict(sorted(Counter(block.get("type") for block in blocks).items()))
    payload = {
        "page": page,
        "block_count": len(blocks),
        "counts": counts,
        "blocks": block_summary(blocks),
        "packet_question": packet_question,
    }
    return f"""You are a PDF Lab agentic second-pass reviewer for pdf_oxide.

Inspect the clean page image and the current bbox overlay image. Compare the
visible page structure against the current deterministic extraction JSON.

Review scope:
- body text vs lists vs section headings;
- tables and table false positives;
- references and reference continuation lines;
- footnotes;
- running headers, footers, side DOI/publication chrome, and page numbers.

Rules:
- Use the images as the authority for visible layout.
- Use block ids and bbox coordinates only from the JSON payload.
- Do not propose page-specific literal hacks.
- Return actionable findings only when a core extractor or NIST preset/ledger
  patch is justified.
- If a page is visually acceptable, return an empty findings array and explain
  why in reviewed_summary.
- Do not claim closure; this is a review result for downstream validation.

Current extraction payload:
{json.dumps(payload, indent=2, sort_keys=True)}

Field note:
- `type` and `semantic_role` are the current materialized extraction
  classification after deterministic presets.
- `source_type` is raw extractor provenance before ledger/preset application;
  do not report a defect solely because `source_type` differs from `type` when
  the final `type` and `semantic_role` are correct.

Return one valid JSON object with this exact schema and no surrounding prose:
{{
  "schema": "pdf_oxide.next30_agentic_second_pass.page_review.v1",
  "page": {page},
  "reviewed": true,
  "reviewed_summary": "short visual/extraction assessment",
  "findings": [
    {{
      "finding_id": "stable short id",
      "severity": "low | medium | high",
      "block_ids": ["actual block id strings"],
      "visible_evidence": "what the image/overlay shows",
      "extraction_defect": "what current extraction gets wrong",
      "expected_type_or_action": "expected extraction family or action",
      "recommended_owner": "pdf_oxide_core | nist_preset_ledger | materializer | second_pass_prompt | human_review",
      "patch_hint": "short recommendation only",
      "confidence": "low | medium | high"
    }}
  ],
  "human_needed": [
    {{
      "block_ids": ["actual block id strings"],
      "reason": "why model cannot adjudicate this visually"
    }}
  ],
  "overall_confidence": "low | medium | high"
}}
"""


def validate_review(page: int, review: Any, block_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(review, dict):
        return ["review is not an object"]
    if review.get("schema") != "pdf_oxide.next30_agentic_second_pass.page_review.v1":
        errors.append("schema mismatch")
    if int(review.get("page", -1)) != page:
        errors.append("page mismatch")
    if review.get("reviewed") is not True:
        errors.append("reviewed must be true")
    if not isinstance(review.get("findings"), list):
        errors.append("findings must be a list")
    if not isinstance(review.get("human_needed"), list):
        errors.append("human_needed must be a list")
    for index, finding in enumerate(review.get("findings") or []):
        if not isinstance(finding, dict):
            errors.append(f"finding {index} is not an object")
            continue
        if finding.get("severity") not in {"low", "medium", "high"}:
            errors.append(f"finding {index} invalid severity")
        if finding.get("recommended_owner") not in {
            "pdf_oxide_core",
            "nist_preset_ledger",
            "materializer",
            "second_pass_prompt",
            "human_review",
        }:
            errors.append(f"finding {index} invalid recommended_owner")
        ids = finding.get("block_ids")
        if not isinstance(ids, list):
            errors.append(f"finding {index} block_ids must be list")
            continue
        for block_id in ids:
            if str(block_id) not in block_ids:
                errors.append(f"finding {index} unknown block id {block_id}")
    return errors


async def review_page(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    model: str,
    api_key: str,
    batch_id: str,
    packet_root: Path,
    out_root: Path,
    pdf: Path,
    ledger: Path,
    entry: dict[str, Any],
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    page = int(entry["page"])
    page_out = out_root / "pages" / f"page_{page:04d}"
    page_out.mkdir(parents=True, exist_ok=True)
    source_page_png = packet_root / entry["dir"] / "page.png"
    page_png = page_out / "page.png"
    shutil.copy2(source_page_png, page_png)

    extraction = current_extraction(pdf, ledger, page)
    blocks = extraction.get("blocks") or []
    write_json(page_out / "release_extraction_blocks.json", extraction)
    overlay = page_out / "bbox_overlay_current.png"
    render_overlay(page_png, blocks, overlay)

    prompt = prompt_for_page(page, blocks, str(entry.get("question") or ""))
    (page_out / "prompt.txt").write_text(prompt, encoding="utf-8")
    request = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": "Clean page image:"},
                    image_part(page_png),
                    {"type": "text", "text": "Current bbox overlay image:"},
                    image_part(overlay),
                ],
            }
        ],
        "response_format": {"type": "json_object"},
        "reasoning_effort": "high",
        "scillm_metadata": {"batch_id": batch_id, "item_id": f"page_{page:04d}"},
    }
    write_json(
        page_out / "request.manifest.json",
        {**request, "messages": "[omitted: see prompt.txt and image files]"},
    )

    started = utc_now()
    raw: dict[str, Any] = {"ok": False, "error": "not attempted"}
    attempts: list[dict[str, Any]] = []
    async with semaphore:
        for attempt in range(1, retries + 2):
            await wait_for_scillm_health(client, timeout_s=60.0)
            try:
                response = await client.post(
                    f"{SCILLM_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "X-Caller-Skill": CALLER,
                    },
                    json=request,
                    timeout=timeout,
                )
                raw = {
                    "ok": response.status_code == 200,
                    "status_code": response.status_code,
                    "headers": {
                        key: value
                        for key, value in response.headers.items()
                        if key.lower().startswith("x-scillm") or key.lower() in {"x-cost-usd"}
                    },
                    "body": response.json()
                    if response.headers.get("content-type", "").startswith("application/json")
                    else response.text,
                }
            except Exception as exc:  # noqa: BLE001
                raw = {"ok": False, "error": repr(exc)}
            attempts.append({"attempt": attempt, "ok": bool(raw.get("ok")), "error": raw.get("error")})
            write_json(page_out / f"response.raw.attempt{attempt}.json", raw)
            if raw.get("ok"):
                break
            await asyncio.sleep(4.0)
    finished = utc_now()
    raw["attempts"] = attempts
    write_json(page_out / "response.raw.json", raw)

    parsed: Any = None
    parse_error = ""
    validation_errors: list[str] = []
    if raw.get("ok"):
        try:
            content = raw["body"]["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            write_json(page_out / "model_review.json", parsed)
            validation_errors = validate_review(page, parsed, {str(block.get("id")) for block in blocks})
        except Exception as exc:  # noqa: BLE001
            parse_error = repr(exc)
            validation_errors = [parse_error]
    else:
        validation_errors = [str(raw.get("error") or raw.get("body") or "raw call failed")[:1000]]

    validation = {
        "page": page,
        "ok": not validation_errors,
        "error_count": len(validation_errors),
        "errors": validation_errors,
        "finding_count": len((parsed or {}).get("findings") or []) if isinstance(parsed, dict) else 0,
        "human_needed_count": len((parsed or {}).get("human_needed") or []) if isinstance(parsed, dict) else 0,
    }
    write_json(page_out / "validation.json", validation)
    manifest = {
        "page": page,
        "model": model,
        "started_at": started,
        "finished_at": finished,
        "raw_ok": bool(raw.get("ok")),
        "ok": bool(validation["ok"]),
        "parse_error": parse_error,
        "finding_count": validation["finding_count"],
        "human_needed_count": validation["human_needed_count"],
        "paths": {
            "page": str(page_png),
            "overlay": str(overlay),
            "prompt": str(page_out / "prompt.txt"),
            "response_raw": str(page_out / "response.raw.json"),
            "model_review": str(page_out / "model_review.json") if parsed else None,
            "validation": str(page_out / "validation.json"),
        },
    }
    write_json(page_out / "call_manifest.json", manifest)
    return manifest


async def wait_for_scillm_health(client: httpx.AsyncClient, timeout_s: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error = ""
    while asyncio.get_running_loop().time() < deadline:
        try:
            response = await client.get(f"{SCILLM_URL}/health", timeout=5.0)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last_error = f"status={response.status_code} body={response.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
        await asyncio.sleep(2.0)
    raise RuntimeError(f"scillm health did not recover within {timeout_s}s: {last_error}")


async def run(args: argparse.Namespace) -> int:
    manifest = read_json(args.packet / "manifest.json")
    pages = manifest["pages"]
    if args.pages:
        wanted = set(args.pages)
        pages = [entry for entry in pages if int(entry["page"]) in wanted]
    batch_id = args.batch_id or f"next30-agentic-second-pass-{datetime.now().strftime('%Y%m%dT%H%M%SZ')}"
    args.out.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient() as client:
        health = await client.get(f"{SCILLM_URL}/health", timeout=5)
        health.raise_for_status()
        key_info = resolve_scillm_api_key()
        api_key = key_info.get("api_key")
        if not api_key:
            write_json(
                args.out / "scillm_preflight.json",
                {
                    "health": health.json(),
                    "auth": {"ok": False, "error": "missing_scillm_api_key"},
                    "auth_source": key_info.get("source"),
                },
            )
            raise RuntimeError("missing Scillm API key; set SCILLM_API_KEY or ensure scillm-proxy exposes SCILLM_MASTER_KEY")
        auth = await client.get(
            f"{SCILLM_URL}/v1/scillm/auth",
            headers={"Authorization": f"Bearer {api_key}", "X-Caller-Skill": CALLER},
            timeout=10,
        )
        auth_body = auth.json() if auth.headers.get("content-type", "").startswith("application/json") else auth.text
        write_json(
            args.out / "scillm_preflight.json",
            {
                "health": health.json(),
                "auth": {"status_code": auth.status_code, "body": auth_body},
                "auth_source": key_info.get("source"),
            },
        )
        auth.raise_for_status()
        manifests: list[dict[str, Any]] = []
        semaphore = asyncio.Semaphore(args.concurrency)
        if args.concurrency == 1:
            for entry in pages:
                item = await review_page(
                    client,
                    semaphore,
                    model=args.model,
                    api_key=api_key,
                    batch_id=batch_id,
                    packet_root=args.packet,
                    out_root=args.out,
                    pdf=Path(manifest["pdf"]),
                    ledger=Path(manifest["ledger"]),
                    entry=entry,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                manifests.append(item)
                print(json.dumps(item, sort_keys=True), flush=True)
        else:
            tasks = [
                asyncio.create_task(
                    review_page(
                        client,
                        semaphore,
                        model=args.model,
                        api_key=api_key,
                        batch_id=batch_id,
                        packet_root=args.packet,
                        out_root=args.out,
                        pdf=Path(manifest["pdf"]),
                        ledger=Path(manifest["ledger"]),
                        entry=entry,
                        timeout=args.timeout,
                        retries=args.retries,
                    )
                )
                for entry in pages
            ]
            for task in asyncio.as_completed(tasks):
                item = await task
                manifests.append(item)
                print(json.dumps(item, sort_keys=True), flush=True)

    findings: list[dict[str, Any]] = []
    human_needed: list[dict[str, Any]] = []
    for item in sorted(manifests, key=lambda x: x["page"]):
        review_path = item["paths"].get("model_review")
        if not review_path:
            continue
        review = read_json(Path(review_path))
        for finding in review.get("findings") or []:
            findings.append({"page": item["page"], **finding})
        for need in review.get("human_needed") or []:
            human_needed.append({"page": item["page"], **need})

    summary = {
        "schema": "pdf_oxide.next30_agentic_second_pass.summary.v1",
        "batch_id": batch_id,
        "model": args.model,
        "created_at": utc_now(),
        "packet": str(args.packet),
        "page_count": len(pages),
        "ok_count": sum(1 for item in manifests if item.get("ok")),
        "raw_ok_count": sum(1 for item in manifests if item.get("raw_ok")),
        "finding_count": len(findings),
        "human_needed_count": len(human_needed),
        "manifests": sorted(manifests, key=lambda x: x["page"]),
        "findings": findings,
        "human_needed": human_needed,
    }
    write_json(args.out / "summary.json", summary)
    return 0 if summary["ok_count"] == len(pages) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--packet",
        type=Path,
        default=REPO / "artifacts/pdf_lab/project_agent_hardening/next30_review_packet_20260601Tnow",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "artifacts/pdf_lab/project_agent_hardening/next30_agentic_second_pass_20260601Tnow",
    )
    parser.add_argument("--pages", type=int, nargs="*", default=[])
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--batch-id", default="")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
