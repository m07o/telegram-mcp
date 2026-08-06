"""Minimal native XLSX writer using only Python standard library.

Creates a valid .xlsx file (ZIP of XML) without external dependencies.
Supports multiple worksheets with headers, rows, and basic cell formatting.
"""

from __future__ import annotations

import datetime as dt
import os
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


# XML namespace constants
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _make_content_types() -> str:
    """Generate [Content_Types].xml"""
    ET.register_namespace("", NS_CT)
    types = ET.Element("Types", xmlns=NS_CT)
    ET.SubElement(
        types,
        "Default",
        Extension="rels",
        ContentType="application/vnd.openxmlformats-package.relationships+xml",
    )
    ET.SubElement(types, "Default", Extension="xml", ContentType="application/xml")
    ET.SubElement(
        types,
        "Override",
        PartName="/xl/workbook.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    ET.SubElement(
        types,
        "Override",
        PartName="/xl/styles.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
    )
    ET.SubElement(
        types,
        "Override",
        PartName="/xl/sharedStrings.xml",
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
    )
    return ET.tostring(types, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _make_rels() -> str:
    """Generate _rels/.rels"""
    ET.register_namespace("", NS_RELS)
    rels = ET.Element("Relationships", xmlns=NS_RELS)
    ET.SubElement(
        rels, "Relationship", Id="rId1", Type=f"{NS_RELS}officeDocument", Target="xl/workbook.xml"
    )
    return ET.tostring(rels, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _make_workbook_rels(sheet_count: int) -> str:
    """Generate xl/_rels/workbook.xml.rels"""
    ET.register_namespace("", NS_RELS)
    rels = ET.Element("Relationships", xmlns=NS_RELS)
    for i in range(1, sheet_count + 1):
        ET.SubElement(
            rels,
            "Relationship",
            Id=f"rId{i}",
            Type=f"{NS_RELS}worksheet",
            Target=f"worksheets/sheet{i}.xml",
        )
    ET.SubElement(
        rels,
        "Relationship",
        Id=f"rId{sheet_count + 1}",
        Type=f"{NS_RELS}sharedStrings",
        Target="sharedStrings.xml",
    )
    ET.SubElement(
        rels,
        "Relationship",
        Id=f"rId{sheet_count + 2}",
        Type=f"{NS_RELS}styles",
        Target="styles.xml",
    )
    return ET.tostring(rels, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _make_workbook(sheet_names: list[str]) -> str:
    """Generate xl/workbook.xml"""
    ET.register_namespace("", NS_MAIN)
    ET.register_namespace("r", NS_RELS)
    wb = ET.Element("workbook", xmlns=NS_MAIN, **{"xmlns:r": NS_RELS})
    sheets = ET.SubElement(wb, "sheets")
    for idx, name in enumerate(sheet_names, 1):
        ET.SubElement(
            sheets, "sheet", name=name, sheetId=str(idx), **{f"{{{NS_RELS}}}id": f"rId{idx}"}
        )
    return ET.tostring(wb, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _make_styles() -> str:
    """Generate xl/styles.xml with minimal styles (header bold, default cell)"""
    ET.register_namespace("", NS_MAIN)
    style_sheet = ET.Element("styleSheet", xmlns=NS_MAIN)
    # Number formats
    num_fmts = ET.SubElement(style_sheet, "numFmts", count="0")
    # Fonts: 0=default, 1=bold
    fonts = ET.SubElement(style_sheet, "fonts", count="2")
    ET.SubElement(fonts, "font")  # default
    font_bold = ET.SubElement(fonts, "font")
    ET.SubElement(font_bold, "b")
    # Fills: 0=none
    fills = ET.SubElement(style_sheet, "fills", count="1")
    fill = ET.SubElement(fills, "fill")
    ET.SubElement(fill, "patternFill", patternType="none")
    # Borders: 0=none
    borders = ET.SubElement(style_sheet, "borders", count="1")
    border = ET.SubElement(borders, "border")
    for side in ("left", "right", "top", "bottom", "diagonal"):
        ET.SubElement(border, side)
    # Cell style Xfs: 0=default
    cell_style_xfs = ET.SubElement(style_sheet, "cellStyleXfs", count="1")
    ET.SubElement(cell_style_xfs, "xf", numFmtId="0", fontId="0", fillId="0", borderId="0")
    # Cell Xfs: 0=default, 1=header bold
    cell_xfs = ET.SubElement(style_sheet, "cellXfs", count="2")
    ET.SubElement(
        cell_xfs, "xf", numFmtId="0", fontId="0", fillId="0", borderId="0", xfId="0"
    )  # default
    ET.SubElement(
        cell_xfs, "xf", numFmtId="0", fontId="1", fillId="0", borderId="0", xfId="0", applyFont="1"
    )  # header
    return ET.tostring(style_sheet, encoding="utf-8", xml_declaration=True).decode("utf-8")


class XLSXWriter:
    """Build an XLSX file in memory and write to disk."""

    def __init__(self, sheet_names: list[str]) -> None:
        self.sheet_names = sheet_names
        self.sheet_data: list[list[list[Any]]] = [[] for _ in sheet_names]  # rows per sheet
        self._shared_strings: dict[str, int] = {}
        self._shared_list: list[str] = []

    def _get_shared_string_idx(self, value: str) -> int:
        """Get shared string index, adding if new."""
        if value not in self._shared_strings:
            self._shared_strings[value] = len(self._shared_list)
            self._shared_list.append(value)
        return self._shared_strings[value]

    def _make_shared_strings(self) -> str:
        ET.register_namespace("", NS_MAIN)
        sst = ET.Element(
            "sst",
            xmlns=NS_MAIN,
            count=str(sum(1 for _ in self._shared_list)),
            uniqueCount=str(len(self._shared_list)),
        )
        for s in self._shared_list:
            si = ET.SubElement(sst, "si")
            t = ET.SubElement(si, "t")
            t.text = escape(s)
        return ET.tostring(sst, encoding="utf-8", xml_declaration=True).decode("utf-8")

    def add_row(self, sheet_idx: int, cells: list[Any], is_header: bool = False) -> None:
        """Add a row to a sheet. cells can be str, int, float, bool, datetime, or None."""
        row_num = len(self.sheet_data[sheet_idx]) + 1
        row_cells = []
        for col_idx, val in enumerate(cells):
            cell_ref = f"{self._col_letter(col_idx)}{row_num}"
            if val is None:
                row_cells.append(
                    {
                        "ref": cell_ref,
                        "type": "blank",
                        "value": None,
                        "style": 1 if is_header else 0,
                    }
                )
            elif isinstance(val, bool):
                row_cells.append(
                    {
                        "ref": cell_ref,
                        "type": "b",
                        "value": "1" if val else "0",
                        "style": 1 if is_header else 0,
                    }
                )
            elif isinstance(val, (int, float)):
                row_cells.append(
                    {
                        "ref": cell_ref,
                        "type": "n",
                        "value": str(val),
                        "style": 1 if is_header else 0,
                    }
                )
            elif isinstance(val, dt.datetime):
                # Excel serial date
                epoch = dt.datetime(1899, 12, 30)
                serial = (val - epoch).total_seconds() / 86400
                row_cells.append(
                    {
                        "ref": cell_ref,
                        "type": "n",
                        "value": str(serial),
                        "style": 1 if is_header else 0,
                    }
                )
            else:
                # String -> shared string
                s = str(val)
                idx = self._get_shared_string_idx(s)
                row_cells.append(
                    {
                        "ref": cell_ref,
                        "type": "s",
                        "value": str(idx),
                        "style": 1 if is_header else 0,
                    }
                )
        self.sheet_data[sheet_idx].append(row_cells)

    def _col_letter(self, idx: int) -> str:
        """Convert 0-based column index to Excel column letter (A, B, ..., Z, AA, AB, ...)."""
        result = ""
        while idx >= 0:
            result = chr(ord("A") + (idx % 26)) + result
            idx = idx // 26 - 1
        return result

    def write(self, path: str | Path) -> None:
        """Write the XLSX file to disk."""
        path = Path(path)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            # [Content_Types].xml
            zf.writestr("[Content_Types].xml", _make_content_types())
            # _rels/.rels
            zf.writestr("_rels/.rels", _make_rels())
            # xl/workbook.xml
            zf.writestr("xl/workbook.xml", _make_workbook(self.sheet_names))
            # xl/_rels/workbook.xml.rels
            zf.writestr("xl/_rels/workbook.xml.rels", _make_workbook_rels(len(self.sheet_names)))
            # xl/styles.xml
            zf.writestr("xl/styles.xml", _make_styles())
            # xl/sharedStrings.xml
            zf.writestr("xl/sharedStrings.xml", self._make_shared_strings())
            # Worksheets
            for sheet_idx, sheet_name in enumerate(self.sheet_names):
                zf.writestr(
                    f"xl/worksheets/sheet{sheet_idx + 1}.xml", self._make_worksheet(sheet_idx)
                )

    def _make_worksheet(self, sheet_idx: int) -> str:
        ET.register_namespace("", NS_MAIN)
        ws = ET.Element("worksheet", xmlns=NS_MAIN)
        sheet_data_el = ET.SubElement(ws, "sheetData")
        for row in self.sheet_data[sheet_idx]:
            row_el = ET.SubElement(sheet_data_el, "row")
            for cell in row:
                c = ET.SubElement(row_el, "c", r=cell["ref"])
                if cell["type"] != "n":
                    c.set("t", cell["type"])
                c.set("s", str(cell["style"]))
                if cell["value"] is not None:
                    v = ET.SubElement(c, "v")
                    v.text = cell["value"]
        return ET.tostring(ws, encoding="utf-8", xml_declaration=True).decode("utf-8")


def write_analysis_to_excel(analysis_json: str, output_path: str | Path) -> None:
    """Convert analyze_group detail JSON output to an Excel workbook.

    Sheets created:
    1. Summary - overall stats
    2. Topics - per-topic detail with message samples
    3. Duplicates - duplicate topic groups
    4. Gaps - topics with missing data
    5. Dead Topics - inactive topics

    Args:
        analysis_json: JSON string from analyze_group(mode="detail")
        output_path: Path to write .xlsx file
    """
    import json

    data = json.loads(analysis_json)

    sheet_names = ["Summary", "Topics", "Duplicates", "Gaps", "Dead Topics"]
    writer = XLSXWriter(sheet_names)

    # Sheet 1: Summary
    writer.add_row(0, ["Field", "Value"], is_header=True)
    stats = data.get("summary_stats", {})
    for key, val in stats.items():
        writer.add_row(0, [key.replace("_", " ").title(), val])
    findings = data.get("findings", {})
    for key, val in findings.items():
        writer.add_row(0, [key.replace("_", " ").title(), val])

    # Sheet 2: Topics
    topics = data.get("topics", [])
    if topics:
        writer.add_row(
            1,
            [
                "ID",
                "Title",
                "Total Messages",
                "Last Activity",
                "Icon Emoji ID",
                "Hidden",
                "Closed",
                "Description",
                "Sample Messages",
            ],
            is_header=True,
        )
        for t in topics:
            samples = t.get("message_samples", [])
            sample_text = "; ".join(f"[{s['id']}] {s['text'][:80]}" for s in samples[:3])
            writer.add_row(
                1,
                [
                    t.get("id"),
                    t.get("title"),
                    t.get("total_messages"),
                    t.get("last_activity_iso"),
                    t.get("icon_emoji_id"),
                    t.get("hidden"),
                    t.get("closed"),
                    t.get("description", ""),
                    sample_text,
                ],
            )

    # Sheet 3: Duplicates
    duplicates = data.get("duplicates", [])
    if duplicates:
        writer.add_row(
            2, ["Normalized Title", "Topic IDs", "Original Titles", "Count"], is_header=True
        )
        for d in duplicates:
            writer.add_row(
                2,
                [
                    d.get("normalized_title"),
                    ", ".join(str(x) for x in d.get("topic_ids", [])),
                    ", ".join(d.get("original_titles", [])),
                    len(d.get("topic_ids", [])),
                ],
            )

    # Sheet 4: Gaps
    gaps = data.get("gaps", [])
    if gaps:
        writer.add_row(3, ["Kind", "Topic ID", "Detail"], is_header=True)
        for g in gaps:
            writer.add_row(3, [g.get("kind"), g.get("topic_id"), g.get("detail")])

    # Sheet 5: Dead Topics
    dead = data.get("dead_topics", [])
    if dead:
        writer.add_row(4, ["Topic ID"], is_header=True)
        for tid in dead:
            writer.add_row(4, [tid])

    writer.write(output_path)
