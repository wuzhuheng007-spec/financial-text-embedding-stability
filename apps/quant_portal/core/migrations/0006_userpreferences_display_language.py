from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_publicfactcard_guidance_fields")]
    operations = [migrations.AddField(model_name="userpreferences", name="display_language",
        field=models.CharField(choices=[("zh", "中文"), ("en", "English")], default="zh", max_length=2))]
