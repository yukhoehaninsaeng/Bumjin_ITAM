from datetime import timedelta
from pathlib import Path
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from . import policies, serializers, services
from .models import (
    Asset,
    Attachment,
    AuditEvent,
    Employee,
    MaintenanceContract,
    Organization,
    Ticket,
    TicketMessage,
    UploadDraft,
)
from .uploads import MAX_FILES, validate_upload
from .transactions import read_snapshot


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        details = response.data
        response.data = {
            "error": {
                "code": getattr(exc, "default_code", "error"),
                "message": (
                    str(details.get("detail", "입력 내용을 확인하세요."))
                    if isinstance(details, dict)
                    else str(details)
                ),
                "fields": details if isinstance(details, dict) and "detail" not in details else {},
            }
        }
    return response


def parse(schema, data):
    result = schema(data=data)
    result.is_valid(raise_exception=True)
    return result.validated_data


def file_json(file):
    return {
        "id": str(file.id),
        "name": file.name,
        "size": file.size,
        "media_type": file.media_type,
        "is_image": file.is_image,
        "url": f"/api/v1/files/{file.id}/download",
    }


def ticket_json(ticket, *, detail=False, portal=False, as_of=None):
    result = {
        "id": str(ticket.id),
        "number": ticket.number,
        "title": ticket.title,
        "status": ticket.status,
        "status_label": ticket.get_status_display(),
        "priority": ticket.priority,
        "priority_label": ticket.get_priority_display(),
        "requester": ticket.requester_snapshot,
        "team": ticket.team.name,
        "assignee": services.actor_name(ticket.assignee) if ticket.assignee_id else "미배정",
        "created_at": ticket.created_at.isoformat(),
        "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
        "overdue": ticket.status in policies.OPEN_STATES
        and ticket.due_at is not None
        and ticket.due_at < (as_of or timezone.now()),
        "version": ticket.version,
    }
    if detail:
        messages = ticket.messages.select_related("author")
        if portal:
            messages = messages.filter(internal=False)
        result.update(
            body=ticket.body,
            type=ticket.type,
            category=ticket.category,
            messages=[
                {
                    "id": str(m.id),
                    "body": m.body,
                    "author": services.actor_name(m.author),
                    "created_at": m.created_at.isoformat(),
                    **({} if portal else {"internal": m.internal}),
                }
                for m in messages
            ],
            attachments=[file_json(f) for f in ticket.attachments.all()],
        )
        assets = (
            policies.assets_visible(ticket.requester.user, portal=True)
            if portal and ticket.requester.user_id
            else ticket.assets.all()
        )
        result["assets"] = [
            {"id": str(a.id), "number": a.number, "model": a.model}
            for a in ticket.assets.filter(pk__in=assets.values("pk"))
        ]
        if not portal:
            result["events"] = [
                {"action": e.action, "created_at": e.created_at.isoformat(), "data": e.data}
                for e in ticket.events.all()
            ]
    return result


def asset_grade(asset):
    from datetime import date
    meta = asset.legacy_data or {}
    raw = meta.get("purchase_date") or meta.get("manufacture_date") or ""
    try:
        acquired = date.fromisoformat(str(raw)[:10])
        years = (date.today() - acquired).days / 365.25
        return "C" if years >= 5 else "B" if years >= 3 else "A"
    except (ValueError, TypeError):
        return meta.get("grade") or "미확인"


def asset_json(asset):
    assignment = (
        asset.assignments.filter(returned_at__isnull=True).select_related("employee").first()
    )
    return {
        "id": str(asset.id),
        "number": asset.number,
        "category": asset.category,
        "grade": asset_grade(asset),
        "specification": " · ".join(filter(None, [(asset.legacy_data or {}).get("ram"), (asset.legacy_data or {}).get("ssd")])) if asset.category == "PC" else (asset.legacy_data or {}).get("size", ""),
        "maker": asset.maker,
        "model": asset.model,
        "serial": asset.serial,
        "status": asset.status,
        "status_label": asset.get_status_display(),
        "company": asset.company.code,
        "organization": asset.organization.name,
        "organization_id": str(asset.organization_id),
        "version": asset.version,
        "user": assignment.employee.name if assignment and assignment.employee else "미배정 / 공용",
    }


def maintenance_json(contract):
    remaining = (contract.ends_on - timezone.localdate()).days if contract.ends_on else None
    return {
        "id": str(contract.id),
        "company": contract.company.code,
        "organization": contract.organization.name if contract.organization else "",
        "organization_id": str(contract.organization_id) if contract.organization_id else "",
        "vendor": contract.vendor,
        "service": contract.service,
        "quantity": contract.quantity,
        "period": contract.period,
        "starts_on": contract.starts_on.isoformat() if contract.starts_on else "",
        "ends_on": contract.ends_on.isoformat() if contract.ends_on else "",
        "remaining": remaining,
        "costs": contract.costs or {},
        "contact": contract.contact,
        "note": contract.note,
        "active": contract.active,
        "version": contract.version,
    }


def paginate(request, qs, render):
    try:
        page = max(1, int(request.query_params.get("page", "1")))
        size = min(100, max(1, int(request.query_params.get("page_size", "30"))))
    except (TypeError, ValueError):
        raise ValidationError("페이지 값이 올바르지 않습니다.")
    total = qs.count()
    return Response(
        {
            "data": [render(o) for o in qs[(page - 1) * size : page * size]],
            "meta": {"page": page, "page_size": size, "total": total},
        }
    )


@api_view(["GET"])
def me(request):
    employee = policies.employee_for(request.user)
    return Response(
        {
            "data": {
                "username": request.user.username,
                "name": services.actor_name(request.user),
                "manager": policies.is_manager(request.user),
                "employee_id": str(employee.id) if employee else None,
                "roles": list(request.user.role_grants.values_list("role", flat=True).distinct()),
            }
        }
    )


@api_view(["GET"])
@read_snapshot()
def dashboard(request):
    now = timezone.now()
    tickets = policies.tickets_visible(request.user)
    counts = tickets.aggregate(
        total=Count("pk"),
        open=Count("pk", filter=Q(status__in=policies.OPEN_STATES)),
        unassigned=Count("pk", filter=Q(status__in=policies.OPEN_STATES, assignee__isnull=True)),
        overdue=Count("pk", filter=Q(status__in=policies.OPEN_STATES, due_at__lt=now)),
        waiting=Count("pk", filter=Q(status="WAITING_REQUESTER")),
    )
    assets = policies.assets_visible(request.user)
    return Response(
        {
            "data": {
                "counts": counts,
                "recent": [ticket_json(t, as_of=now) for t in tickets[:6]],
                "assets": assets.aggregate(
                    total=Count("pk"),
                    using=Count("pk", filter=Q(status="IN_USE")),
                    stock=Count("pk", filter=Q(status="IN_STOCK")),
                    repair=Count("pk", filter=Q(status="REPAIR")),
                ),
                "people": policies.employees_visible(request.user).filter(active=True).count(),
                "organizations": policies.organizations_visible(request.user).count(),
                "as_of": now.isoformat(),
            }
        }
    )


@api_view(["GET", "POST"])
def tickets(request, portal=False):
    if request.method == "POST":
        payload = parse(
            serializers.TicketCreate if portal else serializers.AdminTicketCreate, request.data
        )
        # UUID values are serialized identically for the idempotency hash.
        import json

        payload = json.loads(json.dumps(payload, default=str))
        ticket, created = services.create_ticket(
            request.user, payload, request.headers.get("Idempotency-Key"), portal=portal
        )
        return Response(
            {"data": ticket_json(ticket, detail=True, portal=portal)},
            status=201 if created else 200,
        )
    qs = policies.tickets_visible(request.user, portal=portal)
    queue = request.query_params.get("queue", "all")
    if queue not in ["all", "open", "mine", "unassigned", "overdue", "waiting", "progress", "done"]:
        raise ValidationError("큐를 확인하세요.")
    qs = policies.filter_queue(qs, queue, request.user, timezone.now())
    query = request.query_params.get("q", "").strip()[:200]
    if query:
        condition = (
            Q(title__icontains=query)
            | Q(requester_snapshot__name__icontains=query)
            | Q(requester_snapshot__organization__icontains=query)
        )
        digits = query.removeprefix("TK-")
        if digits.isdigit():
            condition |= Q(sequence_id=int(digits))
        qs = qs.filter(condition)
    state = request.query_params.get("status")
    if state:
        if state not in dict(Ticket.STATES):
            raise ValidationError("상태를 확인하세요.")
        qs = qs.filter(status=state)
    return paginate(request, qs, ticket_json)


@api_view(["GET", "PATCH"])
def ticket_detail(request, ticket_id, portal=False):
    if request.method == "PATCH":
        if portal:
            raise PermissionDenied("직원은 처리 설정을 변경할 수 없습니다.")
        payload = parse(serializers.TicketMetadata, request.data)
        ticket = services.update_ticket(request.user, ticket_id, payload)
        return Response({"data": ticket_json(ticket, detail=True)})
    ticket = get_object_or_404(policies.tickets_visible(request.user, portal=portal), pk=ticket_id)
    return Response({"data": ticket_json(ticket, detail=True, portal=portal)})


@api_view(["POST"])
def claim(request, ticket_id):
    payload = parse(serializers.Claim, request.data)
    ticket = services.claim_ticket(request.user, ticket_id, payload["expected_version"])
    return Response({"data": ticket_json(ticket, detail=True)})


@api_view(["POST"])
def transition(request, ticket_id, portal=False):
    payload = parse(serializers.Transition, request.data)
    ticket = services.transition_ticket(
        request.user,
        ticket_id,
        payload["expected_version"],
        payload["status"],
        payload["reason"],
        portal=portal,
    )
    return Response({"data": ticket_json(ticket, detail=True, portal=portal)})


@api_view(["POST"])
def message(request, ticket_id, portal=False):
    payload = parse(
        serializers.MessageCreate if portal else serializers.AdminMessageCreate, request.data
    )
    with transaction.atomic():
        ticket = get_object_or_404(
            policies.tickets_visible(request.user, portal=portal).select_for_update(of=("self",)),
            pk=ticket_id,
        )
        TicketMessage.objects.create(
            ticket=ticket,
            author=request.user,
            body=payload["body"],
            internal=payload.get("internal", False),
        )
        # Do not reveal internal-note events in the employee response.
        AuditEvent.objects.create(
            actor=request.user,
            company=ticket.company,
            ticket=ticket,
            action="ticket.message_added",
            data={"internal": payload.get("internal", False)},
        )
        ticket.version += 1
        ticket.save()
    return Response({"data": ticket_json(ticket, detail=True, portal=portal)}, status=201)


@api_view(["GET", "POST"])
def assets(request, portal=False):
    if request.method == "POST":
        if portal:
            raise PermissionDenied("직원은 자산을 등록할 수 없습니다.")
        asset = services.register_asset(request.user, parse(serializers.AssetCreate, request.data))
        return Response({"data": asset_json(asset)}, status=201)
    qs = policies.assets_visible(request.user, portal=portal)
    query = request.query_params.get("q", "").strip()[:200]
    if query:
        qs = qs.filter(
            Q(number__icontains=query)
            | Q(maker__icontains=query)
            | Q(model__icontains=query)
            | Q(serial__icontains=query)
            | Q(company__code__icontains=query)
            | Q(organization__name__icontains=query)
            | Q(assignments__employee__name__icontains=query)
        ).distinct()
    category = request.query_params.get("category", "").strip()
    status = request.query_params.get("status", "").strip()
    company = request.query_params.get("company", "").strip()
    if category in {"PC", "모니터"}:
        qs = qs.filter(category=category)
    if status in {"IN_USE", "IN_STOCK", "REPAIR", "LOST", "DISPOSED"}:
        qs = qs.filter(status=status)
    if company:
        qs = qs.filter(company__code=company[:20])
    inspection = request.query_params.get("inspection", "")
    if inspection == "missing":
        qs = qs.filter(Q(serial="") | Q(model=""))
    elif inspection == "aged":
        qs = qs.filter(pk__in=[a.pk for a in qs if asset_grade(a) == "C"])
    response = paginate(request, qs.order_by("number", "id"), asset_json)
    base = policies.assets_visible(request.user, portal=portal)
    response.data["meta"]["categories"] = {
        "all": base.count(), "PC": base.filter(category="PC").count(),
        "모니터": base.filter(category="모니터").count(),
    }
    return response


@api_view(["GET", "PATCH"])
def asset_detail(request, asset_id, portal=False):
    if request.method == "PATCH":
        if portal:
            raise PermissionDenied("직원은 자산 정보를 수정할 수 없습니다.")
        payload = parse(serializers.AssetUpdate, request.data)
        asset = services.update_asset(request.user, asset_id, payload)
    else:
        asset = get_object_or_404(policies.assets_visible(request.user, portal=portal), pk=asset_id)
    result = asset_json(asset)
    if not portal:
        result["legacy_data"] = asset.legacy_data
        result["tickets"] = [
            ticket_json(t) for t in policies.tickets_visible(request.user).filter(assets=asset)
        ]
        recent = AuditEvent.objects.filter(
            company=asset.company, action__startswith="asset."
        ).order_by("-created_at")[:100]
        result["history"] = [
            {"action": event.action, "created_at": event.created_at.isoformat(), "data": event.data}
            for event in recent
            if (event.data or {}).get("asset_id") == str(asset.id)
        ][:10]
    return Response({"data": result})


@api_view(["POST"])
def asset_assign(request, asset_id, returning=False):
    payload = parse(serializers.Claim if returning else serializers.Assign, request.data)
    asset = services.assign_asset(
        request.user,
        asset_id,
        payload.get("employee_id"),
        payload["expected_version"],
        returning=returning,
    )
    return Response({"data": asset_json(asset)})


@api_view(["GET"])
def asset_export(request):
    if not request.user.role_grants.filter(role="asset_manager").exists():
        raise PermissionDenied("자산 Excel 다운로드 권한이 없습니다.")
    from io import BytesIO
    from openpyxl import Workbook

    assets = Asset.objects.filter(
        policies.company_scope(request.user, "asset_manager")
    ).select_related("company", "organization").prefetch_related("assignments__employee").order_by("company__code", "number")
    columns = [
        "관리번호", "법인", "관리조직", "장비구분", "장비유형", "제조사", "모델", "일련번호",
        "상태", "사용자", "CPU", "비트", "RAM", "SSD", "HDD", "그래픽", "운영체제", "OS 에디션",
        "화면크기", "구입일/제조일", "Office", "한글", "SAP", "백신", "Docuone", "OfficeKeeper", "기타SW", "비고",
    ]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "자산 목록"
    sheet.append(columns)
    for asset in assets:
        meta = asset.legacy_data or {}
        software = meta.get("software") or {}
        assignment = asset.assignments.filter(returned_at__isnull=True).select_related("employee").first()
        sheet.append([
            asset.number, asset.company.code, asset.organization.name, asset.category,
            {"NOTEBOOK": "노트북", "DESKTOP": "데스크톱"}.get(meta.get("device_type"), ""),
            asset.maker, asset.model, asset.serial, asset.get_status_display(),
            assignment.employee.name if assignment and assignment.employee else "",
            meta.get("cpu", ""), meta.get("bits", ""), meta.get("ram", ""), meta.get("ssd", ""), meta.get("hdd", ""),
            meta.get("gpu", ""), meta.get("os", ""), meta.get("os_edition", ""), meta.get("size", ""),
            meta.get("purchase_date", "") or meta.get("manufacture_date", ""), software.get("office", ""), software.get("hangul", ""),
            software.get("sap", ""), software.get("antivirus", ""), software.get("docuone", ""),
            software.get("officekeeper", ""), software.get("other", ""), meta.get("note", ""),
        ])
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Bumjin_IT_assets.xlsx"'
    return response


@api_view(["GET"])
def asset_qr(request, asset_id):
    asset = get_object_or_404(policies.assets_visible(request.user), pk=asset_id)
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderSVG

    url = request.build_absolute_uri(f"/a/{asset.id}")
    qr = QrCodeWidget(url, barLevel="H", barBorder=4)
    x0, y0, x1, y1 = qr.getBounds()
    drawing = Drawing(240, 240, transform=[240 / (x1 - x0), 0, 0, 240 / (y1 - y0), 0, 0])
    drawing.add(qr)
    return HttpResponse(renderSVG.drawToString(drawing), content_type="image/svg+xml")


@api_view(["POST"])
def asset_labels(request):
    if not request.user.role_grants.filter(role="asset_manager").exists():
        raise PermissionDenied("라벨 출력 권한이 없습니다.")
    ids = request.data.get("asset_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
        raise ValidationError({"asset_ids": "자산 1~500개를 선택하세요."})
    if len(set(map(str, ids))) != len(ids):
        raise ValidationError({"asset_ids": "중복 자산이 포함되어 있습니다."})
    qs = Asset.objects.filter(
        policies.company_scope(request.user, "asset_manager"), pk__in=ids
    ).select_related("company", "organization")
    assets_by_id = {str(asset.id): asset for asset in qs}
    if len(assets_by_id) != len(ids):
        raise NotFound("선택한 자산을 찾을 수 없거나 출력 권한이 없습니다.")
    ordered = [{"id": str(asset.id), "number": asset.number} for asset in (assets_by_id[str(i)] for i in ids)]
    from .label_pdf import make_pdf

    root = Path(__file__).resolve().parents[2]
    logo = root / "public" / "qr-label-logo.svg"
    try:
        pdf = make_pdf(ordered, request.build_absolute_uri("/"), logo, request.data)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="Bumjin_QR_labels.pdf"'
    return response


@api_view(["GET", "POST"])
def maintenance_contracts(request):
    if not request.user.role_grants.filter(role="asset_manager").exists():
        raise PermissionDenied("유지보수 관리 권한이 없습니다.")
    if request.method == "POST":
        payload = parse(serializers.MaintenanceContractCreate, request.data)
        contract = services.create_maintenance_contract(request.user, payload)
        return Response({"data": maintenance_json(contract)}, status=201)
    qs = MaintenanceContract.objects.filter(
        policies.company_scope(request.user, "asset_manager")
    ).select_related("company", "organization").order_by("ends_on", "vendor", "service")
    query = request.query_params.get("q", "").strip()[:200]
    if query:
        qs = qs.filter(Q(vendor__icontains=query) | Q(service__icontains=query) | Q(contact__icontains=query))
    active = request.query_params.get("active", "")
    if active in {"true", "false"}:
        qs = qs.filter(active=(active == "true"))
    data = [maintenance_json(contract) for contract in qs]
    return Response({"data": data, "meta": {"total": len(data)}})


@api_view(["PATCH"])
def maintenance_contract_detail(request, contract_id):
    if not request.user.role_grants.filter(role="asset_manager").exists():
        raise PermissionDenied("유지보수 관리 권한이 없습니다.")
    payload = parse(serializers.MaintenanceContractUpdate, request.data)
    contract = services.update_maintenance_contract(request.user, contract_id, payload)
    return Response({"data": maintenance_json(contract)})


@api_view(["GET"])
def organizations(request):
    return Response(
        {
            "data": [
                {
                    "id": str(o.id),
                    "name": o.name,
                    "company": o.company.code,
                    "parent_id": str(o.parent_id) if o.parent_id else None,
                }
                for o in (
                    Organization.objects.filter(
                        policies.company_scope(request.user, "asset_manager")
                    ).select_related("company", "parent")
                    if request.query_params.get("purpose") == "asset"
                    else policies.organizations_visible(request.user)
                ).order_by("company__code", "name")
            ]
        }
    )


@api_view(["GET"])
def employees(request):
    role = {"request": "support_agent", "assignment": "asset_manager"}.get(
        request.query_params.get("purpose"), "organization_manager"
    )
    qs = policies.employees_visible(request.user, role=role).filter(active=True)
    if request.query_params.get("directory") == "1":
        def directory_employee(employee):
            return {
                "id": str(employee.id),
                "name": employee.name,
                "email": employee.email,
                "organization": employee.organization.name,
                "organization_id": str(employee.organization_id),
                "company": employee.company.code,
            }
        return paginate(request, qs.select_related("company", "organization").order_by("name", "id"), directory_employee)
    return Response(
        {
            "data": [
                {
                    "id": str(e.id),
                    "name": e.name,
                    "email": e.email,
                    "organization": e.organization.name,
                    "organization_id": str(e.organization_id),
                    "company": e.company.code,
                }
                for e in qs.order_by("name")[:500]
            ]
        }
    )


@api_view(["GET"])
def requester_assets(request, employee_id):
    employee = get_object_or_404(
        policies.employees_visible(request.user, role="support_agent").filter(active=True),
        pk=employee_id,
    )
    from .models import Asset

    qs = (
        Asset.objects.filter(
            company=employee.company,
            assignments__employee=employee,
            assignments__returned_at__isnull=True,
        )
        .select_related("company", "organization")
        .distinct()
    )
    return Response({"data": [asset_json(a) for a in qs.order_by("number")[:100]]})


@api_view(["POST"])
def draft_create(request):
    if request.data:
        raise ValidationError("초안 생성에는 추가 필드가 필요하지 않습니다.")
    if not policies.employee_for(request.user) and not policies.is_manager(request.user):
        raise PermissionDenied()
    # Bound unused drafts per account to prevent unbounded staging storage.
    if (
        UploadDraft.objects.filter(
            owner=request.user, ticket__isnull=True, expires_at__gt=timezone.now()
        ).count()
        >= 20
    ):
        raise ValidationError(
            "미완료 첨부가 많습니다. 기존 요청을 완료하거나 잠시 후 다시 시도하세요."
        )
    draft = UploadDraft.objects.create(
        owner=request.user, expires_at=timezone.now() + timedelta(hours=24)
    )
    return Response(
        {"data": {"id": str(draft.id), "expires_at": draft.expires_at.isoformat()}}, status=201
    )


@api_view(["POST"])
def draft_upload(request, draft_id):
    if set(request.data) - {"file"} or len(request.FILES.getlist("file")) != 1:
        raise ValidationError("파일을 하나씩 업로드하세요.")
    upload = request.FILES.get("file")
    if upload is None:
        raise ValidationError("파일을 선택하세요.")
    metadata = validate_upload(upload)
    saved = None
    try:
        with transaction.atomic():
            draft = get_object_or_404(
                UploadDraft.objects.select_for_update(),
                pk=draft_id,
                owner=request.user,
                ticket__isnull=True,
                expires_at__gt=timezone.now(),
            )
            if draft.attachments.count() >= MAX_FILES:
                raise ValidationError("최대 5개까지 첨부할 수 있습니다.")
            saved = Attachment(draft=draft, **metadata)
            saved.file.save("upload", upload, save=False)
            saved.save()
    except Exception:
        if saved and saved.file.name:
            saved.file.storage.delete(saved.file.name)
        raise
    return Response({"data": file_json(saved)}, status=201)


@api_view(["DELETE"])
def draft_remove(request, draft_id, file_id):
    with transaction.atomic():
        draft = get_object_or_404(
            UploadDraft.objects.select_for_update(),
            pk=draft_id,
            owner=request.user,
            ticket__isnull=True,
        )
        file = get_object_or_404(Attachment, pk=file_id, draft=draft, ticket__isnull=True)
        storage, name = file.file.storage, file.file.name
        file.delete()
        transaction.on_commit(lambda: storage.delete(name))
    return Response(status=204)


@api_view(["GET"])
def download(request, file_id):
    file = get_object_or_404(Attachment.objects.select_related("draft"), pk=file_id)
    if file.ticket_id:
        allowed = (
            policies.tickets_visible(request.user).filter(pk=file.ticket_id).exists()
            or policies.tickets_visible(request.user, portal=True)
            .filter(pk=file.ticket_id)
            .exists()
        )
    else:
        allowed = (
            file.draft.owner_id == request.user.pk
            and file.draft.ticket_id is None
            and file.draft.expires_at > timezone.now()
        )
    if not allowed:
        raise NotFound()
    try:
        handle = file.file.open("rb")
    except FileNotFoundError:
        raise NotFound("파일을 찾을 수 없습니다. 관리자에게 문의하세요.")
    response = FileResponse(
        handle, as_attachment=True, filename=file.name, content_type=file.media_type
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
