from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponseNotFound
from django.urls import include, path, re_path
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.chat.api import urls as chat_api_urls
from apps.support.api import urls as support_api_urls
from apps.accounts.api import urls as accounts_api_urls
from apps.common.api import urls as common_api_urls
from apps.accounts.api.views import AvatarView, LoginView, MeView, RefreshView, RegisterView, UsernameAvailabilityView
from config.centralization_views import admin_action_catalog, admin_action_execute, centralization_control_snapshot, centralization_readiness, internal_admin_monitoring
from config.admin_gate import project_admin_entry
from config.frontend import firebase_messaging_service_worker
from config.health import DeepHealthView, LiveHealthView, ReadyHealthView


def not_found(request, *args, **kwargs):
    return HttpResponseNotFound("Not found")

urlpatterns = [
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/v1/health/live/", LiveHealthView.as_view(), name="health-live"),
    path("api/v1/health/ready/", ReadyHealthView.as_view(), name="health-ready"),
    path("api/v1/health/deep/", DeepHealthView.as_view(), name="health-deep"),
    # Local/LAN authentication aliases used by the bundled web client.
    path("api/v1/auth/token/", LoginView.as_view(), name="local-auth-token"),
    path("api/v1/auth/token/refresh/", RefreshView.as_view(), name="local-auth-token-refresh"),
    path("api/v1/auth/register/", RegisterView.as_view(), name="local-auth-register"),
    path("api/v1/auth/username-availability/", UsernameAvailabilityView.as_view(), name="local-auth-username-availability"),
    path("api/v1/users/me/", MeView.as_view(), name="local-auth-me"),
    path("api/v1/users/me/avatar/", AvatarView.as_view(), name="local-auth-me-avatar"),
    path("api/v1/accounts/", include(accounts_api_urls.urlpatterns)),
    path("api/v1/realtime/", include((common_api_urls.urlpatterns, "realtime"), namespace="realtime")),
    path("api/centralization/readiness/", centralization_readiness, name="centralization-readiness"),
    path("api/centralization/control-snapshot/", centralization_control_snapshot, name="centralization-control-snapshot"),
    path("api/centralization/admin-actions/", admin_action_catalog, name="centralization-admin-actions"),
    path("api/centralization/admin-actions/<str:action>/", admin_action_execute, name="centralization-admin-action-execute"),
    path("internal/admin-monitoring", internal_admin_monitoring, name="internal-admin-monitoring"),
    path("admin-gateway-entry/", project_admin_entry, name="project-admin-gateway-entry"),
    path("admin/", admin.site.urls),
    path("api/v1/chat/", include(chat_api_urls.urlpatterns)),
    path("api/v1/chat/", include((chat_api_urls.urlpatterns, "chat"), namespace="chat")),
    path("api/v1/support/", include((support_api_urls.urlpatterns, "support"), namespace="support")),
    path("firebase-messaging-sw.js", firebase_messaging_service_worker, name="firebase-messaging-service-worker"),
]

urlpatterns.append(re_path(r"^api/.*$", not_found, name="api-not-found"))

urlpatterns += [
    path("", TemplateView.as_view(template_name="index.html"), name="frontend-index"),
    path("<path:route>", TemplateView.as_view(template_name="index.html"), name="frontend-spa"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
