from django.test import SimpleTestCase

from apps.support.knowledge_sanitizer import knowledge_plain_text, sanitize_knowledge_html


class SupportKnowledgeSanitizationTests(SimpleTestCase):
    def test_removes_scripts_handlers_and_unsafe_protocols(self):
        value = '<h2 onclick="alert(1)">Help</h2><script>alert(1)</script><a href="javascript:alert(1)">bad</a><a href="https://example.com">good</a>'
        cleaned = sanitize_knowledge_html(value)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("javascript:", cleaned)
        self.assertIn("https://example.com", cleaned)
        self.assertEqual(knowledge_plain_text(cleaned), "Helpalert(1)badgood")

    def test_preserves_supported_article_structure(self):
        value = "<h2>Reset access</h2><ol><li>Open settings.</li><li>Choose security.</li></ol>"
        cleaned = sanitize_knowledge_html(value)
        self.assertIn("<h2>Reset access</h2>", cleaned)
        self.assertIn("<ol>", cleaned)
        self.assertIn("<li>Open settings.</li>", cleaned)

    def test_insecure_http_links_are_not_published(self):
        cleaned = sanitize_knowledge_html(
            '<a href="http://example.com">insecure</a> '
            '<a href="https://example.com">secure</a>'
        )
        self.assertNotIn('href="http://example.com"', cleaned)
        self.assertIn('href="https://example.com"', cleaned)
