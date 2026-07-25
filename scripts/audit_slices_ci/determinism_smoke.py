#!/usr/bin/env python3
"""CI determinism smoke: 3 fresh-process extractions of a bundled PDF
must produce identical canonical output hashes. Runs BEFORE fixtures."""
import hashlib, json, subprocess, sys, os

PDF = os.environ.get("SMOKE_PDF", "tests/fixtures/1.pdf")
WORKER = (
    "import json,sys,hashlib,re,collections\n"
    "import pdf_oxide\n"
    "res = pdf_oxide.extract_pdf(sys.argv[1])\n"
    "pages = collections.defaultdict(list)\n"
    "for b in res.blocks: pages[b['page']].append((b['type'], re.sub(r'\\s+','',b['text'] or '')))\n"
    "for t in res.tables: pages[t['page']].append(('T', re.sub(r'\\s+','',' '.join(str(c) for r in t.get('data',[]) for c in r))))\n"
    "blob = json.dumps({str(k): sorted(v) for k, v in pages.items()}, sort_keys=True)\n"
    "print(hashlib.sha256(blob.encode()).hexdigest())\n"
)
py = os.environ.get("SMOKE_PYTHON", sys.executable)
hashes = []
for i in range(3):
    r = subprocess.run([py, "-c", WORKER, PDF], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print("worker failed:", r.stderr[-300:]); sys.exit(2)
    h = r.stdout.strip().splitlines()[-1]
    hashes.append(h)
    print(f"run{i}: {h}")
sys.exit(0 if len(set(hashes)) == 1 else 1)
