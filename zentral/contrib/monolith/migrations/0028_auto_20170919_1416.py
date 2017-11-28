# -*- coding: utf-8 -*-
# Generated by Django 1.10.7 on 2017-09-19 14:16
from __future__ import unicode_literals

import django.contrib.postgres.fields.jsonb
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('monolith', '0027_auto_20170919_1007'),
    ]

    operations = [
        migrations.AddField(
            model_name='printer',
            name='pkg_info',
            field=django.contrib.postgres.fields.jsonb.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='printer',
            name='tags',
            field=models.ManyToManyField(blank=True, to='inventory.Tag'),
        ),
        migrations.AlterField(
            model_name='printer',
            name='version',
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
