from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("support", "0015_support_routing"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="supportknowledgearticle", name="seo_description", field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="supportknowledgearticle", name="language", field=models.CharField(db_index=True, default="en", max_length=12)),
        migrations.CreateModel(
            name="SupportKnowledgeArticleRevision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField()),
                ("title", models.CharField(max_length=180)),
                ("summary", models.CharField(blank=True, max_length=320)),
                ("seo_description", models.CharField(blank=True, max_length=160)),
                ("language", models.CharField(default="en", max_length=12)),
                ("body", models.TextField(max_length=30000)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")], max_length=16)),
                ("category_name", models.CharField(blank=True, max_length=100)),
                ("all_websites", models.BooleanField(default=True)),
                ("website_ids", models.JSONField(blank=True, default=list)),
                ("is_featured", models.BooleanField(default=False)),
                ("change_note", models.CharField(blank=True, max_length=255)),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="support.supportknowledgearticle")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_knowledge_revisions_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-version"]},
        ),
        migrations.CreateModel(
            name="SupportKnowledgeRelatedArticle",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="related_links", to="support.supportknowledgearticle")),
                ("related_article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="related_to_links", to="support.supportknowledgearticle")),
            ],
            options={"ordering": ["sort_order", "created_at"]},
        ),
        migrations.AddConstraint(model_name="supportknowledgearticlerevision", constraint=models.UniqueConstraint(fields=("article", "version"), name="uniq_support_kb_revision_version")),
        migrations.AddIndex(model_name="supportknowledgearticlerevision", index=models.Index(fields=["article", "-version"], name="sup_kb_revision_article_idx")),
        migrations.AddConstraint(model_name="supportknowledgerelatedarticle", constraint=models.UniqueConstraint(fields=("article", "related_article"), name="uniq_support_kb_related_article")),
        migrations.AddConstraint(model_name="supportknowledgerelatedarticle", constraint=models.CheckConstraint(condition=~models.Q(article=models.F("related_article")), name="support_kb_related_not_self")),
    ]
