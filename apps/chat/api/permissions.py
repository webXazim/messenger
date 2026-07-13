from rest_framework.permissions import BasePermission
from apps.chat.models import Conversation, Message, ConversationParticipant

class IsConversationParticipant(BasePermission):
    def has_object_permission(self, request, view, obj):
        if isinstance(obj, Conversation):
            return ConversationParticipant.objects.filter(
                conversation=obj,
                user=request.user,
                left_at__isnull=True,
            ).exists()
        return False

class IsMessageOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        return isinstance(obj, Message) and obj.sender_id == request.user.id
