from django.db.models import Q
from .models import Asset, Employee, Organization, RoleGrant, Ticket

OPEN_STATES = ["NEW", "IN_PROGRESS", "WAITING_REQUESTER", "WAITING_VENDOR"]


def employee_for(user):
    if not user.is_authenticated or not user.is_active:
        return None
    return (
        Employee.objects.filter(user=user, active=True, company__active=True)
        .select_related("company", "organization")
        .first()
    )


def company_scope(user, role):
    if not user.is_authenticated or not user.is_active:
        return Q(pk__in=[])
    grants = RoleGrant.objects.filter(user=user, role=role)
    if grants.filter(scope_type="GLOBAL", company__isnull=True).exists():
        return Q(company__active=True)
    return Q(
        company_id__in=grants.filter(scope_type="COMPANY", company__active=True).values(
            "company_id"
        )
    )


def is_manager(user):
    return user.is_authenticated and user.is_active and RoleGrant.objects.filter(user=user).exists()


def tickets_visible(user, *, portal=False):
    qs = Ticket.objects.select_related("requester", "company", "team", "assignee")
    if portal:
        employee = employee_for(user)
        return qs.filter(requester=employee) if employee else qs.none()
    return qs.filter(company_scope(user, "support_agent"), team__active=True, team__members=user)


def assets_visible(user, *, portal=False):
    qs = Asset.objects.select_related("company", "organization")
    if portal:
        employee = employee_for(user)
        return (
            qs.filter(
                assignments__employee=employee, assignments__returned_at__isnull=True
            ).distinct()
            if employee
            else qs.none()
        )
    own_scope = Q(pk__in=Asset.objects.filter(company_scope(user, "asset_manager")).values("pk"))
    linked_scope = Q(ticket__in=tickets_visible(user))
    return qs.filter(own_scope | linked_scope).distinct()


def employees_visible(user, *, role="organization_manager"):
    qs = Employee.objects.filter(company_scope(user, role)).select_related(
        "organization", "company"
    )
    if role == "support_agent":
        qs = qs.filter(
            company__supportteam__active=True, company__supportteam__members=user
        ).distinct()
    return qs


def organizations_visible(user):
    return Organization.objects.filter(company_scope(user, "organization_manager")).select_related(
        "company", "parent"
    )


def filter_queue(qs, queue, user, now):
    if queue == "open":
        return qs.filter(status__in=OPEN_STATES)
    if queue == "mine":
        return qs.filter(status__in=OPEN_STATES, assignee=user)
    if queue == "unassigned":
        return qs.filter(status__in=OPEN_STATES, assignee__isnull=True)
    if queue == "overdue":
        return qs.filter(status__in=OPEN_STATES, due_at__lt=now)
    if queue == "waiting":
        return qs.filter(status="WAITING_REQUESTER")
    if queue == "progress":
        return qs.filter(status="IN_PROGRESS")
    if queue == "done":
        return qs.filter(status__in=["RESOLVED", "CLOSED"])
    return qs
