"""Import the legacy Excel ITAM ledger into the integrated database.

Default mode is a read-only preview. Use --apply only with a backup copy of the
source workbook and a development/staging database first.
"""
import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from operations.models import (
    Asset,
    AssetAssignment,
    Company,
    Employee,
    MaintenanceContract,
    Organization,
)

SHEETS = {
    "BJM": ("3.BJM PC 현황", "W", "X", "U", "V"),
    "BJE": ("4.BJE PC 현황", "Y", "Z", "V", "X"),
    "BJC": ("5.BJC PC 현황", "Y", "Z", "V", "X"),
    "BJM:모니터": ("3_1.BJM 모니터 현황", "I", "K", "H", "J"),
    "BJE:모니터": ("4_1.BJE 모니터 현황", "I", "K", "H", "J"),
    "BJC:모니터": ("5_1.BJC 모니터 현황", "I", "K", "H", "J"),
}
STATUS = {"사용": "IN_USE", "보관": "IN_STOCK", "수리": "REPAIR", "분실": "LOST", "폐기": "DISPOSED"}
MAINT_FIELDS = {
    "vendor": "B", "service": "C", "quantity": "D", "division": "E",
    "period": "F", "cost2024": "G", "cost2025": "H", "cost2026": "I",
    "contact": "J", "note": "L", "end": "M", "start": "N",
}


def clean(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    return str(value).strip()


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = clean(value)
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y.%m.%d"):
        try:
            parsed = datetime.strptime(value, pattern)
            return parsed.date() if pattern != "%Y-%m" else date(parsed.year, parsed.month, 1)
        except ValueError:
            pass
    return None


def resolve_merged(sheet, row, col):
    cell = sheet[f"{col}{row}"]
    for merged in sheet.merged_cells.ranges:
        if merged.min_row <= row <= merged.max_row and merged.min_col <= cell.column <= merged.max_col:
            return sheet.cell(merged.min_row, merged.min_col).value
    return cell.value


class Command(BaseCommand):
    help = "Preview or explicitly apply legacy XLSX assets, software, assignments, and maintenance contracts."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--apply", action="store_true", help="Write validated rows to the current database.")

    def handle(self, *args, **options):
        source = Path(options["file"])
        if not source.is_file() or source.suffix.lower() != ".xlsx":
            raise CommandError("XLSX 원장 사본 경로를 지정하세요.")
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        book = openpyxl.load_workbook(source, read_only=False, data_only=True)
        try:
            assets, issues = self.read_assets(book)
            contracts = self.read_contracts(book, issues)
        finally:
            book.close()
        report = {
            "source": source.name,
            "source_checksum": checksum,
            "mode": "apply" if options["apply"] else "preview only",
            "assets_found": len(assets),
            "maintenance_found": len(contracts),
            "issues": issues,
        }
        if not options["apply"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return
        if checksum != hashlib.sha256(source.read_bytes()).hexdigest():
            raise CommandError("적용 직전에 원장 파일이 변경되었습니다. 다시 검사하세요.")
        result = self.apply_records(assets, contracts)
        report.update(result)
        report["source_checksum_after"] = hashlib.sha256(source.read_bytes()).hexdigest()
        if report["source_checksum_after"] != checksum:
            raise CommandError("원장 파일이 변경되었습니다. 가져온 DB를 검토하세요.")
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))

    def read_assets(self, book):
        records, issues, seen = [], [], set()
        companies = {c.code: c for c in Company.objects.filter(active=True)}
        organizations = list(Organization.objects.select_related("company"))
        for division, (sheet_name, date_col, status_col, grade_col, note_col) in SHEETS.items():
            if sheet_name not in book.sheetnames:
                issues.append({"sheet": sheet_name, "code": "MISSING_SHEET"})
                continue
            sheet = book[sheet_name]
            company_code = division.split(":")[0]
            company = companies.get(company_code)
            if not company:
                issues.append({"sheet": sheet_name, "code": "COMPANY_NOT_CONFIGURED", "company": company_code})
                continue
            monitor = ":모니터" in division
            for row in range(6, sheet.max_row + 1):
                number = clean(sheet[f"B{row}"].value)
                if not number:
                    continue
                key = (company_code, number)
                if key in seen:
                    issues.append({"sheet": sheet_name, "row": row, "code": "DUPLICATE_COMPANY_NUMBER_SKIPPED"})
                    continue
                seen.add(key)
                department = clean(sheet[f"C{row}"].value)
                matches = [o for o in organizations if o.company_id == company.id and o.name == department]
                if len(matches) != 1:
                    issues.append({"sheet": sheet_name, "row": row, "code": "ORGANIZATION_MAPPING_REQUIRED", "department": department})
                    continue
                def cell(column):
                    return clean(sheet[f"{column}{row}"].value)
                if monitor:
                    info = {"size": cell("E"), "manufacture_date": cell(date_col), "note": cell(note_col)}
                    maker, model, serial, device_type = "", cell("F"), cell("G"), "MONITOR"
                    software = {}
                else:
                    software = {
                        "office": cell("Q"), "hangul": cell("R"),
                    }
                    extra_cols = {
                        "BJM": {"officekeeper": "S", "antivirus": "T"},
                        "BJE": {"sap": "S", "antivirus": "T", "docuone": "U", "other": "W"},
                        "BJC": {"sap": "S", "antivirus": "T", "docuone": "U", "other": "W"},
                    }.get(company_code, {})
                    software.update({name: cell(col) for name, col in extra_cols.items()})
                    software = {k: v for k, v in software.items() if v}
                    info = {
                        "device_type": "DESKTOP" if "desk" in cell("E").lower() or "데스크" in cell("E") else "NOTEBOOK",
                        "cpu": cell("I"), "bits": cell("J"), "ram": cell("K"),
                        "ssd": cell("L"), "hdd": cell("M"), "gpu": cell("N"),
                        "os": cell("O"), "os_edition": cell("P"),
                        "purchase_date": cell(date_col), "grade": cell(grade_col), "note": cell(note_col),
                    }
                    maker, model, serial = cell("F"), cell("G"), cell("H")
                    device_type = info["device_type"]
                raw_state = cell(status_col)
                status = STATUS.get(raw_state)
                if not status:
                    issues.append({"sheet": sheet_name, "row": row, "code": "UNKNOWN_STATUS_SKIPPED"})
                    continue
                legacy_id = cell("AZ")
                try:
                    asset_id = uuid.UUID(legacy_id) if legacy_id else uuid.uuid5(uuid.NAMESPACE_URL, f"bumjin-itam:{company_code}:{division}:{row}")
                except ValueError:
                    asset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bumjin-itam:{company_code}:{division}:{row}")
                    issues.append({"sheet": sheet_name, "row": row, "code": "INVALID_LEGACY_UUID_REPLACED"})
                legacy_data = {
                    **{k: v for k, v in info.items() if v},
                    "software": software,
                    "migration_key": f"{division}:{row}",
                    "legacy_sheet": sheet_name,
                    "legacy_row": row,
                    "legacy_user": cell("D"),
                    "legacy_uid": cell("BA"),
                }
                records.append({
                    "id": asset_id, "company": company, "organization": matches[0],
                    "number": number, "category": "모니터" if monitor else "PC",
                    "maker": maker, "model": model or number, "serial": serial,
                    "status": status, "legacy_data": legacy_data,
                })
        return records, issues

    def read_contracts(self, book, issues):
        records = []
        names = ["2.전산 유지보수"]
        if "2.전산 유지보수" in book.sheetnames:
            sheet = book["2.전산 유지보수"]
            for row in range(3, 18):
                values = {key: clean(resolve_merged(sheet, row, col)) for key, col in MAINT_FIELDS.items()}
                if values["service"]:
                    self.add_contract_record(records, issues, values, sheet_name, row)
        if "앱_유지보수" in book.sheetnames:
            sheet = book["앱_유지보수"]
            for row in range(2, sheet.max_row + 1):
                values = {key: clean(sheet[f"{col}{row}"].value) for key, col in MAINT_FIELDS.items()}
                if values["service"]:
                    self.add_contract_record(records, issues, values, "앱_유지보수", row)
        return records

    def add_contract_record(self, records, issues, values, sheet_name, row):
        if not values.get("vendor"):
            issues.append({"sheet": sheet_name, "row": row, "code": "MAINTENANCE_VENDOR_MISSING"})
            return
        raw_quantity = values.get("quantity") or "1"
        try:
            quantity = int(float(raw_quantity.replace(",", "")))
        except (TypeError, ValueError):
            issues.append({"sheet": sheet_name, "row": row, "code": "MAINTENANCE_QUANTITY_INVALID"})
            return
        if not 1 <= quantity <= 100000:
            issues.append({"sheet": sheet_name, "row": row, "code": "MAINTENANCE_QUANTITY_OUT_OF_RANGE"})
            return
        values["_quantity"] = quantity
        records.append(values)

    @transaction.atomic
    def apply_records(self, assets, contracts):
        created_assets = updated_assets = assignments = skipped_assets = 0
        companies = {c.code: c for c in Company.objects.filter(active=True)}
        for data in assets:
            existing = Asset.objects.filter(pk=data["id"]).first()
            number_owner = Asset.objects.filter(company=data["company"], number=data["number"]).exclude(pk=data["id"]).first()
            if number_owner:
                skipped_assets += 1
                continue
            defaults = {k: v for k, v in data.items() if k != "id"}
            if existing:
                for key, value in defaults.items():
                    setattr(existing, key, value)
                existing.save()
                asset = existing
                updated_assets += 1
            else:
                asset = Asset.objects.create(id=data["id"], **defaults)
                created_assets += 1
            uid = data["legacy_data"].get("legacy_uid")
            if uid:
                people = Employee.objects.filter(company=data["company"], directory_uid=uid, active=True)
                if people.count() == 1:
                    employee = people.first()
                    active = asset.assignments.filter(returned_at__isnull=True).first()
                    if not active or active.employee_id != employee.id:
                        if active:
                            active.returned_at = timezone.now()
                            active.save(update_fields=["returned_at"])
                        AssetAssignment.objects.create(asset=asset, employee=employee, organization=employee.organization)
                        assignments += 1
        created_contracts = updated_contracts = skipped_contracts = 0
        for values in contracts:
            division = values.get("division", "").strip().upper()
            company = companies.get(division)
            if not company:
                skipped_contracts += 1
                continue
            starts_on, ends_on = as_date(values.get("start")), as_date(values.get("end"))
            costs = {year: values.get(f"cost{year}", "") for year in ("2024", "2025", "2026")}
            costs = {year: amount.replace(",", "") for year, amount in costs.items() if amount}
            identity = MaintenanceContract.objects.filter(
                company=company, vendor=values["vendor"], service=values["service"],
                starts_on=starts_on, ends_on=ends_on,
            ).first()
            defaults = {
                "quantity": values.get("_quantity", 1),
                "period": values.get("period", ""),
                "starts_on": starts_on,
                "ends_on": ends_on,
                "costs": costs,
                "contact": values.get("contact", ""),
                "note": values.get("note", ""),
                "active": not ends_on or ends_on >= timezone.localdate(),
            }
            if identity:
                for key, value in defaults.items():
                    setattr(identity, key, value)
                identity.save()
                updated_contracts += 1
            else:
                MaintenanceContract.objects.create(
                    company=company, vendor=values["vendor"][:160],
                    service=values["service"][:200], **defaults,
                )
                created_contracts += 1
        return {
            "assets_created": created_assets, "assets_updated": updated_assets,
            "assets_skipped_number_conflict": skipped_assets, "assignments_created": assignments,
            "maintenance_created": created_contracts, "maintenance_updated": updated_contracts,
            "maintenance_skipped_company": skipped_contracts,
        }
