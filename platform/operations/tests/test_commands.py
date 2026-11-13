import hashlib
import io
import json
import tempfile
import uuid
from pathlib import Path
from django.core.management import call_command
from django.test import TestCase
import openpyxl
from operations.models import Asset


class LegacyPreviewTests(TestCase):
    def test_preview_does_not_change_workbook_or_database(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "copy.xlsx"
            book = openpyxl.Workbook()
            sheet = book.active
            sheet.title = "5.BJC PC 현황"
            sheet["B6"] = "BJC-N-001"
            sheet["AZ6"] = str(uuid.uuid4())
            sheet["D6"] = "동명이인"
            sheet["Z6"] = "사용"
            book.save(file)
            before = hashlib.sha256(file.read_bytes()).hexdigest()
            out = io.StringIO()
            call_command("preview_legacy", str(file), stdout=out)
            report = json.loads(out.getvalue())
            self.assertEqual(report["counts"]["BJC"], 1)
            self.assertTrue(
                any(e["code"] == "IDENTITY_MAPPING_REVIEW_REQUIRED" for e in report["issues"])
            )
            self.assertEqual(before, hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertFalse(Asset.objects.exists())
