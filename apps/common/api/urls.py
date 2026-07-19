from django.urls import path

from .views import RealtimeCallGrantView, RealtimeGrantView, RealtimeTicketView

urlpatterns = [
    path("tickets/", RealtimeTicketView.as_view(), name="realtime-ticket"),
    path("grants/", RealtimeGrantView.as_view(), name="realtime-grant"),
    path("call-grants/", RealtimeCallGrantView.as_view(), name="realtime-call-grant"),
]
