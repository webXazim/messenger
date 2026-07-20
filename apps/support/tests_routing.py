from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.chat.models import Conversation
from apps.support.models import SupportAccount, SupportAgent, SupportConversation, SupportRoutingPolicy, SupportTeam, SupportTeamMembership, SupportVisitor, SupportWebsite, SupportWebsiteAgent, SupportWebsiteTeam
from apps.support.routing_services import assign_support_conversation

User=get_user_model()
class SupportRoutingTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username="route-owner", password="x")
        self.account=SupportAccount.objects.create(owner=self.owner, status=SupportAccount.Status.ACTIVE)
        self.website=SupportWebsite.objects.create(support_account=self.account,name="Site",domain="route.test")
        self.team=SupportTeam.objects.create(support_account=self.account,name="Support")
        SupportWebsiteTeam.objects.create(website=self.website,team=self.team,is_default=True)
        self.agents=[]
        for index in range(2):
            user=User.objects.create_user(username=f"route-{index}",password="x")
            agent=SupportAgent.objects.create(support_account=self.account,user=user,is_active=True,availability=SupportAgent.Availability.AVAILABLE,max_active_conversations=2)
            SupportWebsiteAgent.objects.create(website=self.website,agent=agent); SupportTeamMembership.objects.create(team=self.team,agent=agent); self.agents.append(agent)
        self.policy=SupportRoutingPolicy.objects.create(website=self.website,mode=SupportRoutingPolicy.Mode.ROUND_ROBIN)
    def make_conversation(self,index):
        visitor=SupportVisitor.objects.create(website=self.website,name=f"V{index}")
        chat=Conversation.objects.create(type=Conversation.ConversationType.DIRECT,title=f"C{index}")
        return SupportConversation.objects.create(conversation=chat,website=self.website,visitor=visitor)
    def test_round_robin_rotates_agents(self):
        first=self.make_conversation(1); second=self.make_conversation(2)
        assign_support_conversation(conversation=first); assign_support_conversation(conversation=second)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertNotEqual(first.assigned_agent_id,second.assigned_agent_id)
    def test_capacity_is_enforced(self):
        self.agents[0].max_active_conversations=1; self.agents[0].save(update_fields=["max_active_conversations"])
        occupied=self.make_conversation(30)
        occupied.assigned_agent=self.agents[0]; occupied.status=SupportConversation.Status.OPEN
        occupied.save(update_fields=["assigned_agent", "status", "updated_at"])
        conv=self.make_conversation(3); assign_support_conversation(conversation=conv); conv.refresh_from_db()
        self.assertEqual(conv.assigned_agent_id,self.agents[1].id)

    def test_manual_policy_leaves_conversation_unassigned(self):
        self.policy.mode=SupportRoutingPolicy.Mode.MANUAL; self.policy.save(update_fields=["mode", "updated_at"])
        conv=self.make_conversation(4); result=assign_support_conversation(conversation=conv); conv.refresh_from_db()
        self.assertFalse(result.assigned); self.assertIsNone(conv.assigned_agent_id)

    def test_least_busy_selects_lower_workload(self):
        self.policy.mode=SupportRoutingPolicy.Mode.LEAST_BUSY; self.policy.save(update_fields=["mode", "updated_at"])
        occupied=self.make_conversation(50); occupied.assigned_agent=self.agents[0]; occupied.status=SupportConversation.Status.OPEN; occupied.save(update_fields=["assigned_agent", "status", "updated_at"])
        conv=self.make_conversation(5); assign_support_conversation(conversation=conv); conv.refresh_from_db()
        self.assertEqual(conv.assigned_agent_id,self.agents[1].id)
