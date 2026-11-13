import hashlib
import json
import uuid
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from .models import (
    Asset,
    AssetAssignment,
    Attachment,
    AuditEvent,
    Employee,
    IdempotencyRecord,
    SupportTeam,
    Ticket,
    TicketMessage,
    TicketSequence,
    UploadDraft,
)
from .policies import OPEN_STATES, assets_visible, company_scope, employee_for, tickets_visible


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"
    default_detail = "다른 변경이 있습니다. 최신 내용을 확인하세요."


def require_version(obj, expected):
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise ValidationError({"expected_version": "현재 버전을 입력하세요."})
    if obj.version != expected:
        raise Conflict()


def actor_name(user):
    employee = employee_for(user)
    return employee.name if employee else user.get_username()


@transaction.atomic
def create_ticket(user, payload, key, *, portal):
    try:
        key = uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("유효한 Idempotency-Key가 필요합니다.")
    # Serialize creates by account so duplicate requests cannot create parallel tickets/draft reuse.
    get_user_model().objects.select_for_update().get(pk=user.pk)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    previous = IdempotencyRecord.objects.filter(user=user, key=key).select_related("ticket").first()
    if previous:
        if previous.payload_hash != digest:
            raise Conflict("같은 키로 다른 요청을 제출했습니다.")
        if not tickets_visible(user, portal=portal).filter(pk=previous.ticket_id).exists():
            raise NotFound()
        return previous.ticket, False
    requester = (
        employee_for(user)
        if portal
        else Employee.objects.filter(pk=payload["requester_id"], active=True)
        .filter(company_scope(user, "support_agent"))
        .select_related("organization", "company")
        .first()
    )
    if not requester:
        raise PermissionDenied("요청자를 확인할 수 없습니다.")
    teams = SupportTeam.objects.filter(companies=requester.company, active=True)
    if not portal:
        teams = teams.filter(members=user)
    team = teams.order_by("id").first()
    if not team:
        raise ValidationError("이 법인의 접수 지원팀이 설정되지 않았습니다.")
    ids = payload.get("asset_ids", [])
    assets = list(assets_visible(user, portal=portal).filter(id__in=ids, company=requester.company))
    # Agents may connect the requester's current equipment, not unrelated company assets.
    if not portal:
        eligible = Asset.objects.filter(
            company=requester.company,
            assignments__employee=requester,
            assignments__returned_at__isnull=True,
        ).distinct()
        assets = list(eligible.filter(id__in=ids))
    if len(assets) != len(set(map(str, ids))):
        raise ValidationError("연결할 수 없는 자산이 있습니다.")
    attachment_ids = payload.get("attachment_ids", [])
    files = []
    draft_id = payload.get("draft_id")
    draft = None
    if draft_id:
        draft = (
            UploadDraft.objects.select_for_update()
            .filter(id=draft_id, owner=user, expires_at__gt=timezone.now())
            .first()
        )
        if not draft or draft.ticket_id:
            raise Conflict("첨부 초안이 만료되었거나 이미 사용됐습니다.")
    if attachment_ids:
        if not draft:
            raise ValidationError("첨부 초안을 먼저 준비하세요.")
        files = list(
            Attachment.objects.select_for_update().filter(
                id__in=attachment_ids, draft=draft, ticket__isnull=True
            )
        )
        if len(files) != len(set(map(str, attachment_ids))) or len(files) > 5:
            raise ValidationError("첨부파일을 확인하세요.")
    ticket = Ticket.objects.create(
        sequence=TicketSequence.objects.create(),
        company=requester.company,
        requester=requester,
        created_by=user,
        team=team,
        title=payload["title"].strip(),
        body=payload["body"].strip(),
        type=payload["type"],
        category=payload["category"],
        requester_snapshot={
            "name": requester.name,
            "organization": requester.organization.name,
            "company": requester.company.code,
        },
    )
    ticket.assets.set(assets)
    if files:
        Attachment.objects.filter(pk__in=[f.pk for f in files]).update(ticket=ticket)
    if draft:
        draft.ticket = ticket
        draft.save(update_fields=["ticket"])
    AuditEvent.objects.create(
        actor=user, company=ticket.company, ticket=ticket, action="ticket.created"
    )
    IdempotencyRecord.objects.create(user=user, key=key, payload_hash=digest, ticket=ticket)
    return ticket, True


@transaction.atomic
def claim_ticket(user, ticket_id, version):
    ticket = tickets_visible(user).select_for_update(of=("self",)).filter(pk=ticket_id).first()
    if not ticket:
        raise NotFound()
    require_version(ticket, version)
    if ticket.status not in OPEN_STATES:
        raise Conflict("미종료 요청만 담당할 수 있습니다.")
    if ticket.assignee_id and ticket.assignee_id != user.pk:
        raise Conflict("다른 담당자가 처리 중입니다.")
    if ticket.assignee_id == user.pk:
        return ticket
    ticket.assignee = user
    if ticket.status == "NEW":
        ticket.status = "IN_PROGRESS"
    ticket.version += 1
    ticket.save()
    AuditEvent.objects.create(
        actor=user,
        company=ticket.company,
        ticket=ticket,
        action="ticket.claimed",
        data={"assignee": user.pk},
    )
    return ticket


@transaction.atomic
def transition_ticket(user, ticket_id, version, status, reason, *, portal):
    ticket = (
        tickets_visible(user, portal=portal)
        .select_for_update(of=("self",))
        .filter(pk=ticket_id)
        .first()
    )
    if not ticket:
        raise NotFound()
    require_version(ticket, version)
    if not reason.strip():
        raise ValidationError({"reason": "처리 내용 또는 사유를 입력하세요."})
    allowed = {
        "NEW": ["IN_PROGRESS", "CANCELLED"],
        "IN_PROGRESS": ["WAITING_REQUESTER", "WAITING_VENDOR", "RESOLVED", "CANCELLED"],
        "WAITING_REQUESTER": ["IN_PROGRESS", "RESOLVED", "CANCELLED"],
        "WAITING_VENDOR": ["IN_PROGRESS", "RESOLVED", "CANCELLED"],
        "RESOLVED": ["CLOSED", "IN_PROGRESS"],
        "CLOSED": ["IN_PROGRESS"],
        "CANCELLED": [],
    }
    if status not in allowed.get(ticket.status, []):
        raise Conflict("현재 상태에서는 이 작업을 할 수 없습니다.")
    if portal and not (
        (ticket.status == "RESOLVED" and status == "CLOSED")
        or (ticket.status in ["RESOLVED", "CLOSED"] and status == "IN_PROGRESS")
    ):
        raise PermissionDenied("직원은 해결 확인 또는 재개만 할 수 있습니다.")
    if status in ["IN_PROGRESS", "WAITING_REQUESTER", "WAITING_VENDOR", "RESOLVED"]:
        if (
            not ticket.assignee_id
            or not tickets_visible(ticket.assignee).filter(pk=ticket.pk).exists()
        ):
            raise Conflict("처리 가능한 담당자를 먼저 지정하세요.")
    before = ticket.status
    ticket.status = status
    ticket.version += 1
    ticket.resolved_at = (
        timezone.now()
        if status == "RESOLVED"
        else (None if status == "IN_PROGRESS" else ticket.resolved_at)
    )
    ticket.closed_at = timezone.now() if status == "CLOSED" else None
    ticket.save()
    TicketMessage.objects.create(ticket=ticket, author=user, body=reason)
    AuditEvent.objects.create(
        actor=user,
        company=ticket.company,
        ticket=ticket,
        action="ticket.transitioned",
        data={"before": before, "after": status},
    )
    return ticket


@transaction.atomic
def assign_asset(user, asset_id, employee_id, version, *, returning=False):
    asset = (
        Asset.objects.filter(company_scope(user, "asset_manager"))
        .select_for_update()
        .filter(pk=asset_id)
        .first()
    )
    if not asset:
        raise NotFound()
    require_version(asset, version)
    current = AssetAssignment.objects.filter(asset=asset, returned_at__isnull=True).first()
    if returning:
        if asset.status == "DISPOSED":
            raise Conflict("폐기 자산은 이 작업으로 되살릴 수 없습니다.")
        if not current:
            raise Conflict("현재 배정된 자산이 아닙니다.")
        current.returned_at = timezone.now()
        current.save(update_fields=["returned_at"])
        asset.status = "IN_STOCK"
    else:
        if current or asset.status != "IN_STOCK":
            raise Conflict("보관 상태이며 미배정인 자산만 배정할 수 있습니다.")
        employee = Employee.objects.filter(
            pk=employee_id, active=True, company=asset.company
        ).first()
        if not employee:
            raise ValidationError("같은 법인의 재직 직원을 선택하세요.")
        AssetAssignment.objects.create(
            asset=asset, employee=employee, organization=employee.organization
        )
        asset.status = "IN_USE"
    asset.version += 1
    asset.save()
    AuditEvent.objects.create(
        actor=user,
        company=asset.company,
        action="asset.returned" if returning else "asset.assigned",
        data={"asset_id": str(asset.id), "employee_id": str(employee_id) if employee_id else None},
    )
    return asset


@transaction.atomic
def update_ticket(user, ticket_id, payload):
    ticket = tickets_visible(user).select_for_update(of=("self",)).filter(pk=ticket_id).first()
    if not ticket:
        raise NotFound()
    require_version(ticket, payload["expected_version"])
    before = {
        "priority": ticket.priority,
        "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
    }
    for field in ["priority", "due_at"]:
        if field in payload:
            setattr(ticket, field, payload[field])
    ticket.version += 1
    ticket.save()
    AuditEvent.objects.create(
        actor=user,
        company=ticket.company,
        ticket=ticket,
        action="ticket.metadata_updated",
        data={
            "before": before,
            "after": {
                "priority": ticket.priority,
                "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
            },
        },
    )
    return ticket


@transaction.atomic
def register_asset(user, payload):
    from .models import Company, Organization

    organization = Organization.objects.filter(
        company_scope(user, "asset_manager"), pk=payload["organization_id"]
    ).first()
    if not organization:
        raise NotFound()
    # The company row serializes number allocation, including explicitly numbered creates.
    company = Company.objects.select_for_update().get(pk=organization.company_id)
    number = payload.get("number", "").strip()
    if not number:
        kind = (
            "M"
            if payload["category"] == "모니터"
            else ("N" if payload["device_type"] == "NOTEBOOK" else "D")
        )
        prefix = f"{company.code}-{kind}-"
        suffixes = Asset.objects.filter(company=company, number__startswith=prefix).values_list(
            "number", flat=True
        )
        maximum = max(
            (int(n[len(prefix) :]) for n in suffixes if n[len(prefix) :].isdigit()), default=0
        )
        number = prefix + str(maximum + 1).zfill(3)
    if Asset.objects.filter(company=company, number=number).exists():
        raise Conflict("같은 법인에 동일한 관리번호가 있습니다.")
    asset = Asset.objects.create(
        company=company,
        organization=organization,
        number=number,
        category=payload["category"],
        maker=payload["maker"],
        model=payload["model"],
        serial=payload["serial"],
        legacy_data={**payload.get("legacy_data", {}), "device_type": payload["device_type"]},
    )
    AuditEvent.objects.create(
        actor=user,
        company=company,
        action="asset.created",
        data={"asset_id": str(asset.id), "number": asset.number},
    )
    return asset


@transaction.atomic
def update_asset(user, asset_id, payload):
    from .models import Organization

    asset = (
        Asset.objects.filter(company_scope(user, "asset_manager"))
        .select_for_update()
        .filter(pk=asset_id)
        .first()
    )
    if not asset:
        raise NotFound()
    require_version(asset, payload["expected_version"])
    before = {
        "number": asset.number,
        "organization_id": str(asset.organization_id),
        "category": asset.category,
        "maker": asset.maker,
        "model": asset.model,
        "serial": asset.serial,
        "status": asset.status,
        "legacy_data": asset.legacy_data,
    }
    if "organization_id" in payload:
        organization = Organization.objects.filter(
            company_scope(user, "asset_manager"),
            pk=payload["organization_id"],
            company=asset.company,
        ).first()
        if not organization:
            raise NotFound()
        asset.organization = organization
    if "number" in payload:
        number = payload["number"].strip()
        if Asset.objects.filter(company=asset.company, number=number).exclude(pk=asset.pk).exists():
            raise Conflict("같은 법인에 동일한 관리번호가 있습니다.")
        asset.number = number
    for field in ["category", "maker", "model", "serial"]:
        if field in payload:
            setattr(asset, field, payload[field])
    if "status" in payload:
        active = AssetAssignment.objects.filter(asset=asset, returned_at__isnull=True).exists()
        status = payload["status"]
        if active and status != "IN_USE":
            raise Conflict("직원이 배정된 자산은 먼저 수거 처리해야 상태를 변경할 수 있습니다.")
        if not active and status == "IN_USE":
            raise Conflict("사용 중 상태로 바꾸려면 직원 배정 기능을 사용하세요.")
        asset.status = status
    if "legacy_data" in payload:
        merged = dict(asset.legacy_data or {})
        incoming = payload["legacy_data"]
        if "software" in incoming:
            merged["software"] = {**(merged.get("software") or {}), **incoming.pop("software")}
        merged.update(incoming)
        asset.legacy_data = merged
    asset.version += 1
    asset.save()
    AuditEvent.objects.create(
        actor=user,
        company=asset.company,
        action="asset.updated",
        data={
            "asset_id": str(asset.id),
            "before": before,
            "after": {
                "number": asset.number,
                "organization_id": str(asset.organization_id),
                "category": asset.category,
                "maker": asset.maker,
                "model": asset.model,
                "serial": asset.serial,
                "status": asset.status,
                "legacy_data": asset.legacy_data,
            },
        },
    )
    return asset
