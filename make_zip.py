"""Build the submission ZIP: dist/ocd-eval.zip (code, tasks, references, results, transcripts).
Excludes .env, logs, caches and anything listed in .gitignore. Fails if a key-like string is found."""
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXCLUDE_DIRS = {"__pycache__", "dist", ".git"}
EXCLUDE_FILES = {".env"}
EXCLUDE_SUFFIX = {".log", ".pyc", ".mp4", ".mov"}
KEY_PATTERN = re.compile(r"(AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{30,}|sk-ant-[0-9A-Za-z_\-]{20,})")

out = ROOT / "dist" / "ocd-eval.zip"
out.parent.mkdir(exist_ok=True)
n = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_dir() or set(rel.parts) & EXCLUDE_DIRS or p.name in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIX:
            continue
        text = p.read_bytes().decode("utf-8", "ignore")
        if KEY_PATTERN.search(text):
            raise SystemExit(f"Refusing to zip: key-like string found in {rel}")
        z.write(p, Path("ocd-eval") / rel)
        n += 1
print(f"wrote {out} ({n} files, {out.stat().st_size/1e6:.2f} MB)")
