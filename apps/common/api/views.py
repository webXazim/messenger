from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.realtime_auth import (
    RealtimeCredentialError,
    authorize_user_audiences,
    issue_audience_grant,
    issue_call_grant,
    issue_user_realtime_ticket,
)

from .serializers import (
    RealtimeCallGrantRequestSerializer,
    RealtimeGrantRequestSerializer,
    RealtimeTicketRequestSerializer,
)


def realtime_error_response(error: RealtimeCredentialError) -> Response:
    return Response(
        {"detail": error.detail, "code": error.code},
        status=error.status_code,
    )


class RealtimeTicketView(APIView):
    throttle_scope = "realtime_ticket"

    def post(self, request):
        serializer = RealtimeTicketRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            issued = issue_user_realtime_ticket(
                user=request.user,
                request=request,
                **serializer.validated_data,
            )
        except RealtimeCredentialError as error:
            return realtime_error_response(error)
        return Response(
            {
                "ticket": issued.token,
                "expires_in": issued.expires_in,
                "expires_at": issued.expires_at,
                "protocol_version": 1,
            },
            status=status.HTTP_201_CREATED,
        )


class RealtimeGrantView(APIView):
    throttle_scope = "realtime_grant"

    def post(self, request):
        serializer = RealtimeGrantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            audiences = authorize_user_audiences(
                user=request.user,
                requested=serializer.validated_data["audiences"],
            )
            grants = []
            for audience in audiences:
                issued = issue_audience_grant(
                    user=request.user,
                    audience=audience,
                    request=request,
                )
                grants.append(
                    {
                        "audience": audience.as_dict(),
                        "grant": issued.token,
                        "expires_in": issued.expires_in,
                        "expires_at": issued.expires_at,
                    }
                )
        except RealtimeCredentialError as error:
            return realtime_error_response(error)
        return Response({"grants": grants, "protocol_version": 1})


class RealtimeCallGrantView(APIView):
    throttle_scope = "realtime_grant"

    def post(self, request):
        serializer = RealtimeCallGrantRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            issued, participant_ids = issue_call_grant(
                user=request.user,
                call_id=serializer.validated_data["call_id"],
                request=request,
            )
        except RealtimeCredentialError as error:
            return realtime_error_response(error)
        return Response(
            {
                "grant": issued.token,
                "expires_in": issued.expires_in,
                "expires_at": issued.expires_at,
                "participant_ids": participant_ids,
                "protocol_version": 1,
            }
        )
