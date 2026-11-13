from django.urls import path
from operations import api, views

urlpatterns = [
    path("", views.home),
    path("login", views.login_page),
    path("logout", views.logout_page),
    path("health/live", views.live),
    path("health/ready", views.ready),
    path("a/<uuid:asset_id>", views.public_asset),
    path("manage/a/<uuid:asset_id>", views.legacy_asset),
    path("api/v1/auth/me", api.me),
    path("api/v1/admin/dashboard", api.dashboard),
    path("api/v1/admin/organizations", api.organizations),
    path("api/v1/admin/employees", api.employees),
    path("api/v1/admin/employees/<uuid:employee_id>/assets", api.requester_assets),
    path("api/v1/uploads/drafts", api.draft_create),
    path("api/v1/uploads/drafts/<uuid:draft_id>/files", api.draft_upload),
    path("api/v1/uploads/drafts/<uuid:draft_id>/files/<uuid:file_id>", api.draft_remove),
    path("api/v1/files/<uuid:file_id>/download", api.download),
]
for area, portal in [("admin", False), ("portal", True)]:
    prefix = f"api/v1/{area}/"
    urlpatterns += [
        path(prefix + "tickets", api.tickets, {"portal": portal}),
        path(prefix + "tickets/<uuid:ticket_id>", api.ticket_detail, {"portal": portal}),
        path(prefix + "tickets/<uuid:ticket_id>/messages", api.message, {"portal": portal}),
        path(prefix + "tickets/<uuid:ticket_id>/transition", api.transition, {"portal": portal}),
        path(prefix + "assets", api.assets, {"portal": portal}),
        path(prefix + "assets/<uuid:asset_id>", api.asset_detail, {"portal": portal}),
    ]
urlpatterns += [
    path("api/v1/admin/tickets/<uuid:ticket_id>/claim", api.claim),
    path("api/v1/admin/assets/labels", api.asset_labels),
    path("api/v1/admin/assets/<uuid:asset_id>/qr", api.asset_qr),
    path("api/v1/admin/assets/<uuid:asset_id>/assign", api.asset_assign),
    path("api/v1/admin/assets/<uuid:asset_id>/return", api.asset_assign, {"returning": True}),
]
for route in [
    "admin",
    "admin/tickets",
    "admin/assets",
    "admin/organizations",
    "admin/settings",
    "portal",
    "portal/requests",
    "portal/assets",
]:
    urlpatterns.append(path(route, views.workspace))
