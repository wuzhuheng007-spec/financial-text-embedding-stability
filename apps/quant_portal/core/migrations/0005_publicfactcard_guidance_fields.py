from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_publicfactcard_basis_text_publicfactcard_currency"),
    ]

    operations = [
        migrations.AddField(
            model_name="publicfactcard",
            name="fact_kind",
            field=models.CharField(
                choices=[("ACTUAL", "Historical reported result"), ("GUIDANCE", "Future guidance at historical release")],
                default="ACTUAL", max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="fact_topic",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="basis_note",
            field=models.CharField(blank=True, max_length=250),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="range_lower_text",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="range_upper_text",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="range_derivation",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="range_status",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="publicfactcard", name="source_review_status",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
