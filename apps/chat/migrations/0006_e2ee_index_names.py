from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0005_e2ee_maturity_state"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="usere2eedevicekey",
            new_name="chat_usere2_user_id_79646a_idx",
            old_name="chat_useree_user_id_74e1cc_idx",
        ),
        migrations.RenameIndex(
            model_name="usere2eedevicekey",
            new_name="chat_usere2_device__e3cf40_idx",
            old_name="chat_useree_device__fe9c6f_idx",
        ),
    ]
