"""Read-only inventory check; deliberately no apply/auto identity matching."""

import hashlib
import json
import uuid
from pathlib import Path
import openpyxl
from django.core.management.base import BaseCommand, CommandError

SHEETS = {
    "BJM": ("3.BJM PC 현황", "W", "X"),
    "BJE": ("4.BJE PC 현황", "Y", "Z"),
    "BJC": ("5.BJC PC 현황", "Y", "Z"),
    "BJM:모니터": ("3_1.BJM 모니터 현황", "I", "K"),
    "BJE:모니터": ("4_1.BJE 모니터 현황", "I", "K"),
    "BJC:모니터": ("5_1.BJC 모니터 현황", "I", "K"),
}
STATES = {"사용", "보관", "수리", "폐기", "분실"}


class Command(BaseCommand):
    help = "Validate a copy of the current XLSX without changing the file or database; no --apply option."

    def add_arguments(self, parser):
        parser.add_argument("file")

    def handle(self, *args, **options):
        source = Path(options["file"])
        if not source.is_file() or source.suffix.lower() != ".xlsx":
            raise CommandError("XLSX 사본 경로를 지정하세요.")
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        book = openpyxl.load_workbook(source, read_only=True, data_only=False)
        report = {
            "source_checksum": checksum,
            "counts": {},
            "missing_sheets": [],
            "issues": [],
            "preserved_extra_sheets": [],
            "mode": "read-only; no DB or XLSX writes",
        }
        seen_ids = set()
        seen_numbers = set()
        try:
            for key, (sheet, date, state) in SHEETS.items():
                if sheet not in book.sheetnames:
                    report["missing_sheets"].append(sheet)
                    continue
                count = 0
                for row in book[sheet].iter_rows(min_row=6, max_col=53):
                    number = str(row[1].value or "").strip()
                    if not number:
                        continue
                    count += 1
                    aid = str(row[51].value or "").strip()
                    r = row[1].row

                    def issue(code):
                        report["issues"].append({"sheet": sheet, "row": r, "code": code})

                    try:
                        identity = str(uuid.UUID(aid))
                    except ValueError:
                        identity = None
                        issue("MISSING_OR_INVALID_UUID")
                    if identity:
                        if identity in seen_ids:
                            issue("DUPLICATE_UUID")
                        seen_ids.add(identity)
                    number_key = (key.split(":")[0], number)
                    if number_key in seen_numbers:
                        issue("DUPLICATE_NUMBER")
                    seen_numbers.add(number_key)
                    value = row[openpyxl.utils.column_index_from_string(state) - 1].value
                    if str(value or "").strip() not in STATES:
                        issue("UNKNOWN_STATUS")
                    if row[3].value or row[52].value:
                        issue("IDENTITY_MAPPING_REVIEW_REQUIRED")
                report["counts"][key] = count
            report["preserved_extra_sheets"] = [
                n for n in book.sheetnames if n not in {s[0] for s in SHEETS.values()}
            ]
        finally:
            book.close()
        if checksum != hashlib.sha256(source.read_bytes()).hexdigest():
            raise CommandError("검사 중 원본이 외부에서 변경되었습니다.")
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
