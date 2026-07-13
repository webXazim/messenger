from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class CentralJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "config.authentication.CentralJWTAuthentication"
    name = "CentralJWTAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Central crescentsphere access token issued by auth_payment.",
        }
