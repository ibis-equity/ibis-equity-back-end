#!/usr/bin/env python3
"""Convert vector images using Python.

Primary path: SVG -> normalized SVG using svgwrite.
Optional path: SVG -> PNG using cairosvg after normalization.
"""

from __future__ import annotations

import argparse
import shutil
import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import svgwrite


SUPPORTED_VECTOR_EXTS = {".svg", ".pdf"}
SKIP_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _copy_style_attrs(src: ET.Element) -> dict:
    allowed = {
        "fill",
        "fill-opacity",
        "stroke",
        "stroke-opacity",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-dashoffset",
        "opacity",
        "transform",
        "id",
        "class",
        "style",
    }
    out = {}
    for k, v in src.attrib.items():
        key = _strip_ns(k)
        if key in allowed and v:
            out[key] = v
    if "id" in out:
        out["id"] = _sanitize_id(out["id"])
    return out


def _sanitize_id(value: str) -> str:
    # SVG id values cannot contain spaces or most punctuation.
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip())
    cleaned = cleaned.strip("-")
    if not cleaned:
        return "shape"
    if cleaned[0].isdigit():
        return f"id-{cleaned}"
    return cleaned


def _add_children(parent_xml: ET.Element, parent_dwg):
    for child in parent_xml:
        tag = _strip_ns(child.tag).lower()
        style = _copy_style_attrs(child)

        node = None
        if tag == "g":
            node = svgwrite.container.Group(**style)
            parent_dwg.add(node)
            _add_children(child, node)
            continue

        if tag == "path" and child.attrib.get("d"):
            node = svgwrite.path.Path(d=child.attrib.get("d"), **style)
        elif tag == "rect":
            node = svgwrite.shapes.Rect(
                insert=(child.attrib.get("x", "0"), child.attrib.get("y", "0")),
                size=(child.attrib.get("width", "0"), child.attrib.get("height", "0")),
                rx=child.attrib.get("rx"),
                ry=child.attrib.get("ry"),
                **style,
            )
        elif tag == "circle":
            node = svgwrite.shapes.Circle(
                center=(child.attrib.get("cx", "0"), child.attrib.get("cy", "0")),
                r=child.attrib.get("r", "0"),
                **style,
            )
        elif tag == "ellipse":
            node = svgwrite.shapes.Ellipse(
                center=(child.attrib.get("cx", "0"), child.attrib.get("cy", "0")),
                r=(child.attrib.get("rx", "0"), child.attrib.get("ry", "0")),
                **style,
            )
        elif tag == "line":
            node = svgwrite.shapes.Line(
                start=(child.attrib.get("x1", "0"), child.attrib.get("y1", "0")),
                end=(child.attrib.get("x2", "0"), child.attrib.get("y2", "0")),
                **style,
            )
        elif tag == "polyline":
            points = child.attrib.get("points", "")
            node = svgwrite.shapes.Polyline(points=points, **style)
        elif tag == "polygon":
            points = child.attrib.get("points", "")
            node = svgwrite.shapes.Polygon(points=points, **style)
        elif tag == "text":
            text_value = "".join(child.itertext())
            node = svgwrite.text.Text(
                text_value,
                insert=(child.attrib.get("x", "0"), child.attrib.get("y", "0")),
                **style,
            )

        if node is not None:
            parent_dwg.add(node)


def normalize_svg_with_svgwrite(input_svg: Path, output_svg: Path) -> None:
    tree = ET.parse(input_svg)
    root = tree.getroot()

    width = root.attrib.get("width")
    height = root.attrib.get("height")
    view_box = root.attrib.get("viewBox")

    kwargs = {}
    if width and height:
        kwargs["size"] = (width, height)
    if view_box:
        kwargs["viewBox"] = view_box

    drawing = svgwrite.Drawing(filename=str(output_svg), **kwargs)
    _add_children(root, drawing)
    output_svg.parent.mkdir(parents=True, exist_ok=True)
    drawing.save(pretty=True)


def _safe_normalize_svg(input_svg: Path, output_svg: Path) -> bool:
    """Try svgwrite normalization; return False if source SVG is malformed."""
    try:
        normalize_svg_with_svgwrite(input_svg, output_svg)
        return True
    except Exception:
        # Preserve conversion progress by falling back to raw SVG payload.
        if input_svg != output_svg:
            output_svg.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(input_svg, output_svg)
        return False


def svg_to_png(input_svg: Path, output_png: Path) -> None:
    try:
        import cairosvg
    except ImportError as exc:
        raise RuntimeError(
            "cairosvg is required for PNG export. Install with: pip install cairosvg"
        ) from exc

    output_png.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(url=str(input_svg), write_to=str(output_png))


def _extract_pdf_pages_to_svg(pdf_path: Path, output_dir: Path, max_pages: int | None = None) -> list[Path]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for PDF conversion. Install with: pip install pymupdf"
        ) from exc

    exported = []
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        total_pages = len(doc)
        page_count = min(total_pages, max_pages) if max_pages else total_pages
        for i in range(page_count):
            page = doc.load_page(i)
            try:
                svg_text = page.get_svg_image(text_as_path=False)
            except TypeError:
                svg_text = page.get_svg_image()

            if not svg_text:
                continue

            out_svg = output_dir / f"{pdf_path.stem}.page-{i+1:04d}.svg"
            out_svg.write_text(svg_text, encoding="utf-8")
            exported.append(out_svg)
    finally:
        doc.close()

    return exported


def _extract_pdf_pages_to_png(
    pdf_path: Path,
    output_dir: Path,
    scale: float = 2.0,
    max_pages: int | None = None,
) -> list[Path]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for PDF conversion. Install with: pip install pymupdf"
        ) from exc

    exported = []
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        matrix = fitz.Matrix(scale, scale)
        total_pages = len(doc)
        page_count = min(total_pages, max_pages) if max_pages else total_pages
        for i in range(page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out_png = output_dir / f"{pdf_path.stem}.page-{i+1:04d}.png"
            pix.save(out_png)
            exported.append(out_png)
    finally:
        doc.close()

    return exported


def iter_inputs(path: Path, recursive: bool):
    if path.is_file():
        yield path
        return
    pattern = "**/*" if recursive else "*"
    for p in path.glob(pattern):
        if any(part in SKIP_DIR_NAMES for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in SUPPORTED_VECTOR_EXTS:
            yield p


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert vector images with Python/svgwrite")
    p.add_argument("input", type=Path, help="Input SVG file or directory")
    p.add_argument("--out-dir", type=Path, default=Path("converted-vectors"), help="Output directory")
    p.add_argument("--to", choices=["svg", "png"], default="svg", help="Output format")
    p.add_argument("--recursive", action="store_true", help="Recurse into subdirectories")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing output files")
    p.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages to export per PDF (default: all pages)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    input_path = args.input.resolve()
    out_dir = args.out_dir.resolve()

    if args.max_pages is not None and args.max_pages <= 0:
        print("--max-pages must be a positive integer", file=sys.stderr)
        return 2

    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2

    converted = 0
    skipped = 0

    for src in iter_inputs(input_path, args.recursive):
        suffix = src.suffix.lower()
        if suffix not in SUPPORTED_VECTOR_EXTS:
            skipped += 1
            continue

        if suffix == ".svg":
            rel = src.name if input_path.is_file() else src.relative_to(input_path)
            if args.to == "svg":
                dst = (out_dir / rel).with_suffix(".svg")
            else:
                dst = (out_dir / rel).with_suffix(".png")

            if dst.exists() and not args.overwrite:
                skipped += 1
                continue

            try:
                temp_svg = dst if args.to == "svg" else dst.with_suffix(".normalized.svg")
                normalize_svg_with_svgwrite(src, temp_svg)
                if args.to == "png":
                    svg_to_png(temp_svg, dst)
                    if temp_svg.exists():
                        temp_svg.unlink()
                converted += 1
                print(f"OK: {src} -> {dst}")
            except Exception as exc:
                print(f"FAIL: {src} ({exc})", file=sys.stderr)
            continue

        if suffix == ".pdf":
            rel = src.stem if input_path.is_file() else str(src.relative_to(input_path).with_suffix(""))
            pdf_out_dir = out_dir / rel
            try:
                if args.to == "png":
                    page_pngs = _extract_pdf_pages_to_png(src, pdf_out_dir, max_pages=args.max_pages)
                    if not page_pngs:
                        print(f"WARN: {src} (no PNG pages exported)", file=sys.stderr)
                        continue

                    for page_png in page_pngs:
                        converted += 1
                        print(f"OK: {src} -> {page_png}")
                    continue

                page_svgs = _extract_pdf_pages_to_svg(src, pdf_out_dir, max_pages=args.max_pages)
                if not page_svgs:
                    print(f"WARN: {src} (no SVG pages exported)", file=sys.stderr)
                    continue

                for page_svg in page_svgs:
                    if args.to == "svg":
                        dst = page_svg
                        # Re-normalize in place for consistency.
                        normalized = _safe_normalize_svg(page_svg, dst)
                        if not normalized:
                            print(f"WARN: normalization skipped for malformed SVG {dst}", file=sys.stderr)
                    else:
                        dst = page_svg.with_suffix(".png")
                        normalized = _safe_normalize_svg(page_svg, page_svg)
                        if not normalized:
                            print(f"WARN: normalization skipped for malformed SVG {page_svg}", file=sys.stderr)
                        svg_to_png(page_svg, dst)
                        if page_svg.exists():
                            page_svg.unlink()

                    converted += 1
                    print(f"OK: {src} -> {dst}")
            except Exception as exc:
                print(f"FAIL: {src} ({exc})", file=sys.stderr)
            continue

    print(f"Done. converted={converted} skipped={skipped} out_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
