from __future__ import annotations

from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.support.conversation_services import mark_visitor_read, send_team_message, send_visitor_message
from apps.support.models import SupportAccount, SupportAgent, SupportWebsite, SupportWebsiteAgent
from apps.support.services import deactivate_agent, get_support_context, update_agent
from apps.support.widget_services import create_widget_session
from apps.support.workflow_services import create_internal_note
from config.asgi import application

User = get_user_model()


@override_settings(
    SUPPORT_CHAT_ENABLED=True,
    SUPPORT_WIDGET_ENABLED=True,
    SUPPORT_WIDGET_REQUIRE_ORIGIN=True,
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class SupportRealtimeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user(username="owner-live", email="owner-live@example.com", password="pass")
        self.agent_user = User.objects.create_user(username="agent-live", email="agent-live@example.com", password="pass")
        self.account = SupportAccount.objects.create(
            owner=self.owner,
            status=SupportAccount.Status.ACTIVE,
            plan_code="support-live",
            website_limit=3,
            agent_limit=3,
        )
        self.website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Main website",
            domain="main.example.com",
            allowed_origins=["https://main.example.com"],
        )
        self.second_website = SupportWebsite.objects.create(
            support_account=self.account,
            name="Second website",
            domain="second.example.com",
            allowed_origins=["https://second.example.com"],
        )
        self.agent = SupportAgent.objects.create(
            support_account=self.account,
            user=self.agent_user,
            invited_by=self.owner,
            availability=SupportAgent.Availability.AVAILABLE,
        )
        SupportWebsiteAgent.objects.create(website=self.website, agent=self.agent)

    def issue_session(self, website=None, origin=None):
        website = website or self.website
        origin = origin or website.allowed_origins[0]
        issued = create_widget_session(
            website=website,
            origin=origin,
            name="Visitor",
            email="visitor@example.com",
        )
        return issued.session, issued.raw_token

    def test_owner_socket_receives_visitor_message(self):
        session, _ = self.issue_session()
        token = str(AccessToken.for_user(self.owner))

        async def scenario():
            communicator = WebsocketCommunicator(application, f"/ws/support/?token={token}")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            ready = await communicator.receive_json_from(timeout=2)
            self.assertEqual(ready["event"], "support.ready")

            await database_sync_to_async(send_visitor_message)(session=session, text="I need help")
            event = await communicator.receive_json_from(timeout=2)
            self.assertEqual(event["event"], "support.message.created")
            self.assertEqual(event["data"]["website_id"], str(self.website.id))
            self.assertEqual(event["data"]["sender"]["kind"], "visitor")
            self.assertEqual(event["data"]["text"], "I need help")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_agent_socket_receives_only_assigned_website_events(self):
        session, _ = self.issue_session(website=self.second_website, origin="https://second.example.com")
        token = str(AccessToken.for_user(self.agent_user))

        async def scenario():
            communicator = WebsocketCommunicator(application, f"/ws/support/?token={token}")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            ready = await communicator.receive_json_from(timeout=2)
            self.assertEqual(ready["data"]["website_ids"], [str(self.website.id)])

            await database_sync_to_async(send_visitor_message)(session=session, text="Private second-site message")
            self.assertTrue(await communicator.receive_nothing(timeout=0.3))
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_agent_socket_refreshes_website_groups_after_owner_changes_access(self):
        first_session, _ = self.issue_session()
        second_session, _ = self.issue_session(website=self.second_website, origin="https://second.example.com")
        token = str(AccessToken.for_user(self.agent_user))

        async def scenario():
            communicator = WebsocketCommunicator(application, f"/ws/support/?token={token}")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            ready = await communicator.receive_json_from(timeout=2)
            self.assertEqual(ready["data"]["website_ids"], [str(self.website.id)])

            await database_sync_to_async(update_agent)(
                account=self.account,
                agent=self.agent,
                website_ids=[self.second_website.id],
                max_active_conversations=self.agent.max_active_conversations,
                can_view_all_conversations=self.agent.can_view_all_conversations,
                can_assign_conversations=self.agent.can_assign_conversations,
                can_view_analytics=self.agent.can_view_analytics,
            )
            access_event = await communicator.receive_json_from(timeout=2)
            self.assertEqual(access_event["event"], "support.access.updated")
            self.assertEqual(access_event["data"]["website_ids"], [str(self.second_website.id)])

            await database_sync_to_async(send_visitor_message)(session=second_session, text="Now visible")
            visible_event = await communicator.receive_json_from(timeout=2)
            self.assertEqual(visible_event["event"], "support.message.created")
            self.assertEqual(visible_event["data"]["website_id"], str(self.second_website.id))
            refresh_event = await communicator.receive_json_from(timeout=2)
            self.assertEqual(refresh_event["event"], "support.website.updated")

            await database_sync_to_async(send_visitor_message)(session=first_session, text="No longer visible")
            self.assertTrue(await communicator.receive_nothing(timeout=0.3))
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_deactivated_agent_socket_closes_without_affecting_messenger_auth(self):
        token = str(AccessToken.for_user(self.agent_user))

        async def scenario():
            communicator = WebsocketCommunicator(application, f"/ws/support/?token={token}")
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.receive_json_from(timeout=2)

            await database_sync_to_async(deactivate_agent)(account=self.account, agent=self.agent)
            output = await communicator.receive_output(timeout=2)
            self.assertEqual(output["type"], "websocket.close")
            self.assertEqual(output["code"], 4403)

        async_to_sync(scenario)()

    def test_widget_socket_receives_team_reply(self):
        session, raw_token = self.issue_session()
        support_conversation, _ = send_visitor_message(session=session, text="Hello")

        async def scenario():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/support/widget/{self.website.site_key}/?session_id={session.id}&token={raw_token}",
                headers=[(b"origin", b"https://main.example.com")],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            ready = await communicator.receive_json_from(timeout=2)
            self.assertEqual(ready["event"], "support.widget.ready")

            def team_reply():
                return send_team_message(
                    context=get_support_context(self.owner),
                    actor=self.owner,
                    support_conversation=support_conversation,
                    text="We are here",
                )

            await database_sync_to_async(team_reply)()
            event = await communicator.receive_json_from(timeout=2)
            self.assertEqual(event["event"], "support.message.created")
            self.assertEqual(event["data"]["sender"]["kind"], "owner")
            self.assertEqual(event["data"]["text"], "We are here")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_repeated_widget_read_acknowledgement_is_idempotent(self):
        session, _ = self.issue_session()
        support_conversation, _ = send_visitor_message(session=session, text="Hello")
        team_message = send_team_message(
            context=get_support_context(self.owner),
            actor=self.owner,
            support_conversation=support_conversation,
            text="We are here",
        )

        with patch("apps.support.conversation_services._publish_receipt_event") as publish:
            support_conversation = mark_visitor_read(
                support_conversation=support_conversation,
                message_id=team_message.id,
            )
            first_ack_event_count = publish.call_count
            support_conversation = mark_visitor_read(
                support_conversation=support_conversation,
                message_id=team_message.id,
            )

        self.assertEqual(first_ack_event_count, 2)
        self.assertEqual(publish.call_count, first_ack_event_count)
        self.assertEqual(support_conversation.visitor_last_read_message_id, team_message.id)

    def test_widget_presence_activity_typing_and_receipts_are_realtime(self):
        session, raw_token = self.issue_session()
        support_conversation, visitor_message = send_visitor_message(session=session, text="Hello")
        token = str(AccessToken.for_user(self.owner))

        async def scenario():
            team = WebsocketCommunicator(application, f"/ws/support/?token={token}")
            widget = WebsocketCommunicator(
                application,
                f"/ws/support/widget/{self.website.site_key}/?session_id={session.id}&token={raw_token}",
                headers=[(b"origin", b"https://main.example.com")],
            )
            self.assertTrue((await team.connect())[0])
            await team.receive_json_from(timeout=2)
            self.assertTrue((await widget.connect())[0])
            await widget.receive_json_from(timeout=2)

            presence = await team.receive_json_from(timeout=2)
            self.assertEqual(presence["event"], "support.visitor.presence")
            self.assertTrue(presence["data"]["is_online"])

            await widget.send_json_to({
                "event": "support.visitor.activity",
                "data": {
                    "current_page_url": "https://main.example.com/pricing",
                    "referrer": "https://main.example.com/",
                },
            })
            activity = await team.receive_json_from(timeout=2)
            self.assertEqual(activity["event"], "support.visitor.presence")
            self.assertEqual(activity["data"]["current_page_url"], "https://main.example.com/pricing")

            await widget.send_json_to({"event": "support.typing.start", "data": {}})
            typing = await team.receive_json_from(timeout=2)
            self.assertEqual(typing["event"], "support.typing.started")
            self.assertEqual(typing["data"]["sender"]["kind"], "visitor")

            await team.send_json_to({
                "event": "support.message.read",
                "data": {
                    "conversation_id": str(support_conversation.id),
                    "message_id": str(visitor_message.id),
                },
            })
            receipt = await widget.receive_json_from(timeout=2)
            self.assertEqual(receipt["event"], "support.message.read")
            self.assertEqual(receipt["data"]["message_id"], str(visitor_message.id))
            team_receipt = await team.receive_json_from(timeout=2)
            self.assertEqual(team_receipt["event"], "support.message.read")

            await widget.disconnect()
            offline = await team.receive_json_from(timeout=2)
            self.assertEqual(offline["event"], "support.visitor.presence")
            self.assertFalse(offline["data"]["is_online"])
            await team.disconnect()

        async_to_sync(scenario)()
        support_conversation.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(session.current_page_url, "https://main.example.com/pricing")
        read_state = support_conversation.read_states.get(user=self.owner)
        self.assertEqual(read_state.last_read_message_id, visitor_message.id)
        self.assertEqual(read_state.last_delivered_message_id, visitor_message.id)

    def test_private_workflow_event_reaches_team_but_not_widget(self):
        session, raw_token = self.issue_session()
        support_conversation, _ = send_visitor_message(session=session, text="Hello")
        token = str(AccessToken.for_user(self.owner))

        async def scenario():
            team = WebsocketCommunicator(application, f"/ws/support/?token={token}")
            widget = WebsocketCommunicator(
                application,
                f"/ws/support/widget/{self.website.site_key}/?session_id={session.id}&token={raw_token}",
                headers=[(b"origin", b"https://main.example.com")],
            )
            team_connected, _ = await team.connect()
            widget_connected, _ = await widget.connect()
            self.assertTrue(team_connected)
            self.assertTrue(widget_connected)
            await team.receive_json_from(timeout=2)
            await widget.receive_json_from(timeout=2)
            presence = await team.receive_json_from(timeout=2)
            self.assertEqual(presence["event"], "support.visitor.presence")
            self.assertTrue(presence["data"]["is_online"])

            def add_note():
                return create_internal_note(
                    context=get_support_context(self.owner),
                    conversation=support_conversation,
                    actor=self.owner,
                    body="Private team context",
                )

            await database_sync_to_async(add_note)()
            event = await team.receive_json_from(timeout=2)
            self.assertEqual(event["event"], "support.conversation.private_updated")
            self.assertEqual(event["data"]["conversation_id"], str(support_conversation.id))
            self.assertEqual(event["data"]["reason"], "internal_note_added")
            self.assertTrue(await widget.receive_nothing(timeout=0.3))
            await team.disconnect()
            await widget.disconnect()

        async_to_sync(scenario)()

    def test_widget_socket_rejects_wrong_origin(self):
        session, raw_token = self.issue_session()

        async def scenario():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/support/widget/{self.website.site_key}/?session_id={session.id}&token={raw_token}",
                headers=[(b"origin", b"https://evil.example.com")],
            )
            connected, close_code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(close_code, 4403)

        async_to_sync(scenario)()

    def test_unread_summary_is_separate_by_website(self):
        first_session, _ = self.issue_session()
        second_session, _ = self.issue_session(website=self.second_website, origin="https://second.example.com")
        send_visitor_message(session=first_session, text="First")
        send_visitor_message(session=second_session, text="Second")

        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.get("/api/v1/support/unread-summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread_total"], 2)
        self.assertEqual(response.data["website_unread"][str(self.website.id)], 1)
        self.assertEqual(response.data["website_unread"][str(self.second_website.id)], 1)
