from django.test import SimpleTestCase, override_settings

from apps.support.feature_flags import support_feature_enabled, support_feature_snapshot


class SupportFeatureFlagTests(SimpleTestCase):
    @override_settings(
        SUPPORT_ANALYTICS_V2_ENABLED=True,
        SUPPORT_AUTOMATIONS_ENABLED=False,
    )
    def test_feature_flags_are_explicit_and_isolated(self):
        self.assertTrue(support_feature_enabled("analytics_v2"))
        self.assertFalse(support_feature_enabled("automations"))
        self.assertFalse(support_feature_enabled("unknown"))
        snapshot = support_feature_snapshot()
        self.assertIn("routing", snapshot)
        self.assertIn("security_v2", snapshot)
