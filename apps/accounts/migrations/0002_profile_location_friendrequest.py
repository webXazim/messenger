import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='location_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='FriendRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('canceled', 'Canceled')], default='pending', max_length=16)),
                ('message', models.CharField(blank=True, max_length=255)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('receiver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='friend_requests_received', to=settings.AUTH_USER_MODEL)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='friend_requests_sent', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='friendrequest',
            index=models.Index(fields=['sender', 'status'], name='accounts_fr_sender_0cf811_idx'),
        ),
        migrations.AddIndex(
            model_name='friendrequest',
            index=models.Index(fields=['receiver', 'status'], name='accounts_fr_receive_4e5bbc_idx'),
        ),
        migrations.AddIndex(
            model_name='friendrequest',
            index=models.Index(fields=['status', 'created_at'], name='accounts_fr_status_7d874b_idx'),
        ),
        migrations.AddConstraint(
            model_name='friendrequest',
            constraint=models.UniqueConstraint(fields=('sender', 'receiver'), name='uniq_friend_request_sender_receiver'),
        ),
        migrations.AddConstraint(
            model_name='friendrequest',
            constraint=models.CheckConstraint(condition=~models.Q(('sender', models.F('receiver'))), name='friend_request_sender_not_receiver'),
        ),
    ]
