"""MSCI 成份股清单解析（TXT / CSV / Excel）。

从 vite.config.ts 的 readSheetMatrix / normalizeMsciMatrix 迁入：
    * TXT/CSV：探测分隔符，UTF-8 失败回退 GBK
    * Excel：openpyxl 读 xlsx/xlsm，xlrd 读老版 xls
    * 代码列 / 名称列自动识别，最多 3000 行
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TEXT_DELIMITERS = ["\t", ",", "，", ";", "；", "|"]


def decode_text_buffer(data: bytes) -> str:
    if len(data) >= 3 and data[0] == 0xEF and data[1] == 0xBB and data[2] == 0xBF:
        return data[3:].decode("utf-8")
    text = data.decode("utf-8", errors="replace")
    if "\ufffd" not in text:
        return text
    try:
        return data.decode("gbk", errors="replace")
    except (UnicodeDecodeError, LookupError):
        return text


def parse_delimited_text(text: str) -> list[list[str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    sample = lines[:10]
    delimiter = ""
    best_cells = 0
    for candidate in TEXT_DELIMITERS:
        counts = [len(line.split(candidate)) for line in sample]
        minimum = min(counts)
        if minimum > best_cells:
            best_cells = minimum
            delimiter = candidate

    result: list[list[str]] = []
    for line in lines:
        cells = line.split(delimiter) if delimiter and best_cells >= 2 else line.split()
        result.append([cell.strip().strip('"').strip("'") for cell in cells])
    return result


def _read_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook  # 延迟导入，未安装时报错更清晰

    wb = load_workbook(data, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        matrix: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            matrix.append([str(cell) if cell is not None else "" for cell in row])
        return matrix
    finally:
        wb.close()


def _read_xls(data: bytes) -> list[list[str]]:
    import xlrd  # 老版 .xls

    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_index(0)
    return [
        [str(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
        for r in range(sheet.nrows)
    ]


def read_sheet_matrix(filename: str, data: bytes) -> list[list[str]]:
    ext = Path(filename).suffix.lower()
    is_zip = len(data) > 4 and data[0] == 0x50 and data[1] == 0x4B
    is_ole = len(data) > 4 and data[0] == 0xD0 and data[1] == 0xCF
    is_excel = ext in {".xlsx", ".xls", ".xlsm"} or is_zip or is_ole

    if is_excel:
        if ext == ".xls" and is_ole and not is_zip:
            return _read_xls(data)
        return _read_xlsx(data)

    return parse_delimited_text(decode_text_buffer(data))


def extract_code(value: str) -> str | None:
    match = re.search(r"(\d{6})", str(value or "").upper())
    return match.group(1) if match else None


def normalize_msci_matrix(matrix: list[list[str]]) -> dict[str, Any]:
    rows = [row for row in matrix if any(str(cell or "").strip() for cell in row)]
    if not rows:
        return {"columns": [], "rows": [], "warnings": ["文件里没有可识别的数据"]}

    max_cols = max(len(row) for row in rows)
    first_row_has_code = any(extract_code(cell) for cell in rows[0])
    has_header = (
        not first_row_has_code
        and len([c for c in rows[0] if str(c or "").strip()]) >= 2
        and len(rows) > 1
    )

    header_row = rows[0] if has_header else []
    data_rows = rows[1:] if has_header else rows

    code_hits = [0] * max_cols
    for row in data_rows[:500]:
        for col in range(max_cols):
            if col < len(row) and extract_code(row[col]):
                code_hits[col] += 1
    code_col = code_hits.index(max(code_hits))
    if code_hits[code_col] == 0:
        return {"columns": [], "rows": [], "warnings": ["没有找到 6 位股票代码，请确认文件内容"]}

    def header_of(index: int) -> str:
        return str(header_row[index] if index < len(header_row) and str(header_row[index]).strip() else f"列{index + 1}")

    name_col = -1
    for col in range(max_cols):
        if col == code_col:
            continue
        if re.search(r"名称|简称|股票名", str(header_row[col]) if col < len(header_row) else ""):
            name_col = col
            break
    if name_col < 0:
        best_score = 0
        for col in range(max_cols):
            if col == code_col:
                continue
            score = sum(
                1
                for row in data_rows[:100]
                if col < len(row) and re.search(r"[\u4e00-\u9fa5]", str(row[col])) and not extract_code(row[col] or "")
            )
            if score > best_score:
                best_score = score
                name_col = col
        if best_score < min(3, len(data_rows)):
            name_col = -1

    extra_cols = [
        col
        for col in range(max_cols)
        if col not in (code_col, name_col)
        and any(str(row[col] or "").strip() for row in data_rows[:50] if col < len(row))
    ]

    warnings: list[str] = []
    if not has_header:
        warnings.append("未识别到表头，已按「每行第一个 6 位代码」解析")

    code_set: set[str] = set()
    parsed_rows: list[dict[str, Any]] = []
    skipped = 0
    for row in data_rows:
        code = extract_code(row[code_col]) if code_col < len(row) else None
        if not code:
            skipped += 1
            continue
        if code in code_set:
            continue
        code_set.add(code)

        extra: dict[str, str] = {}
        for col in extra_cols:
            value = str(row[col] or "").strip() if col < len(row) else ""
            if value:
                extra[header_of(col)] = value

        parsed_rows.append(
            {
                "code": code,
                "name": str(row[name_col] or "").strip() if 0 <= name_col < len(row) else "",
                "extra": extra,
            }
        )
        if len(parsed_rows) >= 3000:
            warnings.append("单次最多导入 3000 条，已截断")
            break

    if skipped:
        warnings.append(f"跳过 {skipped} 行没有股票代码的内容")

    return {"columns": [header_of(col) for col in extra_cols], "rows": parsed_rows, "warnings": warnings}
