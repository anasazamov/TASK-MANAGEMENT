from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0003_voiceprofile')]
    operations = [migrations.AddField(model_name='agentconversation', name='tools',
                                     field=models.JSONField(default=dict))]
