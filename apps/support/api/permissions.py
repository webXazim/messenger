from rest_framework.permissions import BasePermission


class IsSupportOwner(BasePermission):
    message = "Only the Support Chat owner can perform this action."

    def has_permission(self, request, view):
        account = getattr(request, "support_account", None)
        return bool(account and account.owner_id == request.user.id)
