#!/usr/bin/env python3
"""
Build script (`make web`) that bundles `paragon/*`, sample builds, and `web/*`
into a single self-contained static HTML application (`dist/index.html`).
"""

import ast
import base64
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paragon import ParagonBoard, load_meta_data, unwrap_paragon_boards  # noqa: E402

PARAGON_DIR = ROOT / "paragon"
WEB_DIR = ROOT / "web"
DIST_DIR = ROOT / "dist"

CLASS_DISPLAY_NAMES = {
    "Barb": "Barbarian",
    "Druid": "Druid",
    "Necro": "Necromancer",
    "Paladin": "Paladin",
    "Rogue": "Rogue",
    "Sorc": "Sorcerer",
    "Spirit": "Spiritborn",
    "Warlock": "Warlock",
}


def strip_python_source(src: str) -> str:
    """Strip comments and docstrings from Python source via AST round-tripping."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
                if not node.body:
                    node.body.append(ast.Pass())
    return ast.unparse(tree)


def minify_asset(text: str, loader: str) -> str:
    """Minify JS or CSS via `esbuild` (falling back to rjsmin/rcssmin if unavailable)."""
    if shutil.which("esbuild"):
        out = subprocess.check_output(
            ["esbuild", "--minify", f"--loader={loader}"],
            input=text.encode("utf-8"),
        )
        return out.decode("utf-8").strip()
    if loader == "js":
        try:
            import rjsmin
            return rjsmin.jsmin(text)
        except ImportError:
            pass
    elif loader == "css":
        try:
            import rcssmin
            return rcssmin.cssmin(text)
        except ImportError:
            pass
    return text


def build_paragon_zip_b64() -> str:
    """Pack stripped `paragon/*.py` and `paragon/paragon_meta_data.json` into a deflated base64 zip."""
    buf = io.BytesIO()
    py_files = [
        "__init__.py",
        "board.py",
        "game_data.py",
        "hints.py",
        "solver.py",
        "visualize.py",
    ]
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZipFile and zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for fname in py_files:
            raw_py = (PARAGON_DIR / fname).read_text(encoding="utf-8")
            zf.writestr(f"paragon/{fname}", strip_python_source(raw_py))
        zf.write(PARAGON_DIR / "paragon_meta_data.json", arcname="paragon/paragon_meta_data.json")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def load_sample_builds() -> tuple[dict, list[dict]]:
    """Load valid 5-board builds from `tests/builds/*.json` grouped by class."""
    meta = load_meta_data()
    samples: dict[str, list] = {}
    by_class: dict[str, list[dict]] = {cls_label: [] for cls_label in CLASS_DISPLAY_NAMES.values()}

    builds_dir = ROOT / "tests" / "builds"
    if builds_dir.exists():
        for path in sorted(builds_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                boards = unwrap_paragon_boards(raw)
                parsed = [ParagonBoard(entry, meta) for entry in boards]
                active = [b for b in parsed if b.target_nodes or b.relaxed_nodes or b.is_starter_board()]
                if len(active) != 5:
                    continue
            except Exception:
                continue

            raw_cls = boards[0]["id"].split("_")[1]
            cls_label = CLASS_DISPLAY_NAMES.get(raw_cls, raw_cls)
            name = (raw.get("name") if isinstance(raw, dict) else None) or path.stem.replace("_", " ")
            name = " ".join(str(name).split())
            key = path.stem
            samples[key] = boards
            by_class.setdefault(cls_label, []).append({
                "key": key,
                "label": name,
            })

    catalog = []
    for cls_label in sorted(by_class.keys()):
        items = by_class[cls_label]
        if items:
            items.sort(key=lambda it: it["label"].lower())
            catalog.append({"class_label": cls_label, "items": items})

    return samples, catalog


def safe_js_json(obj) -> str:
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def main() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_b64 = build_paragon_zip_b64()
    samples, catalog = load_sample_builds()

    css_text = minify_asset((WEB_DIR / "styles.css").read_text(encoding="utf-8"), "css")
    worker_raw = (WEB_DIR / "worker.js").read_text(encoding="utf-8")
    worker_raw = re.sub(
        r"(const PYTHON_BRIDGE_CODE = `)([\s\S]*?)(`;)",
        lambda m: m.group(1) + strip_python_source(m.group(2)) + m.group(3),
        worker_raw,
        count=1,
    )
    worker_src = minify_asset(worker_raw, "js")
    app_src = minify_asset((WEB_DIR / "app.js").read_text(encoding="utf-8"), "js")
    html_template = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    samples_bytes = json.dumps(samples, separators=(",", ":")).encode("utf-8")
    samples_b64 = base64.b64encode(zlib.compress(samples_bytes, level=9)).decode("ascii")

    js_payload = "\n".join([
        f"window.__PARAGON_ZIP_B64__={safe_js_json(zip_b64)};",
        f"window.__WORKER_SRC__={safe_js_json(worker_src)};",
        f"window.__SAMPLE_BUILDS_B64__={safe_js_json(samples_b64)};",
        f"window.__SAMPLE_CATALOG__={safe_js_json(catalog)};",
        app_src,
    ])

    final_html = (
        html_template
        .replace("/*__INLINE_CSS__*/", css_text)
        .replace("/*__INLINE_JS_PAYLOAD__*/", js_payload)
    )

    out_path = DIST_DIR / "index.html"
    out_path.write_text(final_html, encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024.0
    print(f"Built single-file webapp: {out_path} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
