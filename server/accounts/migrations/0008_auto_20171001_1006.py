# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2017-10-01 10:06
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_usertotp'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='usertotp',
            unique_together=set([('user', 'name')]),
        ),
    ]
