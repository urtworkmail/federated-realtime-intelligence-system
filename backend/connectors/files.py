"""
FRIS File Connectors
CSV and Excel file upload connectors.
Handles: encoding detection, delimiter sniffing, multi-sheet Excel,
header detection, and schema inference.
"""

import csv
import io
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseConnector, ConnectorConfig, ConnectorType, SchemaDetectionResult


class CSVConnector(BaseConnector):
    """
    CSV file connector.
    Config keys:
        file_path: str          - path to CSV file on server, OR
        file_content: str       - base64 encoded content (for uploads)
        delimiter: str          - default auto-detect
        encoding: str           - default auto-detect (utf-8, latin-1, etc.)
        has_header: bool        - default True
        skip_rows: int          - rows to skip at top, default 0
        date_columns: list      - columns to parse as dates
        watch_for_changes: bool - re-sync if file changes, default False
    """

    CONNECTOR_TYPE = ConnectorType.CSV_FILE
    DISPLAY_NAME = "CSV File"
    DESCRIPTION = "Upload or point to a CSV data file"
    ICON = "📄"
    DOCS = (
        "1. Provide a file_path (server-accessible path) or file_url (publicly downloadable link).\n"
        "2. Delimiter and encoding are auto-detected from a sample, but can be overridden if "
        "detection gets it wrong (e.g. semicolon-delimited European exports).\n"
        "3. First row is treated as column headers by default."
    )

    CONFIG_SCHEMA = {
        "required_one_of": ["file_path", "file_content"],
        "optional": ["delimiter", "encoding", "has_header", "skip_rows",
                     "date_columns", "watch_for_changes"],
        "defaults": {
            "has_header": True,
            "skip_rows": 0,
            "watch_for_changes": False
        }
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._data: Optional[List[Dict[str, Any]]] = None
        self._file_mtime: Optional[float] = None

    def _get_file_content(self) -> str:
        if self.cfg.get("file_content"):
            import base64
            content = self.cfg["file_content"]
            # Handle both raw and base64
            try:
                decoded = base64.b64decode(content).decode(self._detect_encoding(content))
                return decoded
            except Exception:
                return content

        file_path = self.cfg.get("file_path")
        if file_path and os.path.exists(file_path):
            encoding = self._detect_file_encoding(file_path)
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()

        raise ValueError("No file_path or file_content provided in connector config")

    def _detect_file_encoding(self, file_path: str) -> str:
        try:
            import chardet
            with open(file_path, "rb") as f:
                raw = f.read(10000)
            result = chardet.detect(raw)
            return result.get("encoding", "utf-8") or "utf-8"
        except ImportError:
            # Fallback: try utf-8 then latin-1
            for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
                try:
                    with open(file_path, "r", encoding=enc) as f:
                        f.read(1000)
                    return enc
                except UnicodeDecodeError:
                    continue
            return "utf-8"

    def _detect_encoding(self, content: str) -> str:
        return "utf-8"

    def _detect_delimiter(self, sample: str) -> str:
        if self.cfg.get("delimiter"):
            return self.cfg["delimiter"]
        # Sniff delimiter from first few lines
        try:
            dialect = csv.Sniffer().sniff(sample[:2000], delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            # Count occurrences
            counts = {",": sample.count(","), ";": sample.count(";"),
                      "\t": sample.count("\t"), "|": sample.count("|")}
            return max(counts, key=counts.get)

    def _parse_content(self, content: str) -> List[Dict[str, Any]]:
        skip_rows = self.cfg.get("skip_rows", 0)
        has_header = self.cfg.get("has_header", True)

        delimiter = self._detect_delimiter(content)
        lines = content.splitlines()
        if skip_rows:
            lines = lines[skip_rows:]
        content = "\n".join(lines)

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

        records = []
        for row in reader:
            # Clean up None keys that csv.DictReader sometimes produces
            clean = {k.strip(): v.strip() if isinstance(v, str) else v
                     for k, v in row.items() if k is not None}
            records.append(clean)

        return records

    async def connect(self) -> bool:
        content = self._get_file_content()
        self._data = self._parse_content(content)

        if self.cfg.get("file_path"):
            try:
                self._file_mtime = os.path.getmtime(self.cfg["file_path"])
            except Exception:
                pass
        return True

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            start = time.time()
            if not self._data:
                await self.connect()
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"File loaded: {len(self._data)} rows detected",
                "latency_ms": latency_ms,
                "row_count": len(self._data)
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._data:
            await self.connect()
        return self._data[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._data:
            await self.connect()

        # If file watch enabled, re-read if file changed
        if self.cfg.get("watch_for_changes") and self.cfg.get("file_path"):
            try:
                current_mtime = os.path.getmtime(self.cfg["file_path"])
                if current_mtime != self._file_mtime:
                    await self.connect()
            except Exception:
                pass

        return self._data


class ExcelConnector(BaseConnector):
    """
    Excel (.xlsx, .xls) connector.
    Config keys:
        file_path: str          - path to Excel file, OR
        file_content: str       - base64 encoded Excel file
        sheet_name: str         - sheet to read, default first sheet
        sheet_index: int        - alternative to sheet_name
        header_row: int         - row index of header, default 0
        skip_rows: int          - rows to skip after header
        use_all_sheets: bool    - merge all sheets, default False
    """

    CONNECTOR_TYPE = ConnectorType.EXCEL_FILE
    DISPLAY_NAME = "Excel File"
    DESCRIPTION = "Upload an Excel spreadsheet (.xlsx or .xls)"
    ICON = "📊"
    DOCS = (
        "1. Provide a file_path or file_url pointing to the .xlsx/.xls file.\n"
        "2. If the workbook has multiple sheets, specify sheet_name — otherwise the first "
        "sheet is used.\n"
        "3. First row is treated as column headers by default."
    )

    CONFIG_SCHEMA = {
        "required_one_of": ["file_path", "file_content"],
        "optional": ["sheet_name", "sheet_index", "header_row", "skip_rows", "use_all_sheets"],
        "defaults": {"header_row": 0, "skip_rows": 0, "use_all_sheets": False}
    }

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.cfg = config.config
        self._data: Optional[List[Dict[str, Any]]] = None
        self._sheet_names: List[str] = []

    def _get_file_bytes(self) -> bytes:
        if self.cfg.get("file_content"):
            import base64
            content = self.cfg["file_content"]
            try:
                return base64.b64decode(content)
            except Exception:
                return content.encode()

        file_path = self.cfg.get("file_path")
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                return f.read()

        raise ValueError("No file_path or file_content provided")

    def _parse_excel(self, file_bytes: bytes) -> List[Dict[str, Any]]:
        try:
            import openpyxl
        except ImportError:
            raise RuntimeError("openpyxl not installed. Run: pip install openpyxl")

        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        self._sheet_names = wb.sheetnames

        if self.cfg.get("use_all_sheets", False):
            all_records = []
            for sheet_name in wb.sheetnames:
                records = self._parse_sheet(wb[sheet_name])
                for r in records:
                    r["_sheet_name"] = sheet_name
                all_records.extend(records)
            return all_records

        # Single sheet
        if self.cfg.get("sheet_name"):
            ws = wb[self.cfg["sheet_name"]]
        elif self.cfg.get("sheet_index") is not None:
            ws = wb[wb.sheetnames[self.cfg["sheet_index"]]]
        else:
            ws = wb.active

        return self._parse_sheet(ws)

    def _parse_sheet(self, ws) -> List[Dict[str, Any]]:
        header_row_idx = self.cfg.get("header_row", 0)
        skip_rows = self.cfg.get("skip_rows", 0)

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        headers = [str(h).strip() if h is not None else f"col_{i}"
                   for i, h in enumerate(rows[header_row_idx])]

        data_rows = rows[header_row_idx + 1 + skip_rows:]
        records = []
        for row in data_rows:
            if all(v is None for v in row):
                continue  # skip empty rows
            record = {}
            for i, value in enumerate(row):
                header = headers[i] if i < len(headers) else f"col_{i}"
                record[header] = value
            records.append(record)

        return records

    async def connect(self) -> bool:
        file_bytes = self._get_file_bytes()
        self._data = self._parse_excel(file_bytes)
        return True

    async def test_connection(self) -> Dict[str, Any]:
        import time
        try:
            start = time.time()
            if not self._data:
                await self.connect()
            latency_ms = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"Excel file loaded: {len(self._data)} rows, sheets: {self._sheet_names}",
                "latency_ms": latency_ms,
                "row_count": len(self._data),
                "sheets": self._sheet_names
            }
        except Exception as e:
            return {"success": False, "message": str(e), "latency_ms": -1}

    async def fetch_sample(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self._data:
            await self.connect()
        return self._data[:limit]

    async def fetch_all(self, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self._data:
            await self.connect()
        return self._data
