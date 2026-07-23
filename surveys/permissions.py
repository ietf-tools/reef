# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework.permissions import BasePermission


class CanManageSurveys(BasePermission):
    """Allow only users granted the surveys.manage_surveys rule (staff)."""

    def has_permission(self, request, view):
        return bool(request.user) and request.user.has_perm("surveys.manage_surveys")


class CanViewResults(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user) and request.user.has_perm("surveys.view_results")
