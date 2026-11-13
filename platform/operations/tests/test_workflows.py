import io
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient
from operations.models import (
    Asset,
    AssetAssignment,
    Attachment,
    AuditEvent,
    Company,
    Employee,
    IdempotencyRecord,
    Organization,
    RoleGrant,
    SupportTeam,
    Ticket,
    UploadDraft,
)
from operations.services import Conflict, assign_asset, create_ticket, transition_ticket


def fixture():
    User = get_user_model()
    company = Company.objects.create(code="BJC", name="BJC")
    other = Company.objects.create(code="BJE", name="BJE")
    org = Organization.objects.create(company=company, name="IT")
    other_org = Organization.objects.create(company=other, name="Sales")
    agent = User.objects.create_user("agent", password="a-strong-test-password!")
    user = User.objects.create_user("employee", password="a-strong-test-password!")
    other_user = User.objects.create_user("other", password="a-strong-test-password!")
    employee = Employee.objects.create(user=user, company=company, organization=org, name="김직원")
    other_employee = Employee.objects.create(
        user=other_user, company=other, organization=other_org, name="이직원"
    )
    team = SupportTeam.objects.create(name="IT 지원팀")
    team.companies.add(company)
    team.members.add(agent)
    other_team = SupportTeam.objects.create(name="다른 지원팀")
    other_team.companies.add(other)
    for role in ["support_agent", "asset_manager", "organization_manager"]:
        RoleGrant.objects.create(user=agent, company=company, role=role)
    asset = Asset.objects.create(
        company=company, organization=org, number="BJC-N-1", model="노트북", status="IN_USE"
    )
    AssetAssignment.objects.create(asset=asset, employee=employee, organization=org)
    spare = Asset.objects.create(
        company=company, organization=org, number="BJC-N-2", model="예비 노트북"
    )
    return locals()


def payload(**kwargs):
    return {
        "title": "오류 확인 요청",
        "body": "오류 화면을 확인해주세요.",
        "type": "INCIDENT",
        "category": "DEVICE",
        **kwargs,
    }


def png():
    output = io.BytesIO()
    Image.new("RGB", (3, 3), "red").save(output, format="PNG")
    return SimpleUploadedFile("screen.png", output.getvalue(), content_type="image/png")


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for k, v in fixture().items():
            if k != "User":
                setattr(cls, k, v)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.client = APIClient()
        self.client.force_login(self.user, backend="operations.auth.LocalBackend")

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def create(self, **extra):
        return self.client.post(
            "/api/v1/portal/tickets",
            payload(**extra),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

    def manager(self):
        self.client.force_login(self.agent, backend="operations.auth.LocalBackend")

    def test_ticket_persists_and_is_idempotent(self):
        key = str(uuid.uuid4())
        url = "/api/v1/portal/tickets"
        one = self.client.post(url, payload(), format="json", HTTP_IDEMPOTENCY_KEY=key)
        two = self.client.post(url, payload(), format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(one.status_code, 201, one.data)
        self.assertEqual(two.status_code, 200, two.data)
        self.assertEqual(one.data["data"]["id"], two.data["data"]["id"])
        self.assertEqual(Ticket.objects.count(), 1)
        three = self.client.post(
            url, payload(title="다른 내용"), format="json", HTTP_IDEMPOTENCY_KEY=key
        )
        self.assertEqual(three.status_code, 409)

    def test_employee_cannot_set_requester_company_or_internal_fields(self):
        for extra in [
            {"requester_id": str(self.other_employee.pk)},
            {"company_id": str(self.other.pk)},
            {"internal": True},
        ]:
            result = self.create(**extra)
            self.assertEqual(result.status_code, 400, result.data)

    def test_employee_cannot_link_other_employee_asset(self):
        result = self.create(asset_ids=[str(self.spare.pk)])
        self.assertEqual(result.status_code, 400, result.data)

    def test_own_asset_link_and_agent_related_asset_access(self):
        result = self.create(asset_ids=[str(self.asset.pk)])
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["data"]["assets"][0]["id"], str(self.asset.pk))

    def test_cross_employee_ticket_and_attachment_are_hidden(self):
        draft = self.client.post("/api/v1/uploads/drafts", {}, format="json").data["data"]["id"]
        file = self.client.post(
            f"/api/v1/uploads/drafts/{draft}/files", {"file": png()}, format="multipart"
        )
        self.assertEqual(file.status_code, 201, file.data)
        created = self.create(draft_id=draft, attachment_ids=[file.data["data"]["id"]])
        self.assertEqual(created.status_code, 201, created.data)
        ticket_id = created.data["data"]["id"]
        url = file.data["data"]["url"]
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other_user, backend="operations.auth.LocalBackend")
        self.assertEqual(self.client.get(f"/api/v1/portal/tickets/{ticket_id}").status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.manager()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))

    def test_internal_note_absent_from_employee_json(self):
        ticket = self.create().data["data"]
        self.manager()
        result = self.client.post(
            f"/api/v1/admin/tickets/{ticket['id']}/messages",
            {"body": "내부 전용 내용", "internal": True},
            format="json",
        )
        self.assertEqual(result.status_code, 201, result.data)
        self.client.force_login(self.user, backend="operations.auth.LocalBackend")
        result = self.client.get(f"/api/v1/portal/tickets/{ticket['id']}")
        self.assertNotIn("내부 전용 내용", str(result.data))
        self.assertNotIn("events", result.data["data"])
        result = self.client.post(
            f"/api/v1/portal/tickets/{ticket['id']}/messages",
            {"body": "x", "internal": True},
            format="json",
        )
        self.assertEqual(result.status_code, 400)

    def test_claim_resolve_and_dashboard_share_queue_rules(self):
        ticket = self.create().data["data"]
        self.manager()

        def counts():
            return self.client.get("/api/v1/admin/dashboard").data["data"]["counts"]

        self.assertEqual(counts()["unassigned"], 1)
        result = self.client.post(
            f"/api/v1/admin/tickets/{ticket['id']}/claim",
            {"expected_version": ticket["version"]},
            format="json",
        )
        self.assertEqual(result.status_code, 200, result.data)
        current = result.data["data"]
        self.assertEqual(current["status"], "IN_PROGRESS")
        self.assertEqual(counts()["unassigned"], 0)
        old = self.client.post(
            f"/api/v1/admin/tickets/{ticket['id']}/transition",
            {"expected_version": 1, "status": "RESOLVED", "reason": "해결"},
            format="json",
        )
        self.assertEqual(old.status_code, 409)
        result = self.client.post(
            f"/api/v1/admin/tickets/{ticket['id']}/transition",
            {"expected_version": current["version"], "status": "RESOLVED", "reason": "재설치 완료"},
            format="json",
        )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(counts()["open"], 0)
        result = self.client.get("/api/v1/admin/tickets?queue=open")
        self.assertEqual(result.data["meta"]["total"], 0)
        self.client.force_login(self.user, backend="operations.auth.LocalBackend")
        current = Ticket.objects.get(id=ticket["id"])
        result = self.client.post(
            f"/api/v1/portal/tickets/{ticket['id']}/transition",
            {"expected_version": current.version, "status": "CLOSED", "reason": "확인 완료"},
            format="json",
        )
        self.assertEqual(result.status_code, 200, result.data)

    def test_overdue_excludes_resolved_and_other_company(self):
        ticket = self.create().data["data"]
        Ticket.objects.filter(id=ticket["id"]).update(due_at=timezone.now() - timedelta(days=1))
        self.manager()
        d = self.client.get("/api/v1/admin/dashboard").data["data"]
        self.assertEqual(d["counts"]["overdue"], 1)
        self.assertEqual(
            self.client.get("/api/v1/admin/tickets?queue=overdue").data["meta"]["total"], 1
        )
        Ticket.objects.filter(id=ticket["id"]).update(status="RESOLVED")
        self.assertEqual(
            self.client.get("/api/v1/admin/dashboard").data["data"]["counts"]["overdue"], 0
        )

    def test_role_and_scope_cannot_be_combined_across_grants(self):
        RoleGrant.objects.create(user=self.agent, company=self.other, role="system_admin")
        self.client.force_login(self.other_user, backend="operations.auth.LocalBackend")
        created = self.create()
        self.assertEqual(created.status_code, 201, created.data)
        self.other_team.members.add(self.agent)
        self.manager()
        self.assertEqual(self.client.get("/api/v1/admin/tickets").data["meta"]["total"], 0)

    def test_staging_ownership_expiry_and_reuse(self):
        draft = self.client.post("/api/v1/uploads/drafts", {}, format="json").data["data"]["id"]
        file = self.client.post(
            f"/api/v1/uploads/drafts/{draft}/files", {"file": png()}, format="multipart"
        ).data["data"]["id"]
        self.client.force_login(self.other_user, backend="operations.auth.LocalBackend")
        result = self.create(draft_id=draft, attachment_ids=[file])
        self.assertEqual(result.status_code, 409)
        self.client.force_login(self.user, backend="operations.auth.LocalBackend")
        created = self.create(draft_id=draft, attachment_ids=[file])
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.create(draft_id=draft, attachment_ids=[file]).status_code, 409)
        self.assertEqual(
            self.client.delete(f"/api/v1/uploads/drafts/{draft}/files/{file}").status_code, 404
        )

    def test_invalid_image_and_sixth_file_are_rejected(self):
        draft = self.client.post("/api/v1/uploads/drafts", {}, format="json").data["data"]["id"]
        url = f"/api/v1/uploads/drafts/{draft}/files"
        bad = self.client.post(
            url,
            {"file": SimpleUploadedFile("x.png", b"not image", content_type="image/png")},
            format="multipart",
        )
        self.assertEqual(bad.status_code, 400)
        for i in range(5):
            self.assertEqual(
                self.client.post(url, {"file": png()}, format="multipart").status_code, 201
            )
        self.assertEqual(
            self.client.post(url, {"file": png()}, format="multipart").status_code, 400
        )
        self.assertEqual(Attachment.objects.count(), 5)

    def test_expired_draft_cannot_upload_or_download(self):
        draft = self.client.post("/api/v1/uploads/drafts", {}, format="json").data["data"]["id"]
        url = f"/api/v1/uploads/drafts/{draft}/files"
        file = self.client.post(url, {"file": png()}, format="multipart").data["data"]
        UploadDraft.objects.filter(pk=draft).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.client.get(file["url"]).status_code, 404)
        self.assertEqual(
            self.client.post(url, {"file": png()}, format="multipart").status_code, 404
        )

    def test_asset_assignment_checks_version_company_and_retirement(self):
        self.manager()
        url = f"/api/v1/admin/assets/{self.spare.id}/assign"
        for person in [self.other_employee]:
            result = self.client.post(
                url, {"employee_id": str(person.id), "expected_version": 1}, format="json"
            )
            self.assertEqual(result.status_code, 400)
        result = self.client.post(
            url, {"employee_id": str(self.employee.id), "expected_version": 1}, format="json"
        )
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client.post(
            url, {"employee_id": str(self.employee.id), "expected_version": 1}, format="json"
        )
        self.assertEqual(result.status_code, 409)
        self.assertEqual(
            AssetAssignment.objects.filter(asset=self.spare, returned_at__isnull=True).count(), 1
        )

    def test_employee_routes_and_api_require_real_permissions(self):
        self.assertEqual(self.client.get("/admin").status_code, 403)
        self.assertEqual(self.client.get("/portal").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/tickets").data["meta"]["total"], 0)
        Employee.objects.filter(pk=self.employee.pk).update(active=False)
        self.assertEqual(self.client.get("/api/v1/portal/tickets").status_code, 401)

    def test_public_qr_excludes_private_fields(self):
        response = self.client.get(f"/a/{self.asset.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.employee.name)
        self.assertNotContains(response, "일련번호")

    def test_csrf_is_required_for_authenticated_mutations(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user, backend="operations.auth.LocalBackend")
        result = client.post(
            "/api/v1/portal/tickets",
            payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(result.status_code, 403)

    def test_unauthenticated_api_is_401(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/v1/portal/tickets").status_code, 401)

    def test_priority_and_due_date_updates_are_versioned(self):
        ticket = self.create().data["data"]
        self.manager()
        result = self.client.patch(
            f"/api/v1/admin/tickets/{ticket['id']}",
            {
                "expected_version": 1,
                "priority": "HIGH",
                "due_at": (timezone.now() - timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data["data"]["overdue"])
        self.assertEqual(
            self.client.get("/api/v1/admin/dashboard").data["data"]["counts"]["overdue"], 1
        )
        self.client.force_login(self.user, backend="operations.auth.LocalBackend")
        self.assertEqual(
            self.client.patch(
                f"/api/v1/portal/tickets/{ticket['id']}",
                {"expected_version": 2, "priority": "LOW"},
                format="json",
            ).status_code,
            403,
        )

    def test_asset_registration_is_scoped_and_auto_numbered(self):
        self.manager()
        url = "/api/v1/admin/assets"
        result = self.client.post(
            url, {"organization_id": str(self.org.id), "model": "신규 장비"}, format="json"
        )
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["data"]["number"], "BJC-N-003")
        denied = self.client.post(
            url, {"organization_id": str(self.other_org.id), "model": "다른 법인"}, format="json"
        )
        self.assertEqual(denied.status_code, 404)
        self.client.force_login(self.user, backend="operations.auth.LocalBackend")
        self.assertEqual(
            self.client.post(
                "/api/v1/portal/assets",
                {"organization_id": str(self.org.id), "model": "x"},
                format="json",
            ).status_code,
            403,
        )

    def test_requester_options_require_team_membership(self):
        self.team.members.remove(self.agent)
        self.manager()
        result = self.client.get("/api/v1/admin/employees?purpose=request")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["data"], [])
        result = self.client.get(f"/api/v1/admin/employees/{self.employee.id}/assets")
        self.assertEqual(result.status_code, 404)


class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row locks required")
        for k, v in fixture().items():
            if k != "User":
                setattr(self, k, v)

    def test_two_assignments_cannot_overwrite_each_other(self):
        def assign(_):
            close_old_connections()
            try:
                assign_asset(self.agent, self.spare.id, self.employee.id, 1)
                return "ok"
            except Conflict:
                return "conflict"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, [1, 2]))
        self.assertCountEqual(results, ["ok", "conflict"])
        self.assertEqual(
            AssetAssignment.objects.filter(asset=self.spare, returned_at__isnull=True).count(), 1
        )

    def test_concurrent_duplicate_creates_return_same_ticket(self):
        key = uuid.uuid4()

        def create(_):
            close_old_connections()
            try:
                return str(create_ticket(self.user, payload(), key, portal=True)[0].id)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(create, [1, 2]))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Ticket.objects.count(), 1)
