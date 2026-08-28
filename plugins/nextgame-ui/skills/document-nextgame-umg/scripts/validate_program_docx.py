#!/usr/bin/env python3
"""Create render evidence or validate final NextGame UMG DOCX evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from _document_contract_common import (
    BUILD_ACCEPTANCE_SCHEMA,
    DOCUMENT_VERIFICATION_SCHEMA,
    HANDOFF_SCHEMA,
    LEGACY_WIDGET_TREE_TABLE_FORMAT,
    PROGRAM_DOCUMENT_CONTENT_SCHEMA,
    WIDGET_TREE_EMPTY_STATE,
    WIDGET_TREE_TABLE_FORMAT,
    canonical_sha256,
    issue,
    load_json,
    parse_aware_iso8601,
    project_widget_tree_tables,
    result,
    sha256_file,
    validate_schema_instance,
    write_json,
)
from validate_build_acceptance import validate_acceptance_handoff_binding, validate_build_acceptance


RENDER_EVIDENCE_SCHEMA = DOCUMENT_VERIFICATION_SCHEMA.parent / "render-evidence.schema.json"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PDF_SIGNATURE = b"%PDF-"
PDF_EOF = b"%%EOF"
OUTPUT_ROOT_ENVIRONMENT_VARIABLE = "NEXTGAME_UI_PROGRAM_DOCS_ROOT"
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = f"{{{WORD_NAMESPACE}}}"
WORD_NAMESPACES = {"w": WORD_NAMESPACE}
WIDGET_TREE_EMPTY_LABEL = "无自有 WidgetTree 节点（继承结构不重复列出）"
# ``python-docx`` rounds ``Mm(...).twips`` to these exact values. Keep the
# verifier independent of that optional authoring dependency while preserving
# the legacy 0.3 geometry and enforcing the current 0.4 geometry.
LEGACY_WIDGET_TREE_COLUMN_WIDTHS_TWIPS = (3969, 3061, 1247)
WIDGET_TREE_COLUMN_WIDTHS_TWIPS = (2948, 1928, 1134, 2268)
WIDGET_TREE_GEOMETRY_BY_FORMAT = {
    LEGACY_WIDGET_TREE_TABLE_FORMAT: LEGACY_WIDGET_TREE_COLUMN_WIDTHS_TWIPS,
    WIDGET_TREE_TABLE_FORMAT: WIDGET_TREE_COLUMN_WIDTHS_TWIPS,
}

FORBIDDEN_DOCUMENT_POLICIES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "generatedContentLifecycleDetails",
        (
            re.compile(
                r"\b(?:program(?:matically)?[- ](?:generated|populated)|generated|populated|dynamic)\s+"
                r"(?:content|data|items?)\s+(?:data\s+)?(?:source|owner|refresh\s+(?:strategy|policy|schedule|timing))\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:data\s+source|owner|refresh\s+(?:strategy|policy|schedule|timing))\s+"
                r"(?:for|of)\s+(?:program(?:matically)?[- ](?:generated|populated)|generated|populated|dynamic)\s+"
                r"(?:content|data|items?)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:\u7a0b\u5e8f|\u8fd0\u884c\u65f6|\u52a8\u6001)(?:\u751f\u6210|\u586b\u5145)(?:\u7684)?"
                r"(?:\u5185\u5bb9|\u6570\u636e|\u6761\u76ee)(?:\u7684)?"
                r"(?:\u6570\u636e\u6765\u6e90|\u6570\u636e\u6e90|\u6240\u6709\u8005|\u5f52\u5c5e|"
                r"\u5237\u65b0\u7b56\u7565|\u5237\u65b0\u673a\u5236|\u5237\u65b0\u65f6\u673a)"
            ),
        ),
    ),
    (
        "runtimeParameterContractDetails",
        (
            re.compile(
                r"\bruntime\s+parameters?(?:'s)?\s+(?:type|default(?:\s+value)?|update\s+(?:timing|time|schedule))\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:type|default(?:\s+value)?|update\s+(?:timing|time|schedule))\s+(?:for|of)\s+"
                r"(?:the\s+)?runtime\s+parameters?\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\u8fd0\u884c\u65f6\u53c2\u6570(?:\u7684)?"
                r"(?:\u7c7b\u578b|\u9ed8\u8ba4\u503c|\u66f4\u65b0\u65f6\u673a|\u66f4\u65b0\u65f6\u95f4)"
            ),
        ),
    ),
    (
        "eventCallbackContractDetails",
        (
            re.compile(r"\b(?:event|callback)(?:\s+interface)?\s+(?:name|payload)\b", re.IGNORECASE),
            re.compile(r"\b(?:name|payload)\s+(?:for|of)\s+(?:the\s+)?(?:event|callback)\b", re.IGNORECASE),
            re.compile(
                r"(?:\u4e8b\u4ef6|\u56de\u8c03)(?:\u63a5\u53e3)?(?:\u7684)?"
                r"(?:\u540d\u79f0|\u540d\u5b57|\u8f7d\u8377|\u8d1f\u8f7d|\u53c2\u6570(?:\u7ed3\u6784|\u5b57\u6bb5|\u5185\u5bb9)?)"
            ),
        ),
    ),
    (
        "collectionItemSchemaDetails",
        (
            re.compile(
                r"\b(?:list|collection)\s+(?:item|entry)(?:\s+data)?\s+(?:structure|schema|fields?)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:structure|schema|fields?)\s+(?:for|of)\s+(?:the\s+)?(?:list|collection)\s+(?:item|entry)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:\u5217\u8868|\u96c6\u5408)(?:\u6570\u636e)?(?:\u9879|\u6761\u76ee)(?:\u7684)?"
                r"(?:\u6570\u636e\u7ed3\u6784|\u7ed3\u6784|\u6a21\u5f0f|\u5b57\u6bb5)"
            ),
        ),
    ),
)

STATE_BRANCH_VISIBILITY_PATTERN = re.compile(
    r"\bState\s+branch\s*:[^\r\n]*\bvisibility\s*=",
    re.IGNORECASE,
)
SAVED_VISIBILITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:actualSavedVisibilityBindings|actualSavedVisibility|savedVisibility|"
        r"defaultVisibility|initialVisibility|designerVisibility)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:default|initial)\s+(?:designer\s+)?visibility\b", re.IGNORECASE),
    re.compile(
        r"\b(?:actual|saved|post[- ]save(?:d)?|readback|designer)\b"
        r"[^\r\n]{0,80}\bvisibility\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bvisibility\b[^\r\n]{0,80}"
        r"\b(?:actual|saved|post[- ]save(?:d)?|readback|designer)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:实际|已保存|保存后|读回|设计器)[^\r\n]{0,40}(?:可见性|Visibility)", re.IGNORECASE),
    re.compile(r"(?:可见性|Visibility)[^\r\n]{0,40}(?:实际|已保存|保存后|读回|设计器)", re.IGNORECASE),
    re.compile(r"(?:默认|初始)(?:设计器)?可见性", re.IGNORECASE),
)

EMBEDDED_IDENTIFIER_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])(?:collection|state-model)\.[A-Za-z0-9_.-]+")
TITLE_STYLE_ID_PATTERN = re.compile(r"^(?:heading\d*|title|subtitle|toolasset|toolsection)$")
SECTION_NUMBER_PATTERN = re.compile(r"^\s*(\d+)\s*[.．、]\s*(.+?)\s*$")
CHINESE_AUXILIARY_NAME_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _word_property_enabled(element: ElementTree.Element) -> bool:
    value = next(
        (raw for name, raw in element.attrib.items() if name == "val" or name.endswith("}val")),
        None,
    )
    return value is None or str(value).strip().lower() not in {"0", "false", "off", "no"}


def _extract_docx_text(path: Path, *, include_comments: bool, include_hidden: bool) -> str:
    paragraphs: list[str] = []
    with zipfile.ZipFile(path) as archive:
        text_parts = [
            name
            for name in archive.namelist()
            if name == "word/document.xml"
            or re.fullmatch(
                r"word/(?:header\d+|footer\d+|footnotes|endnotes" + (r"|comments" if include_comments else "") + r")\.xml",
                name,
            )
        ]
        for part_name in sorted(text_parts, key=lambda name: (name != "word/document.xml", name)):
            root = ElementTree.fromstring(archive.read(part_name))
            for paragraph in root.iter():
                if not paragraph.tag.endswith("}p"):
                    continue
                fragments: list[str] = []
                runs = [node for node in paragraph.iter() if node.tag.endswith("}r")]
                for run in runs:
                    hidden = any(
                        (node.tag.endswith("}vanish") or node.tag.endswith("}webHidden"))
                        and _word_property_enabled(node)
                        for node in run.iter()
                    )
                    if hidden and not include_hidden:
                        continue
                    fragments.extend(node.text or "" for node in run.iter() if node.tag.endswith("}t"))
                if not runs:
                    fragments.extend(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
                paragraphs.append("".join(fragments))
    return "\n".join(paragraphs)


def extract_docx_text(path: Path) -> str:
    """Extract text that participates in the rendered programmer-facing document."""

    return _extract_docx_text(path, include_comments=False, include_hidden=False)


def extract_docx_policy_text(path: Path) -> str:
    """Extract visible and non-visible Word text so excluded policy details cannot be hidden."""

    return _extract_docx_text(path, include_comments=True, include_hidden=True)


def _check_embedded_identifier_styles(path: Path, errors: list[dict[str, str]]) -> None:
    """Reject collection/state identifiers promoted to document-navigation styles."""

    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    for paragraph_index, paragraph in enumerate(root.iter(f"{WORD}p")):
        text, _ = _word_visible_text(paragraph)
        identifiers = EMBEDDED_IDENTIFIER_PATTERN.findall(text)
        if not identifiers:
            continue
        properties = paragraph.find(f"{WORD}pPr")
        style_element = properties.find(f"{WORD}pStyle") if properties is not None else None
        style_id = _word_attribute(style_element, "val") or ""
        normalized_style = re.sub(r"[^a-z0-9]", "", style_id.lower())
        if TITLE_STYLE_ID_PATTERN.fullmatch(normalized_style):
            errors.append(
                issue(
                    "document.embedded_identifier_title_style",
                    f"$.document.paragraphs[{paragraph_index}]",
                    f"Embedded identifier {identifiers[0]!r} must use body or inline-label styling, not {style_id!r}.",
                )
            )


def _word_visible_text(element: ElementTree.Element) -> tuple[str, bool]:
    fragments: list[str] = []
    hidden_found = False
    for run in (node for node in element.iter() if node.tag == f"{WORD}r"):
        hidden = any(
            node.tag in {f"{WORD}vanish", f"{WORD}webHidden"} and _word_property_enabled(node)
            for node in run.iter()
        )
        hidden_found = hidden_found or hidden
        if not hidden:
            fragments.extend(node.text or "" for node in run.iter() if node.tag == f"{WORD}t")
    return "".join(fragments), hidden_found


def _word_attribute(element: ElementTree.Element | None, name: str) -> str | None:
    if element is None:
        return None
    return next((value for key, value in element.attrib.items() if key == name or key.endswith(f"}}{name}")), None)


def _normalized_style_id(paragraph: ElementTree.Element) -> str:
    properties = paragraph.find(f"{WORD}pPr")
    style_element = properties.find(f"{WORD}pStyle") if properties is not None else None
    style_id = _word_attribute(style_element, "val") or ""
    return re.sub(r"[^a-z0-9]", "", style_id.lower())


def _section_number_and_title(text: str) -> tuple[int | None, str]:
    match = SECTION_NUMBER_PATTERN.fullmatch(text)
    if match is None:
        return None, text.strip()
    return int(match.group(1)), match.group(2).strip()


def _labeled_value(text: str, label: str) -> str | None:
    match = re.fullmatch(rf"\s*{re.escape(label)}\s*[:：]\s*(.*?)\s*", text)
    return match.group(1) if match is not None else None


def _forbidden_v04_heading(title: str) -> str | None:
    _, unnumbered = _section_number_and_title(title)
    compact = re.sub(r"\s+", "", unnumbered)
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("目标资产", re.compile(r"^目标资产(?:清单|列表|说明)?$")),
        ("程序变量", re.compile(r"^程序变量(?:清单|列表|说明)?$")),
        ("动态集合", re.compile(r"^动态集合(?:与EntryClass)?$", re.IGNORECASE)),
        ("状态模型", re.compile(r"^(?:状态模型|状态控制|状态模型[/／]状态控制)$")),
        ("支持依赖定位", re.compile(r"^支持依赖定位$")),
        ("只读快照边界", re.compile(r"^只读快照边界$")),
        ("附录 A", re.compile(r"^附录A(?:[:：].*)?$", re.IGNORECASE)),
    )
    return next((name for name, pattern in patterns if pattern.fullmatch(compact)), None)


def _body_records(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{WORD}body")
    if body is None:
        raise ValueError("word/document.xml has no w:body element.")

    records: list[dict[str, Any]] = []
    for body_index, element in enumerate(list(body)):
        if element.tag == f"{WORD}p":
            text, hidden = _word_visible_text(element)
            records.append(
                {
                    "bodyIndex": body_index,
                    "kind": "paragraph",
                    "text": text.strip(),
                    "hidden": hidden,
                    "style": _normalized_style_id(element),
                    "element": element,
                }
            )
        elif element.tag == f"{WORD}tbl":
            rows = element.findall("./w:tr", WORD_NAMESPACES)
            cells = rows[0].findall("./w:tc", WORD_NAMESPACES) if rows else []
            records.append(
                {
                    "bodyIndex": body_index,
                    "kind": "table",
                    "headers": tuple(_word_visible_text(cell)[0].strip() for cell in cells),
                    "element": element,
                }
            )
    return records


def _check_v04_forbidden_structure(records: list[dict[str, Any]], errors: list[dict[str, str]]) -> None:
    for record in records:
        paragraphs = (
            [record["element"]]
            if record["kind"] == "paragraph"
            else list(record["element"].iter(f"{WORD}p"))
        )
        for paragraph_index, paragraph in enumerate(paragraphs):
            text = "".join(node.text or "" for node in paragraph.iter(f"{WORD}t")).strip()
            location = f"$.document.body[{record['bodyIndex']}]"
            if record["kind"] == "table":
                location += f".paragraphs[{paragraph_index}]"
            if re.match(r"^\s*(?:根控件|所属资产)\s*(?:[:：]|$)", text):
                errors.append(
                    issue(
                        "document.forbidden_asset_field",
                        location,
                        f"Production 0.4 asset blocks must not contain {text!r}.",
                    )
                )
            if not TITLE_STYLE_ID_PATTERN.fullmatch(_normalized_style_id(paragraph)):
                continue
            forbidden = _forbidden_v04_heading(text)
            if forbidden is not None:
                errors.append(
                    issue(
                        "document.forbidden_heading",
                        location,
                        f"Production 0.4 must not emit the legacy standalone heading {forbidden!r}.",
                    )
                )


def _check_v04_section_numbering(
    records: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> list[tuple[int, int | None, str]]:
    sections: list[tuple[int, int | None, str]] = []
    for record_index, record in enumerate(records):
        if record["kind"] != "paragraph" or record["style"] != "toolsection":
            continue
        number, title = _section_number_and_title(record["text"])
        sections.append((record_index, number, title))

    if not sections:
        errors.append(
            issue(
                "document.asset_section_missing",
                "$.document.sections",
                "Production 0.4 requires the numbered 1. 资产详细说明 module.",
            )
        )
        return sections

    actual_numbers = [number for _, number, _ in sections]
    expected_numbers = list(range(1, len(sections) + 1))
    if actual_numbers != expected_numbers:
        errors.append(
            issue(
                "document.section_numbering",
                "$.document.sections",
                f"Tool Section headings must be numbered continuously as {expected_numbers!r}; found {actual_numbers!r}.",
            )
        )
    asset_sections = [(record_index, number) for record_index, number, title in sections if title == "资产详细说明"]
    if asset_sections != [(sections[0][0], 1)]:
        errors.append(
            issue(
                "document.asset_section_missing",
                "$.document.sections",
                "Production 0.4 requires exactly one first module named 1. 资产详细说明.",
            )
        )
    return sections


def _check_v04_asset_block(
    records: list[dict[str, Any]],
    *,
    heading_index: int,
    end_index: int,
    asset: dict[str, Any],
    asset_index: int,
    expected_headers: tuple[str, ...],
    errors: list[dict[str, str]],
) -> None:
    block = records[heading_index:end_index]
    heading = records[heading_index]
    asset_path = asset.get("assetPath")
    basename = asset_path.rsplit("/", 1)[-1] if isinstance(asset_path, str) else ""
    heading_text = heading["text"]
    suffix = heading_text[len(basename) :].strip() if basename and heading_text.startswith(basename) else ""
    path = f"$.document.assetDetails[{asset_index}]"
    if not basename or not heading_text.startswith(basename) or not suffix or not CHINESE_AUXILIARY_NAME_PATTERN.search(suffix):
        errors.append(
            issue(
                "document.asset_heading",
                f"{path}.heading",
                f"Asset heading must start with exact basename {basename!r} and include a non-empty Chinese auxiliary name.",
            )
        )

    def labeled_positions(label: str) -> list[tuple[int, str]]:
        matches: list[tuple[int, str]] = []
        for offset, record in enumerate(block):
            if record["kind"] != "paragraph":
                continue
            value = _labeled_value(record["text"], label)
            if value is not None:
                matches.append((heading_index + offset, value))
        return matches

    asset_paths = labeled_positions("资产路径")
    parent_classes = labeled_positions("Parent Class")
    functional_summaries = labeled_positions("功能说明")
    relationships = labeled_positions("程序接入关系")
    widget_tables = [
        (heading_index + offset, record)
        for offset, record in enumerate(block)
        if record["kind"] == "table" and record.get("headers") == expected_headers
    ]

    if len(asset_paths) != 1 or asset_paths[0][1] != asset_path:
        errors.append(
            issue(
                "document.asset_path",
                f"{path}.assetPath",
                f"Asset block must contain exactly one 资产路径 equal to {asset_path!r}.",
            )
        )

    expected_parent = asset.get("parentClassPath")
    parent_valid = (
        len(parent_classes) == 1
        and bool(parent_classes[0][1])
        and (not isinstance(expected_parent, str) or parent_classes[0][1] == expected_parent)
    )
    if not parent_valid:
        expectation = f" equal to {expected_parent!r}" if isinstance(expected_parent, str) else " with a non-empty value"
        errors.append(
            issue(
                "document.parent_class",
                f"{path}.parentClass",
                f"Asset block must contain exactly one Parent Class{expectation}.",
            )
        )

    if len(functional_summaries) != 1 or not functional_summaries[0][1]:
        errors.append(
            issue(
                "document.functional_summary",
                f"{path}.functionalSummary",
                "Asset block must contain exactly one non-empty 功能说明 before its WidgetTree table.",
            )
        )
    if len(widget_tables) != 1:
        errors.append(
            issue(
                "document.asset_widget_tree",
                f"{path}.widgetTree",
                "Asset block must contain exactly one matching four-column WidgetTree table.",
            )
        )
    if len(relationships) != 1 or not relationships[0][1]:
        errors.append(
            issue(
                "document.program_relationship",
                f"{path}.programRelationship",
                "Asset block must contain exactly one non-empty 程序接入关系 after its WidgetTree table.",
            )
        )

    if all(
        len(items) == 1
        for items in (asset_paths, parent_classes, functional_summaries, widget_tables, relationships)
    ):
        actual_order = [
            asset_paths[0][0],
            parent_classes[0][0],
            functional_summaries[0][0],
            widget_tables[0][0],
            relationships[0][0],
        ]
        if actual_order != sorted(actual_order) or len(set(actual_order)) != len(actual_order):
            errors.append(
                issue(
                    "document.asset_block_order",
                    path,
                    "Asset block order must be heading, 资产路径, Parent Class, 功能说明, four-column WidgetTree table, then 程序接入关系.",
                )
            )


def _check_v04_other_asset_modules(
    records: list[dict[str, Any]],
    sections: list[tuple[int, int | None, str]],
    errors: list[dict[str, str]],
) -> None:
    other_sections = [item for item in sections if item[2] == "其他资产程序说明"]
    for section_index, (start_index, _, _) in enumerate(other_sections):
        following_sections = [record_index for record_index, _, _ in sections if record_index > start_index]
        end_index = min(following_sections) if following_sections else len(records)
        heading_indexes = [
            record_index
            for record_index in range(start_index + 1, end_index)
            if records[record_index]["kind"] == "paragraph" and records[record_index]["style"] == "heading2"
        ]
        module_valid = bool(heading_indexes)
        for heading_offset, heading_index in enumerate(heading_indexes):
            group_end = heading_indexes[heading_offset + 1] if heading_offset + 1 < len(heading_indexes) else end_index
            group = records[heading_index + 1 : group_end]
            asset_paths = [
                _labeled_value(record["text"], "资产路径")
                for record in group
                if record["kind"] == "paragraph" and _labeled_value(record["text"], "资产路径") is not None
            ]
            has_program_content = any(
                record["kind"] == "table"
                or (
                    record["kind"] == "paragraph"
                    and any(
                        bool(_labeled_value(record["text"], label))
                        for label in ("动态集合", "状态说明", "支持依赖")
                    )
                )
                for record in group
            )
            if len(asset_paths) != 1 or not asset_paths[0] or not has_program_content:
                module_valid = False
        if not module_valid:
            errors.append(
                issue(
                    "document.other_assets_empty",
                    f"$.document.otherAssetModules[{section_index}]",
                    "其他资产程序说明 must contain at least one Heading 2 asset group with one non-empty asset path and collection, state, or support-dependency content.",
                )
            )


def _check_v04_asset_structure(
    path: Path,
    document_content: dict[str, Any],
    errors: list[dict[str, str]],
) -> None:
    """Validate the direct Word body order required by production document 0.4."""

    try:
        records = _body_records(path)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        errors.append(issue("document.asset_structure_read", "$.document.body", str(error)))
        return

    _check_v04_forbidden_structure(records, errors)
    sections = _check_v04_section_numbering(records, errors)
    _check_v04_other_asset_modules(records, sections, errors)

    asset_sections = [item for item in sections if item[2] == "资产详细说明"]
    if len(asset_sections) != 1:
        return
    start_index = asset_sections[0][0]
    following_sections = [record_index for record_index, _, _ in sections if record_index > start_index]
    end_index = min(following_sections) if following_sections else len(records)
    heading_indexes = [
        record_index
        for record_index in range(start_index + 1, end_index)
        if records[record_index]["kind"] == "paragraph" and records[record_index]["style"] == "heading2"
    ]

    tree_contract = document_content.get("widgetTreeTables") if isinstance(document_content.get("widgetTreeTables"), dict) else {}
    expected_assets = tree_contract.get("assets") if isinstance(tree_contract.get("assets"), list) else []
    expected_headers = tuple(tree_contract.get("headers")) if isinstance(tree_contract.get("headers"), list) else ()
    if len(heading_indexes) != len(expected_assets):
        errors.append(
            issue(
                "document.asset_block_count",
                "$.document.assetDetails",
                f"Expected {len(expected_assets)} Heading 2 asset blocks in contract order, found {len(heading_indexes)}.",
            )
        )

    for asset_index, (heading_index, asset) in enumerate(zip(heading_indexes, expected_assets)):
        block_end = heading_indexes[asset_index + 1] if asset_index + 1 < len(heading_indexes) else end_index
        _check_v04_asset_block(
            records,
            heading_index=heading_index,
            end_index=block_end,
            asset=asset,
            asset_index=asset_index,
            expected_headers=expected_headers,
            errors=errors,
        )


def _widget_tree_table_summary(asset: dict[str, Any]) -> dict[str, Any]:
    rows = asset.get("treeRows", [])
    summary: dict[str, Any] = {
        "assetId": asset.get("assetId"),
        "assetPath": asset.get("assetPath"),
        "rowCount": len(rows),
        "rowsSha256": canonical_sha256(rows),
    }
    if asset.get("emptyState") == WIDGET_TREE_EMPTY_STATE:
        summary["emptyState"] = WIDGET_TREE_EMPTY_STATE
    return summary


def _validate_widget_tree_row_height(
    row: ElementTree.Element,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if row.find("./w:trPr/w:trHeight", WORD_NAMESPACES) is not None:
        errors.append(
            issue(
                "document.widget_tree_row_height",
                path,
                "WidgetTree header, data, and empty-state rows must not set trHeight.",
            )
        )


def _validate_widget_tree_cell_widths(
    cells: list[ElementTree.Element],
    expected_widths: tuple[int, ...],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if len(cells) != len(expected_widths):
        return
    for cell_index, (cell, expected_width) in enumerate(zip(cells, expected_widths)):
        widths = cell.findall("./w:tcPr/w:tcW", WORD_NAMESPACES)
        if (
            len(widths) != 1
            or _word_attribute(widths[0], "type") != "dxa"
            or _word_attribute(widths[0], "w") != str(expected_width)
        ):
            errors.append(
                issue(
                    "document.widget_tree_cell_width",
                    f"{path}.cells[{cell_index}]",
                    f"WidgetTree cell must have one dxa tcW of {expected_width} twips.",
                )
            )


def inspect_widget_tree_tables(
    docx_path: Path,
    document_content: dict[str, Any],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Require one exact, visible, native Word WidgetTree table per contracted asset."""

    tree_contract = document_content.get("widgetTreeTables") if isinstance(document_content.get("widgetTreeTables"), dict) else {}
    table_format = tree_contract.get("format")
    column_widths = WIDGET_TREE_GEOMETRY_BY_FORMAT.get(table_format)
    if column_widths is None:
        errors.append(
            issue(
                "document.widget_tree_format",
                "$.structure.widgetTreeFormat",
                f"Unsupported WidgetTree table format {table_format!r}.",
            )
        )
        return []
    table_width = sum(column_widths)
    column_count = len(column_widths)
    headers = tree_contract.get("headers") if isinstance(tree_contract.get("headers"), list) else []
    expected_assets = tree_contract.get("assets") if isinstance(tree_contract.get("assets"), list) else []
    try:
        with zipfile.ZipFile(docx_path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        errors.append(issue("document.widget_tree_read", "$.structure", str(error)))
        return []

    candidates: list[ElementTree.Element] = []
    for table in root.iter(f"{WORD}tbl"):
        rows = table.findall("./w:tr", WORD_NAMESPACES)
        if not rows:
            continue
        cells = rows[0].findall("./w:tc", WORD_NAMESPACES)
        visible = [_word_visible_text(cell)[0].strip() for cell in cells]
        if visible == headers:
            candidates.append(table)
    if len(candidates) < len(expected_assets):
        drawings = any(node.tag in {f"{WORD}drawing", f"{WORD}pict"} for node in root.iter())
        code = "document.widget_tree_image_substitution" if drawings and not candidates else "document.widget_tree_table_missing"
        errors.append(issue(code, "$.structure.tables", f"Expected {len(expected_assets)} native WidgetTree tables, found {len(candidates)}."))
    if len(candidates) > len(expected_assets):
        errors.append(issue("document.widget_tree_table_extra", "$.structure.tables", f"Expected {len(expected_assets)} native WidgetTree tables, found {len(candidates)}."))

    indent_step = tree_contract.get("indentTwipsPerDepth")
    for table_index, (table, asset) in enumerate(zip(candidates, expected_assets)):
        path = f"$.structure.tables[{table_index}]"
        if any(node.tag in {f"{WORD}drawing", f"{WORD}pict"} for node in table.iter()):
            errors.append(issue("document.widget_tree_image_substitution", path, "WidgetTree table must not contain drawing or pict content."))
        if any(_word_visible_text(cell)[1] for cell in table.iter(f"{WORD}tc")):
            errors.append(issue("document.widget_tree_hidden", path, "WidgetTree table must not contain hidden text."))
        rows = table.findall("./w:tr", WORD_NAMESPACES)
        table_properties = table.find("./w:tblPr", WORD_NAMESPACES)
        layouts = table_properties.findall("./w:tblLayout", WORD_NAMESPACES) if table_properties is not None else []
        if len(layouts) != 1 or _word_attribute(layouts[0], "type") != "fixed":
            errors.append(
                issue(
                    "document.widget_tree_table_layout",
                    path,
                    "WidgetTree table must have exactly one fixed tblLayout.",
                )
            )
        table_widths = table_properties.findall("./w:tblW", WORD_NAMESPACES) if table_properties is not None else []
        if (
            len(table_widths) != 1
            or _word_attribute(table_widths[0], "type") != "dxa"
            or _word_attribute(table_widths[0], "w") != str(table_width)
        ):
            errors.append(
                issue(
                    "document.widget_tree_table_width",
                    path,
                    f"WidgetTree table must have one dxa tblW of {table_width} twips.",
                )
            )
        table_grid = table.find("./w:tblGrid", WORD_NAMESPACES)
        grid_columns = table_grid.findall("./w:gridCol", WORD_NAMESPACES) if table_grid is not None else []
        actual_grid_widths = [_word_attribute(column, "w") for column in grid_columns]
        expected_grid_widths = [str(width) for width in column_widths]
        if len(grid_columns) != column_count or actual_grid_widths != expected_grid_widths:
            errors.append(
                issue(
                    "document.widget_tree_table_grid",
                    path,
                    f"WidgetTree tblGrid must contain exactly the widths {expected_grid_widths!r}.",
                )
            )
        header_properties = rows[0].find("./w:trPr", WORD_NAMESPACES) if rows else None
        header_repeat = header_properties.find("./w:tblHeader", WORD_NAMESPACES) if header_properties is not None else None
        if header_repeat is None or not _word_property_enabled(header_repeat):
            errors.append(issue("document.widget_tree_header_repeat", path, "WidgetTree table header must repeat across page breaks."))
        header_cant_split = header_properties.find("./w:cantSplit", WORD_NAMESPACES) if header_properties is not None else None
        if header_cant_split is None or not _word_property_enabled(header_cant_split):
            errors.append(issue("document.widget_tree_header_split", path, "WidgetTree table header must set cantSplit."))
        if rows:
            _validate_widget_tree_row_height(rows[0], f"{path}.header", errors)
            header_cells = rows[0].findall("./w:tc", WORD_NAMESPACES)
            _validate_widget_tree_cell_widths(
                header_cells,
                column_widths,
                f"{path}.header",
                errors,
            )
        expected_rows = asset.get("treeRows") if isinstance(asset.get("treeRows"), list) else []
        data_rows = rows[1:]
        if not expected_rows:
            if len(data_rows) != 1:
                errors.append(issue("document.widget_tree_empty_state", path, "Empty reuse-only tree requires exactly one empty-state row."))
                continue
            empty_row_properties = data_rows[0].find("./w:trPr", WORD_NAMESPACES)
            empty_cant_split = empty_row_properties.find("./w:cantSplit", WORD_NAMESPACES) if empty_row_properties is not None else None
            if empty_cant_split is None or not _word_property_enabled(empty_cant_split):
                errors.append(issue("document.widget_tree_row_split", path, "Empty WidgetTree row must set cantSplit."))
            _validate_widget_tree_row_height(data_rows[0], f"{path}.rows[0]", errors)
            cells = data_rows[0].findall("./w:tc", WORD_NAMESPACES)
            _validate_widget_tree_cell_widths(
                cells,
                (table_width,),
                f"{path}.rows[0]",
                errors,
            )
            span = cells[0].find("./w:tcPr/w:gridSpan", WORD_NAMESPACES) if len(cells) == 1 else None
            if len(cells) != 1 or _word_attribute(span, "val") != str(column_count) or _word_visible_text(cells[0])[0].strip() != WIDGET_TREE_EMPTY_LABEL:
                errors.append(
                    issue(
                        "document.widget_tree_empty_state",
                        path,
                        f"Empty reuse-only tree requires the fixed {column_count}-column merged row.",
                    )
                )
            continue
        if len(data_rows) != len(expected_rows):
            errors.append(issue("document.widget_tree_row_count", path, f"Expected {len(expected_rows)} data rows, found {len(data_rows)}."))
        for row_index, (row, expected) in enumerate(zip(data_rows, expected_rows)):
            row_path = f"{path}.rows[{row_index}]"
            row_properties = row.find("./w:trPr", WORD_NAMESPACES)
            cant_split = row_properties.find("./w:cantSplit", WORD_NAMESPACES) if row_properties is not None else None
            if cant_split is None or not _word_property_enabled(cant_split):
                errors.append(issue("document.widget_tree_row_split", row_path, "WidgetTree data rows must set cantSplit."))
            _validate_widget_tree_row_height(row, row_path, errors)
            cells = row.findall("./w:tc", WORD_NAMESPACES)
            if len(cells) != column_count:
                errors.append(
                    issue(
                        "document.widget_tree_column_count",
                        row_path,
                        f"WidgetTree data rows must contain exactly {column_count} cells.",
                    )
                )
                continue
            _validate_widget_tree_cell_widths(cells, column_widths, row_path, errors)
            if any(cell.find("./w:tcPr/w:gridSpan", WORD_NAMESPACES) is not None or cell.find("./w:tcPr/w:vMerge", WORD_NAMESPACES) is not None for cell in cells):
                errors.append(issue("document.widget_tree_column_count", row_path, "Non-empty WidgetTree rows must not merge cells."))
            actual_values = [_word_visible_text(cell)[0].strip() for cell in cells]
            expected_values = [
                expected.get("widgetName"),
                expected.get("className"),
                "true" if expected.get("isVariable") is True else "false",
            ]
            if table_format == WIDGET_TREE_TABLE_FORMAT:
                expected_values.append(expected.get("programPurpose", ""))
            if actual_values != expected_values:
                errors.append(issue("document.widget_tree_row_content", row_path, f"Expected {expected_values!r}, found {actual_values!r}."))
            paragraph = cells[0].find("./w:p", WORD_NAMESPACES)
            indent = paragraph.find("./w:pPr/w:ind", WORD_NAMESPACES) if paragraph is not None else None
            actual_left = _word_attribute(indent, "left")
            expected_left = int(expected.get("depth", 0)) * int(indent_step or 0)
            if actual_left is None:
                parsed_left: int | None = 0
            elif re.fullmatch(r"[+-]?\d+", actual_left):
                parsed_left = int(actual_left)
            else:
                parsed_left = None
            if parsed_left != expected_left:
                found_left = actual_left if parsed_left is None else parsed_left
                errors.append(issue("document.widget_tree_depth", row_path, f"Expected left indent {expected_left}, found {found_left!r}."))
    return [_widget_tree_table_summary(asset) for asset in expected_assets]


def _canonical_statement(kind: str, fields: tuple[tuple[str, Any], ...]) -> str:
    rendered = "; ".join(
        f"{name}={json.dumps(value, ensure_ascii=False, separators=(',', ':'))}" for name, value in fields
    )
    return f"{kind}: {rendered}"


def expected_coverage(handoff: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {
        "targetAssetPaths": set(),
        "programVariableIdentifiers": set(),
        "collectionIdentifiers": set(),
        "collectionEntryClasses": set(),
        "stateModelIdentifiers": set(),
        "stateAxisIdentifiers": set(),
        "stateIdentifiers": set(),
        "stateControlIdentifiers": set(),
        "stateControlKinds": set(),
        "stateControlDescriptions": set(),
        "stateControlTargetStateIdentifiers": set(),
        "stateBranchWidgetIdentifiers": set(),
        "stateOutcomeWidgetIdentifiers": set(),
        "acceptedDeviationIdentifiers": set(),
        "stateControlGapIdentifiers": set(),
        "semanticRelationshipStatements": set(),
    }
    target = handoff.get("target") if isinstance(handoff.get("target"), dict) else {}
    for asset_path in target.get("assetPaths", []):
        if not isinstance(asset_path, str):
            continue
        values["targetAssetPaths"].add(asset_path)
        values["semanticRelationshipStatements"].add(
            _canonical_statement("Target asset", (("assetPath", asset_path),))
        )
    for asset in handoff.get("assets", []):
        if not isinstance(asset, dict):
            continue
        asset_path = asset.get("assetPath")
        for variable in asset.get("programVariables", []):
            if not isinstance(variable, dict):
                continue
            if isinstance(variable.get("widgetName"), str):
                values["programVariableIdentifiers"].add(variable["widgetName"])
            values["semanticRelationshipStatements"].add(
                _canonical_statement(
                    "Program variable",
                    (
                        ("assetPath", asset_path),
                        ("id", variable.get("id")),
                        ("widgetName", variable.get("widgetName")),
                        ("widgetClass", variable.get("widgetClass")),
                        ("purpose", variable.get("purpose")),
                    ),
                )
            )
        for collection in asset.get("collections", []):
            if not isinstance(collection, dict):
                continue
            for field in ("id", "widgetName"):
                if isinstance(collection.get(field), str):
                    values["collectionIdentifiers"].add(collection[field])
            if isinstance(collection.get("entryWidgetClass"), str):
                values["collectionEntryClasses"].add(collection["entryWidgetClass"])
            values["semanticRelationshipStatements"].add(
                _canonical_statement(
                    "Collection EntryClass",
                    (
                        ("assetPath", asset_path),
                        ("collectionId", collection.get("id")),
                        ("widgetName", collection.get("widgetName")),
                        ("entryWidgetClass", collection.get("entryWidgetClass")),
                    ),
                )
            )
        for model in asset.get("states", []):
            if not isinstance(model, dict):
                continue
            if isinstance(model.get("id"), str):
                values["stateModelIdentifiers"].add(model["id"])
            for control in model.get("controlInputs", []):
                if not isinstance(control, dict):
                    continue
                if isinstance(control.get("id"), str):
                    values["stateControlIdentifiers"].add(control["id"])
                if isinstance(control.get("kind"), str):
                    values["stateControlKinds"].add(control["kind"])
                if isinstance(control.get("description"), str):
                    values["stateControlDescriptions"].add(control["description"])
                for target_state_id in control.get("targetStateIds", []):
                    if isinstance(target_state_id, str):
                        values["stateControlTargetStateIdentifiers"].add(target_state_id)
                values["semanticRelationshipStatements"].add(
                    _canonical_statement(
                        "State control",
                        (
                            ("assetPath", asset_path),
                            ("stateModelId", model.get("id")),
                            ("controlId", control.get("id")),
                            ("axisId", control.get("axisId")),
                            (
                                "targetStateIds",
                                sorted(item for item in control.get("targetStateIds", []) if isinstance(item, str)),
                            ),
                            ("kind", control.get("kind")),
                            ("description", control.get("description")),
                        ),
                    )
                )
            for axis in model.get("axes", []):
                if not isinstance(axis, dict):
                    continue
                if isinstance(axis.get("id"), str):
                    values["stateAxisIdentifiers"].add(axis["id"])
                for state in axis.get("states", []):
                    if not isinstance(state, dict):
                        continue
                    if isinstance(state.get("id"), str):
                        values["stateIdentifiers"].add(state["id"])
                    if model.get("implementationStrategy") == "exclusive-panel-branches":
                        for binding in state.get("actualSavedVisibilityBindings", []):
                            if not isinstance(binding, dict):
                                continue
                            if isinstance(binding.get("widgetName"), str):
                                values["stateBranchWidgetIdentifiers"].add(binding["widgetName"])
                            values["semanticRelationshipStatements"].add(
                                _canonical_statement(
                                    "State branch",
                                    (
                                        ("assetPath", asset_path),
                                        ("stateModelId", model.get("id")),
                                        ("axisId", axis.get("id")),
                                        ("stateId", state.get("id")),
                                        ("isDefault", state.get("isDefault")),
                                        ("assetId", binding.get("assetId")),
                                        ("widgetName", binding.get("widgetName")),
                                    ),
                                )
                            )
                    if model.get("implementationStrategy") == "shared-tree-properties":
                        for outcome in state.get("runtimeVisibilityOutcomes", []):
                            if not isinstance(outcome, dict):
                                continue
                            if isinstance(outcome.get("widgetName"), str):
                                values["stateOutcomeWidgetIdentifiers"].add(outcome["widgetName"])
                            values["semanticRelationshipStatements"].add(
                                _canonical_statement(
                                    "State outcome",
                                    (
                                        ("assetPath", asset_path),
                                        ("stateModelId", model.get("id")),
                                        ("axisId", axis.get("id")),
                                        ("stateId", state.get("id")),
                                        ("isDefault", state.get("isDefault")),
                                        ("assetId", outcome.get("assetId")),
                                        ("widgetName", outcome.get("widgetName")),
                                        ("visibility", outcome.get("visibility")),
                                    ),
                                )
                            )
    for deviation in handoff.get("deviations", []):
        if not isinstance(deviation, dict) or deviation.get("status") != "accepted":
            continue
        for field in ("id",):
            if isinstance(deviation.get(field), str):
                values["acceptedDeviationIdentifiers"].add(deviation[field])
        for field in ("affectedAssetIds", "affectedRequirementRefs"):
            for identifier in deviation.get(field, []):
                if isinstance(identifier, str):
                    values["acceptedDeviationIdentifiers"].add(identifier)
        values["semanticRelationshipStatements"].add(
            _canonical_statement(
                "Accepted deviation",
                (
                    ("id", deviation.get("id")),
                    ("status", deviation.get("status")),
                    ("impact", deviation.get("impact")),
                    (
                        "affectedAssetIds",
                        sorted(item for item in deviation.get("affectedAssetIds", []) if isinstance(item, str)),
                    ),
                    (
                        "affectedRequirementRefs",
                        sorted(item for item in deviation.get("affectedRequirementRefs", []) if isinstance(item, str)),
                    ),
                ),
            )
        )
    for gap in handoff.get("gaps", []):
        if not isinstance(gap, dict):
            continue
        for field in ("code", "stateModelId", "controlInputId"):
            if isinstance(gap.get(field), str):
                values["stateControlGapIdentifiers"].add(gap[field])
        values["semanticRelationshipStatements"].add(
            _canonical_statement(
                "State-control gap",
                (
                    ("code", gap.get("code")),
                    ("stateModelId", gap.get("stateModelId")),
                    ("controlInputId", gap.get("controlInputId")),
                ),
            )
        )
    return {key: sorted(items) for key, items in values.items()}


def _natural_page_key(path: Path) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name))


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as stream:
            header = stream.read(24)
            if len(header) != 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
                return None
            width, height = struct.unpack(">II", header[16:24])
            stream.seek(-len(PNG_IEND), os.SEEK_END)
            if stream.read(len(PNG_IEND)) != PNG_IEND:
                return None
            return width, height
    except (OSError, ValueError):
        return None


def inspect_pages(
    render_dir: Path,
    errors: list[dict[str, str]],
    *,
    source_modified_ns: int | None = None,
) -> list[dict[str, Any]]:
    page_paths = sorted(render_dir.glob("*.png"), key=_natural_page_key) if render_dir.is_dir() else []
    if not page_paths:
        errors.append(issue("render.pages_missing", "$.pages", "Render directory contains no PNG pages."))
        return []
    pages: list[dict[str, Any]] = []
    for index, page_path in enumerate(page_paths, 1):
        stat = page_path.stat()
        dimensions = _png_dimensions(page_path)
        if dimensions is None:
            errors.append(issue("render.page_invalid", f"$.pages[{index - 1}]", f"{page_path.name} is not a structurally valid PNG."))
            continue
        width, height = dimensions
        if width < 64 or height < 64:
            errors.append(issue("render.page_dimensions", f"$.pages[{index - 1}]", f"{page_path.name} is too small to be a rendered document page."))
            continue
        if source_modified_ns is not None and stat.st_mtime_ns < source_modified_ns:
            errors.append(issue("render.page_stale", f"$.pages[{index - 1}]", f"{page_path.name} predates the source DOCX."))
            continue
        pages.append(
            {
                "pageNumber": index,
                "fileName": page_path.name,
                "sha256": sha256_file(page_path),
                "byteSize": stat.st_size,
                "width": width,
                "height": height,
            }
        )
    return pages


def _canonical_pdf_name(docx_path: Path) -> str:
    return f"{docx_path.stem}.canonical.pdf"


def _pdf_is_structurally_valid(path: Path) -> bool:
    try:
        size = path.stat().st_size
        if size < 32:
            return False
        with path.open("rb") as stream:
            if stream.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
                return False
            stream.seek(max(0, size - 4096))
            return PDF_EOF in stream.read()
    except OSError:
        return False


def inspect_canonical_pdf(
    docx_path: Path,
    render_dir: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    pdf_path = render_dir / _canonical_pdf_name(docx_path)
    if not pdf_path.is_file():
        errors.append(issue("render.pdf_missing", "$.canonicalPdf", "Fresh canonical PDF is missing from the render directory."))
        return None
    if not _pdf_is_structurally_valid(pdf_path):
        errors.append(issue("render.pdf_invalid", "$.canonicalPdf", "Canonical PDF is not structurally valid."))
        return None
    stat = pdf_path.stat()
    if stat.st_mtime_ns < docx_path.stat().st_mtime_ns:
        errors.append(issue("render.pdf_stale", "$.canonicalPdf", "Canonical PDF predates the source DOCX."))
        return None
    return {
        "fileName": pdf_path.name,
        "sha256": sha256_file(pdf_path),
        "byteSize": stat.st_size,
    }


def _create_inherited_work_directory(parent: Path, prefix: str) -> Path:
    """Create a private-name work directory while inheriting the parent's executable ACL."""

    for _ in range(10):
        candidate = parent / f".{prefix}-{uuid.uuid4().hex}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"Unable to allocate a fresh {prefix} work directory.")


def _cleanup_work_directory(path: Path, errors: list[dict[str, str]], evidence_path: str) -> None:
    try:
        shutil.rmtree(path)
    except OSError as error:
        errors.append(issue("render.temporary_cleanup", evidence_path, f"Unable to remove temporary render workspace: {error}"))


def convert_docx_to_pdf(
    docx_path: Path,
    render_dir: Path,
    soffice_path: Path,
    errors: list[dict[str, str]],
    *,
    destination_path: Path | None = None,
) -> Path | None:
    """Use the supplied LibreOffice executable to persist a fresh canonical PDF."""

    if not render_dir.is_dir():
        errors.append(issue("render.directory_missing", "$.canonicalPdf", "Render directory does not exist."))
        return None
    temporary_root: Path | None = None
    try:
        temporary_root = _create_inherited_work_directory(render_dir.parent, "nextgame-docx-convert")
        output_dir = temporary_root / "output"
        profile_dir = temporary_root / "profile"
        output_dir.mkdir()
        profile_dir.mkdir()
        completed = subprocess.run(
            [
                str(soffice_path),
                f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
                "--invisible",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        generated_pdf = output_dir / docx_path.with_suffix(".pdf").name
        if completed.returncode != 0 or not generated_pdf.is_file():
            errors.append(
                issue(
                    "render.pdf_conversion",
                    "$.canonicalPdf",
                    "LibreOffice headless conversion did not produce the expected PDF from the current DOCX.",
                )
            )
            return None
        if not _pdf_is_structurally_valid(generated_pdf):
            errors.append(issue("render.pdf_invalid", "$.canonicalPdf", "LibreOffice output is not a structurally valid PDF."))
            return None

        destination = destination_path or (render_dir / _canonical_pdf_name(docx_path))
        if destination.resolve().parent != render_dir.resolve():
            errors.append(issue("render.pdf_destination", "$.canonicalPdf", "PDF destination must remain directly inside the render directory."))
            return None
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=render_dir,
            delete=False,
        ) as temporary_output:
            temporary_destination = Path(temporary_output.name)
        try:
            shutil.copyfile(generated_pdf, temporary_destination)
            os.replace(temporary_destination, destination)
        finally:
            temporary_destination.unlink(missing_ok=True)
        return destination
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(issue("render.pdf_conversion", "$.canonicalPdf", f"Unable to convert DOCX to PDF: {error}"))
        return None
    finally:
        if temporary_root is not None and temporary_root.exists():
            _cleanup_work_directory(temporary_root, errors, "$.canonicalPdf")


def render_pdf_to_review_pages(
    pdf_path: Path,
    render_dir: Path,
    pdftoppm_path: Path,
    errors: list[dict[str, str]],
    *,
    persist: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Rasterize the canonical PDF and optionally persist the authoritative review pages."""

    temporary_root: Path | None = None
    try:
        version_probe = subprocess.run(
            [str(pdftoppm_path), "-v"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        version_output = "\n".join(
            part.strip() for part in (version_probe.stdout, version_probe.stderr) if part and part.strip()
        ).strip()
        if version_probe.returncode != 0 or "pdftoppm" not in version_output.lower():
            errors.append(issue("render.pdf_rasterizer_probe", "$.canonicalPageRender", "pdftoppm -v did not identify a working rasterizer."))
            return None, []
        temporary_root = _create_inherited_work_directory(render_dir.parent, "nextgame-pdf-pages")
        prefix = temporary_root / "canonical-page"
        completed = subprocess.run(
            [str(pdftoppm_path), "-png", "-r", "150", str(pdf_path), str(prefix)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        generated_paths = sorted(temporary_root.glob("canonical-page-*.png"), key=_natural_page_key)
        if completed.returncode != 0 or not generated_paths:
            errors.append(issue("render.pdf_rasterization", "$.canonicalPageRender", "pdftoppm did not rasterize the canonical PDF."))
            return None, []
        for page_path in generated_paths:
            dimensions = _png_dimensions(page_path)
            if dimensions is None or dimensions[0] < 64 or dimensions[1] < 64:
                errors.append(issue("render.pdf_rasterization", "$.canonicalPageRender", "pdftoppm emitted an invalid PNG page."))
                return None, []

        if persist:
            unexpected = [
                path.name
                for path in render_dir.glob("*.png")
                if re.fullmatch(r"page-\d+\.png", path.name) is None
            ]
            if unexpected:
                errors.append(
                    issue(
                        "render.directory_not_fresh",
                        "$.pages",
                        f"Render directory contains unexpected PNG files: {sorted(unexpected)}.",
                    )
                )
                return None, []
            final_names = {f"page-{index}.png" for index in range(1, len(generated_paths) + 1)}
            temporary_destinations: list[tuple[Path, Path]] = []
            try:
                for index, generated_path in enumerate(generated_paths, 1):
                    destination = render_dir / f"page-{index}.png"
                    with tempfile.NamedTemporaryFile(
                        prefix=f".{destination.name}.", suffix=".tmp", dir=render_dir, delete=False
                    ) as temporary_output:
                        temporary_destination = Path(temporary_output.name)
                    shutil.copyfile(generated_path, temporary_destination)
                    temporary_destinations.append((temporary_destination, destination))
                for temporary_destination, destination in temporary_destinations:
                    os.replace(temporary_destination, destination)
                for stale_path in render_dir.glob("page-*.png"):
                    if stale_path.name not in final_names:
                        stale_path.unlink()
            finally:
                for temporary_destination, _ in temporary_destinations:
                    temporary_destination.unlink(missing_ok=True)
            page_records = inspect_pages(
                render_dir,
                errors,
                source_modified_ns=pdf_path.stat().st_mtime_ns,
            )
        else:
            page_records = []
            for index, generated_path in enumerate(generated_paths, 1):
                stat = generated_path.stat()
                width, height = _png_dimensions(generated_path) or (0, 0)
                page_records.append(
                    {
                        "pageNumber": index,
                        "fileName": f"page-{index}.png",
                        "sha256": sha256_file(generated_path),
                        "byteSize": stat.st_size,
                        "width": width,
                        "height": height,
                    }
                )
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(issue("render.pdf_rasterization", "$.canonicalPageRender", f"Unable to rasterize canonical PDF: {error}"))
        return None, []
    finally:
        if temporary_root is not None and temporary_root.exists():
            _cleanup_work_directory(temporary_root, errors, "$.canonicalPageRender")

    return (
        {
            "tool": "pdftoppm",
            "version": version_output.splitlines()[0].strip(),
            "dpi": 150,
            "pageCount": len(page_records),
            "authoritativePagesGenerated": True,
        },
        page_records,
    )


def probe_soffice(soffice_path: Path | None, errors: list[dict[str, str]]) -> str | None:
    if soffice_path is None or not soffice_path.is_file():
        errors.append(issue("render.soffice_missing", "$.renderer", "Final verification requires an available soffice executable."))
        return None
    try:
        completed = subprocess.run(
            [str(soffice_path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(issue("render.soffice_probe", "$.renderer", f"Unable to execute soffice --version: {error}"))
        return None
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip()).strip()
    if completed.returncode != 0 or "libreoffice" not in output.lower():
        errors.append(issue("render.soffice_probe", "$.renderer", "soffice --version did not identify a working LibreOffice renderer."))
        return None
    return output.splitlines()[0].strip()


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(root)))) == os.path.normcase(str(root))
    except ValueError:
        return False


def validate_output_location(handoff: dict[str, Any], docx_path: Path, errors: list[dict[str, str]]) -> None:
    output = handoff.get("output") if isinstance(handoff.get("output"), dict) else {}
    env_name = output.get("rootEnvironmentVariable")
    if env_name != OUTPUT_ROOT_ENVIRONMENT_VARIABLE:
        errors.append(issue("output.environment_name", "$.output.rootEnvironmentVariable", "Unexpected document output environment variable."))
        return
    raw_root = os.environ.get(env_name, "").strip()
    if not raw_root:
        errors.append(issue("output.environment_missing", "$.output.rootEnvironmentVariable", f"{env_name} is not set in the current process."))
        return
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        errors.append(issue("output.root_missing", "$.output.rootEnvironmentVariable", "The configured document output root does not exist."))
        return
    if not _path_is_within(docx_path.resolve(), root):
        errors.append(issue("output.path_scope", "$.document.fileName", "DOCX must be located beneath the configured document output root."))


def create_render_evidence(
    *,
    docx_path: Path,
    render_dir: Path,
    soffice_path: Path | None,
    rendered_at: str,
    pdftoppm_path: Path | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    rendered_time = parse_aware_iso8601(rendered_at, "$.renderedAt", errors)
    if not docx_path.is_file():
        errors.append(issue("document.missing", "$.sourceDocument", "DOCX file does not exist."))
        return None, errors
    renderer_version = probe_soffice(soffice_path, errors)
    if renderer_version is not None and soffice_path is not None:
        convert_docx_to_pdf(docx_path, render_dir, soffice_path, errors)
    canonical_pdf = inspect_canonical_pdf(docx_path, render_dir, errors)
    canonical_page_render = None
    pages: list[dict[str, Any]] = []
    if pdftoppm_path is None:
        errors.append(issue("render.pdf_rasterizer_missing", "$.canonicalPageRender", "Render evidence requires an available pdftoppm executable."))
    elif canonical_pdf is not None:
        canonical_page_render, pages = render_pdf_to_review_pages(
            render_dir / canonical_pdf["fileName"],
            render_dir,
            pdftoppm_path,
            errors,
            persist=True,
        )
    if rendered_time is not None:
        document_modified = datetime.fromtimestamp(docx_path.stat().st_mtime, tz=timezone.utc)
        if rendered_time < document_modified:
            errors.append(issue("render.timestamp", "$.renderedAt", "renderedAt predates the source DOCX."))
    if errors:
        return None, errors
    evidence = {
        "version": "0.1",
        "renderedAt": rendered_at,
        "renderer": {"name": "LibreOffice/soffice", "version": renderer_version},
        "sourceDocument": {"fileName": docx_path.name, "sha256": sha256_file(docx_path)},
        "canonicalPdf": canonical_pdf,
        "pages": pages,
    }
    evidence["canonicalPageRender"] = canonical_page_render
    errors.extend(validate_schema_instance(evidence, load_json(RENDER_EVIDENCE_SCHEMA)))
    return (evidence if not errors else None), errors


def validate_render_evidence(
    evidence: Any,
    *,
    docx_path: Path,
    render_dir: Path,
    soffice_path: Path | None,
    pdftoppm_path: Path | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors = validate_schema_instance(evidence, load_json(RENDER_EVIDENCE_SCHEMA))
    if not isinstance(evidence, dict):
        return None, errors
    rendered_time = parse_aware_iso8601(evidence.get("renderedAt"), "$.renderedAt", errors)
    if not docx_path.is_file():
        errors.append(issue("document.missing", "$.sourceDocument", "DOCX file does not exist."))
        return None, errors
    renderer_version = probe_soffice(soffice_path, errors)
    source = evidence.get("sourceDocument") if isinstance(evidence.get("sourceDocument"), dict) else {}
    expected_source = {"fileName": docx_path.name, "sha256": sha256_file(docx_path)}
    if source != expected_source:
        errors.append(issue("render.source_mismatch", "$.sourceDocument", "Render evidence is not bound to the current DOCX."))
    renderer = evidence.get("renderer") if isinstance(evidence.get("renderer"), dict) else {}
    if renderer_version is not None and renderer.get("version") != renderer_version:
        errors.append(issue("render.renderer_version", "$.renderer.version", "Render evidence renderer version differs from the working soffice executable."))
    actual_pages = inspect_pages(render_dir, errors, source_modified_ns=docx_path.stat().st_mtime_ns)
    if evidence.get("pages") != actual_pages:
        errors.append(issue("render.pages_mismatch", "$.pages", "Render evidence pages do not exactly match the current render directory."))
    actual_pdf = inspect_canonical_pdf(docx_path, render_dir, errors)
    if evidence.get("canonicalPdf") != actual_pdf:
        errors.append(issue("render.pdf_mismatch", "$.canonicalPdf", "Render evidence does not match the fresh canonical PDF."))
    recorded_page_render = evidence.get("canonicalPageRender")
    if pdftoppm_path is None:
        errors.append(issue("render.pdf_rasterizer_missing", "$.canonicalPageRender", "Final validation requires the pdftoppm used for authoritative review pages."))
    elif actual_pdf is not None:
        actual_page_render, canonical_pages = render_pdf_to_review_pages(
            render_dir / actual_pdf["fileName"],
            render_dir,
            pdftoppm_path,
            errors,
            persist=False,
        )
        if recorded_page_render != actual_page_render:
            errors.append(issue("render.pdf_page_render_mismatch", "$.canonicalPageRender", "Canonical PDF page comparison no longer matches."))
        if actual_pages != canonical_pages:
            errors.append(issue("render.pdf_page_content", "$.pages", "Supplied review pages do not exactly match a fresh rasterization of the canonical PDF."))
    if soffice_path is not None and renderer_version is not None and pdftoppm_path is not None:
        verification_pdf = render_dir / f".{docx_path.stem}.verification-{uuid.uuid4().hex}.pdf"
        try:
            fresh_pdf = convert_docx_to_pdf(
                docx_path,
                render_dir,
                soffice_path,
                errors,
                destination_path=verification_pdf,
            )
            if fresh_pdf is not None:
                fresh_page_render, fresh_docx_pages = render_pdf_to_review_pages(
                    fresh_pdf,
                    render_dir,
                    pdftoppm_path,
                    errors,
                    persist=False,
                )
                if recorded_page_render != fresh_page_render:
                    errors.append(issue("render.docx_page_renderer", "$.canonicalPageRender", "Fresh DOCX conversion used different page-render provenance."))
                if actual_pages != fresh_docx_pages:
                    errors.append(issue("render.docx_page_content", "$.pages", "Review pages do not exactly match a fresh headless conversion of the current DOCX."))
        finally:
            verification_pdf.unlink(missing_ok=True)
    if rendered_time is not None:
        document_modified = datetime.fromtimestamp(docx_path.stat().st_mtime, tz=timezone.utc)
        if rendered_time < document_modified:
            errors.append(issue("render.timestamp", "$.renderedAt", "Render evidence predates the current DOCX."))
    return (evidence if not errors else None), errors


def _check_identifier_coverage(text: str, coverage: dict[str, list[str]], errors: list[dict[str, str]]) -> None:
    for field, identifiers in coverage.items():
        # Canonical semantic relationship statements are deterministic,
        # machine-facing audit evidence.  They stay in the content contract and
        # verification coverage, but the programmer-facing DOCX must not repeat
        # them as a trace appendix.
        if field == "semanticRelationshipStatements":
            continue
        missing = [identifier for identifier in identifiers if identifier not in text]
        if missing:
            errors.append(
                issue(
                    "document.identifier_coverage",
                    f"$.coverage.{field}",
                    f"DOCX is missing identifiers: {missing}.",
                )
            )


def _check_forbidden_document_policy(text: str, errors: list[dict[str, str]]) -> None:
    normalized = re.sub(r"\s+", " ", text)
    for policy_field, patterns in FORBIDDEN_DOCUMENT_POLICIES:
        if any(pattern.search(normalized) for pattern in patterns):
            errors.append(
                issue(
                    "document.forbidden_policy",
                    f"$.contentPolicy.{policy_field}",
                    f"DOCX contains details forbidden by {policy_field}.",
                )
            )


def _check_forbidden_visibility_evidence(
    text: str,
    handoff: dict[str, Any],
    coverage: dict[str, list[str]],
    errors: list[dict[str, str]],
) -> None:
    allowed_outcomes = [
        statement
        for statement in coverage.get("semanticRelationshipStatements", [])
        if statement.startswith("State outcome: ")
    ]
    exclusive_saved_pairs = {
        (binding["widgetName"], binding["visibility"])
        for asset in handoff.get("assets", [])
        if isinstance(asset, dict)
        for model in asset.get("states", [])
        if isinstance(model, dict) and model.get("implementationStrategy") == "exclusive-panel-branches"
        for axis in model.get("axes", [])
        if isinstance(axis, dict)
        for state in axis.get("states", [])
        if isinstance(state, dict)
        for binding in state.get("actualSavedVisibilityBindings", [])
        if isinstance(binding, dict)
        and isinstance(binding.get("widgetName"), str)
        and isinstance(binding.get("visibility"), str)
    }
    for paragraph in text.splitlines():
        if STATE_BRANCH_VISIBILITY_PATTERN.search(paragraph):
            errors.append(
                issue(
                    "document.saved_visibility_leak",
                    "$.contentPolicy.staticDesignerConfiguration",
                    "DOCX State branch text must not expose saved or Designer Visibility.",
                )
            )
            return
        remainder = paragraph
        for statement in allowed_outcomes:
            remainder = remainder.replace(statement, "")
        if any(
            re.search(rf"(?<![A-Za-z0-9_]){re.escape(widget_name)}(?![A-Za-z0-9_])", remainder)
            and re.search(rf"(?<![A-Za-z0-9_]){re.escape(visibility)}(?![A-Za-z0-9_])", remainder)
            for widget_name, visibility in exclusive_saved_pairs
        ):
            errors.append(
                issue(
                    "document.saved_visibility_leak",
                    "$.contentPolicy.staticDesignerConfiguration",
                    "DOCX exposes an exclusive branch Widget together with its saved Designer Visibility.",
                )
            )
            return
        if any(pattern.search(remainder) for pattern in SAVED_VISIBILITY_PATTERNS):
            errors.append(
                issue(
                    "document.saved_visibility_leak",
                    "$.contentPolicy.staticDesignerConfiguration",
                    "DOCX contains actual, saved, readback, or Designer Visibility evidence.",
                )
            )
            return


def create_document_verification(
    handoff: dict[str, Any],
    *,
    handoff_path: Path,
    build_acceptance: Any,
    build_acceptance_path: Path,
    requirement: Any,
    requirement_path: Path,
    bundle: Any,
    bundle_path: Path,
    readback: Any,
    readback_path: Path,
    docx_path: Path,
    render_dir: Path,
    render_evidence: Any,
    render_evidence_path: Path,
    reviewed_by: str,
    reviewed_at: str,
    reviewed_page_files: list[str],
    soffice_path: Path | None,
    verified_at: str,
    pdftoppm_path: Path | None = None,
    document_content: Any | None = None,
    document_content_path: Path | None = None,
    verification_version: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    errors = validate_schema_instance(handoff, load_json(HANDOFF_SCHEMA))
    errors.extend(
        validate_build_acceptance(
            build_acceptance,
            load_json(BUILD_ACCEPTANCE_SCHEMA),
            acceptance_path=build_acceptance_path,
            requirement=requirement,
            requirement_path=requirement_path,
            bundle=bundle,
            bundle_path=bundle_path,
            readback=readback,
            readback_path=readback_path,
        )["errors"]
    )
    errors.extend(
        validate_acceptance_handoff_binding(
            build_acceptance,
            build_acceptance_path,
            handoff,
        )["errors"]
    )
    if docx_path.name != handoff.get("output", {}).get("fileName"):
        errors.append(issue("document.file_name", "$.document.fileName", "DOCX filename must exactly match UIProgramHandoff.output.fileName."))
    if not docx_path.is_file():
        errors.append(issue("document.missing", "$.document", "DOCX file does not exist."))
        return None, errors
    validate_output_location(handoff, docx_path, errors)
    review_time = parse_aware_iso8601(reviewed_at, "$.visualReview.reviewedAt", errors)
    verified_time = parse_aware_iso8601(verified_at, "$.verifiedAt", errors)
    if not reviewed_by:
        errors.append(issue("review.reviewer", "$.visualReview.reviewedBy", "Page review requires reviewedBy."))
    valid_render, render_errors = validate_render_evidence(
        render_evidence,
        docx_path=docx_path,
        render_dir=render_dir,
        soffice_path=soffice_path,
        pdftoppm_path=pdftoppm_path,
    )
    errors.extend(render_errors)
    rendered_time = None
    if isinstance(render_evidence, dict):
        rendered_time = parse_aware_iso8601(render_evidence.get("renderedAt"), "$.renderedAt", errors)
    if review_time is not None and rendered_time is not None and review_time < rendered_time:
        errors.append(issue("review.timestamp", "$.visualReview.reviewedAt", "Page review cannot precede rendering."))
    if verified_time is not None and review_time is not None and verified_time < review_time:
        errors.append(issue("verification.timestamp", "$.verifiedAt", "Verification cannot precede page review."))

    content_version = document_content.get("version") if isinstance(document_content, dict) else None
    version = verification_version or (content_version if content_version in {"0.3", "0.4"} else "0.2")
    table_summaries: list[dict[str, Any]] = []
    if version in {"0.3", "0.4"}:
        if not isinstance(document_content, dict) or document_content_path is None:
            errors.append(
                issue(
                    "document.content_contract_missing",
                    "$.documentContent",
                    f"Document verification {version} requires program-document-content.json.",
                )
            )
        else:
            errors.extend(validate_schema_instance(document_content, load_json(PROGRAM_DOCUMENT_CONTENT_SCHEMA)))
            if document_content.get("version") != version:
                errors.append(
                    issue(
                        "document.content_contract_version",
                        "$.documentContent.version",
                        f"Document verification {version} requires content contract {version}.",
                    )
                )
            expected_handoff_binding = {"fileName": handoff_path.name, "sha256": sha256_file(handoff_path)}
            if document_content.get("handoff") != expected_handoff_binding:
                errors.append(issue("document.content_contract_binding", "$.documentContent.handoff", "Content contract is not bound to the current handoff."))
            expected_content_coverage = expected_coverage(handoff)
            expected_statements = expected_content_coverage.pop("semanticRelationshipStatements")
            if document_content.get("requiredIdentifiers") != expected_content_coverage:
                errors.append(issue("document.content_contract_identifiers", "$.documentContent.requiredIdentifiers", "Content contract identifiers differ from the current handoff."))
            if document_content.get("requiredSemanticRelationshipStatements") != expected_statements:
                errors.append(issue("document.content_contract_semantics", "$.documentContent.requiredSemanticRelationshipStatements", "Content contract semantic statements differ from the current handoff."))
            expected_trees, tree_errors = project_widget_tree_tables(
                readback,
                handoff.get("assets", []),
                content_version=version,
            )
            errors.extend(tree_errors)
            if document_content.get("widgetTreeTables") != expected_trees:
                errors.append(issue("document.content_contract_tree", "$.documentContent.widgetTreeTables", "Content contract WidgetTree rows differ from current readback."))
    elif version != "0.2":
        errors.append(issue("verification.version", "$.version", f"Unsupported document verification version {version!r}."))

    try:
        text = extract_docx_text(docx_path)
        policy_text = extract_docx_policy_text(docx_path)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        errors.append(issue("document.read", "$.document", str(error)))
        text = ""
        policy_text = ""
    coverage = expected_coverage(handoff)
    _check_identifier_coverage(text, coverage, errors)
    _check_forbidden_document_policy(policy_text, errors)
    _check_forbidden_visibility_evidence(policy_text, handoff, coverage, errors)
    if version == "0.4":
        _check_embedded_identifier_styles(docx_path, errors)
        if isinstance(document_content, dict):
            _check_v04_asset_structure(docx_path, document_content, errors)
    if version in {"0.3", "0.4"} and isinstance(document_content, dict):
        table_summaries = inspect_widget_tree_tables(docx_path, document_content, errors)

    pages = valid_render.get("pages", []) if isinstance(valid_render, dict) else []
    page_files = [page["fileName"] for page in pages]
    if len(reviewed_page_files) != len(set(reviewed_page_files)) or sorted(reviewed_page_files) != sorted(page_files):
        errors.append(issue("review.page_coverage", "$.visualReview.reviewedPageFiles", "Explicitly reviewed pages must exactly cover every current rendered page once."))
    if errors:
        return None, errors

    verification = {
        "version": version,
        "verificationId": f"document:{handoff['handoffId']}",
        "verifiedAt": verified_at,
        "status": "passed",
        "handoff": {"handoffId": handoff["handoffId"], "sha256": sha256_file(handoff_path)},
        "document": {"fileName": docx_path.name, "sha256": sha256_file(docx_path)},
        "render": {
            "renderer": "LibreOffice/soffice",
            "rendererVersion": valid_render["renderer"]["version"],
            "sofficeAvailable": True,
            "evidenceSha256": sha256_file(render_evidence_path),
            "canonicalPdf": valid_render["canonicalPdf"],
            "pageCount": len(pages),
            "pages": pages,
        },
        "coverage": coverage,
        "visualReview": {
            "reviewedBy": reviewed_by,
            "reviewedAt": reviewed_at,
            "reviewedPageFiles": page_files,
            "allPagesReviewed": True,
        },
    }
    if "canonicalPageRender" in valid_render:
        verification["render"]["canonicalPageRender"] = valid_render["canonicalPageRender"]
    if version in {"0.3", "0.4"} and isinstance(document_content, dict) and document_content_path is not None:
        verification["documentContent"] = {
            "version": version,
            "fileName": document_content_path.name,
            "sha256": sha256_file(document_content_path),
        }
        verification["structure"] = {
            "widgetTreeFormat": document_content.get("widgetTreeTables", {}).get("format"),
            "tableCount": len(table_summaries),
            "tables": table_summaries,
        }
    errors.extend(validate_schema_instance(verification, load_json(DOCUMENT_VERIFICATION_SCHEMA)))
    return (verification if not errors else None), errors


def validate_document_verification(
    verification: Any,
    *,
    handoff: Any,
    handoff_path: Path,
    build_acceptance: Any,
    build_acceptance_path: Path,
    requirement: Any,
    requirement_path: Path,
    bundle: Any,
    bundle_path: Path,
    readback: Any,
    readback_path: Path,
    docx_path: Path,
    render_dir: Path,
    render_evidence: Any,
    render_evidence_path: Path,
    soffice_path: Path | None,
    pdftoppm_path: Path | None = None,
    document_content: Any | None = None,
    document_content_path: Path | None = None,
) -> dict[str, Any]:
    errors = validate_schema_instance(verification, load_json(DOCUMENT_VERIFICATION_SCHEMA))
    if not isinstance(verification, dict) or not isinstance(handoff, dict):
        return result(errors)
    visual = verification.get("visualReview") if isinstance(verification.get("visualReview"), dict) else {}
    expected, generation_errors = create_document_verification(
        handoff,
        handoff_path=handoff_path,
        build_acceptance=build_acceptance,
        build_acceptance_path=build_acceptance_path,
        requirement=requirement,
        requirement_path=requirement_path,
        bundle=bundle,
        bundle_path=bundle_path,
        readback=readback,
        readback_path=readback_path,
        docx_path=docx_path,
        render_dir=render_dir,
        render_evidence=render_evidence,
        render_evidence_path=render_evidence_path,
        reviewed_by=visual.get("reviewedBy", ""),
        reviewed_at=visual.get("reviewedAt", ""),
        reviewed_page_files=visual.get("reviewedPageFiles", []),
        soffice_path=soffice_path,
        verified_at=verification.get("verifiedAt", ""),
        pdftoppm_path=pdftoppm_path,
        document_content=document_content,
        document_content_path=document_content_path,
        verification_version=verification.get("version"),
    )
    errors.extend(generation_errors)
    if expected is not None:
        if isinstance(visual.get("notes"), list):
            expected["visualReview"]["notes"] = visual["notes"]
        if verification != expected:
            errors.append(issue("verification.mismatch", "$", "document-verification.json does not match the actual handoff, DOCX, render evidence, rendered pages, and visual review."))
    return result(errors)


def _detect_soffice(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.resolve()
    for name in ("soffice", "soffice.com", "soffice.exe"):
        if found := shutil.which(name):
            return Path(found).resolve()
    return None


def _detect_pdftoppm(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.resolve()
    candidates: list[Path] = []
    for name in ("pdftoppm", "pdftoppm.exe", "pdftoppm.cmd"):
        if found := shutil.which(name):
            discovered = Path(found).resolve()
            candidates.append(discovered)
            if len(discovered.parents) >= 3 and discovered.parent.name.lower() == "override":
                dependencies_root = discovered.parents[2]
                candidates.append(
                    dependencies_root / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
                )
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve()))
        if key in seen:
            continue
        seen.add(key)
        if _pdftoppm_works(candidate):
            return candidate.resolve()
    return None


def _pdftoppm_works(candidate: Path) -> bool:
    if not candidate.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(candidate), "-v"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode == 0 and "pdftoppm" in output.lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--build-acceptance", type=Path, help="Exact post-build direct-user acceptance; required for final document verification modes.")
    parser.add_argument("--requirement", type=Path, help="Exact accepted UIRequirementSpec; required for final document verification modes.")
    parser.add_argument("--bundle", type=Path, help="Exact verified UIBuildBundle; required for final document verification modes.")
    parser.add_argument("--readback", type=Path, help="Exact verified Unreal Widget readback; required for final document verification modes.")
    parser.add_argument("--document-content", type=Path, help="Verified program-document-content.json; required for document-verification 0.3 or 0.4.")
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--render-evidence-output", type=Path, help="Fresh-convert the DOCX and write authoritative render-evidence.json.")
    mode.add_argument("--output", type=Path, help="Write document-verification.json.")
    mode.add_argument("--verification", type=Path, help="Validate an existing document-verification.json.")
    parser.add_argument("--render-evidence", type=Path, help="Render evidence previously bound to this DOCX and page set.")
    parser.add_argument("--reviewed-by")
    parser.add_argument("--reviewed-at")
    parser.add_argument("--reviewed-page", action="append", default=[], help="Rendered PNG filename explicitly reviewed; repeat for every page.")
    parser.add_argument("--soffice-path", type=Path, help="Override detected soffice executable path.")
    parser.add_argument(
        "--pdftoppm-path",
        type=Path,
        help="Override detected pdftoppm used to generate and revalidate authoritative review pages.",
    )
    args = parser.parse_args()
    output: dict[str, Any]
    try:
        soffice_path = _detect_soffice(args.soffice_path)
        pdftoppm_path = _detect_pdftoppm(args.pdftoppm_path)
        docx_path = args.docx.resolve()
        render_dir = args.render_dir.resolve()
        if args.render_evidence_output:
            evidence, errors = create_render_evidence(
                docx_path=docx_path,
                render_dir=render_dir,
                soffice_path=soffice_path,
                rendered_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                pdftoppm_path=pdftoppm_path,
            )
            output = result(errors)
            if evidence is not None:
                write_json(args.render_evidence_output, evidence)
                output["output"] = str(args.render_evidence_output)
        else:
            if any(value is None for value in (args.handoff, args.build_acceptance, args.requirement, args.bundle, args.readback, args.render_evidence)):
                raise ValueError("--handoff, --build-acceptance, --requirement, --bundle, --readback, and --render-evidence are required for document verification.")
            handoff = load_json(args.handoff)
            build_acceptance = load_json(args.build_acceptance)
            requirement = load_json(args.requirement)
            bundle = load_json(args.bundle)
            readback = load_json(args.readback)
            render_evidence = load_json(args.render_evidence)
            document_content = load_json(args.document_content) if args.document_content is not None else None
            if args.output:
                if args.document_content is None:
                    raise ValueError("--document-content is required when writing document-verification 0.4.")
                if not args.reviewed_by or not args.reviewed_at or not args.reviewed_page:
                    raise ValueError("--reviewed-by, --reviewed-at, and one --reviewed-page per rendered page are required when writing verification evidence.")
                verification, errors = create_document_verification(
                    handoff,
                    handoff_path=args.handoff.resolve(),
                    build_acceptance=build_acceptance,
                    build_acceptance_path=args.build_acceptance.resolve(),
                    requirement=requirement,
                    requirement_path=args.requirement.resolve(),
                    bundle=bundle,
                    bundle_path=args.bundle.resolve(),
                    readback=readback,
                    readback_path=args.readback.resolve(),
                    docx_path=docx_path,
                    render_dir=render_dir,
                    render_evidence=render_evidence,
                    render_evidence_path=args.render_evidence.resolve(),
                    reviewed_by=args.reviewed_by,
                    reviewed_at=args.reviewed_at,
                    reviewed_page_files=args.reviewed_page,
                    soffice_path=soffice_path,
                    verified_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                    pdftoppm_path=pdftoppm_path,
                    document_content=document_content,
                    document_content_path=args.document_content.resolve(),
                    verification_version="0.4",
                )
                output = result(errors)
                if verification is not None:
                    write_json(args.output, verification)
                    output["output"] = str(args.output)
            else:
                existing_verification = load_json(args.verification)
                if isinstance(existing_verification, dict) and existing_verification.get("version") in {"0.3", "0.4"} and args.document_content is None:
                    raise ValueError("--document-content is required when validating document-verification 0.3 or 0.4.")
                output = validate_document_verification(
                    existing_verification,
                    handoff=handoff,
                    handoff_path=args.handoff.resolve(),
                    build_acceptance=build_acceptance,
                    build_acceptance_path=args.build_acceptance.resolve(),
                    requirement=requirement,
                    requirement_path=args.requirement.resolve(),
                    bundle=bundle,
                    bundle_path=args.bundle.resolve(),
                    readback=readback,
                    readback_path=args.readback.resolve(),
                    docx_path=docx_path,
                    render_dir=render_dir,
                    render_evidence=render_evidence,
                    render_evidence_path=args.render_evidence.resolve(),
                    soffice_path=soffice_path,
                    pdftoppm_path=pdftoppm_path,
                    document_content=document_content,
                    document_content_path=args.document_content.resolve() if args.document_content is not None else None,
                )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        output = result([issue("io.read", "$", str(error))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
