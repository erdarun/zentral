# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2017-10-16 11:01
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('inventory', '0028_auto_20171015_1949'),
    ]

    operations = [
        migrations.CreateModel(
            name='MetaBusinessUnitPushCertificate',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('meta_business_unit', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='inventory.MetaBusinessUnit')),
            ],
        ),
        migrations.CreateModel(
            name='PushCertificate',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256, unique=True)),
                ('topic', models.CharField(max_length=256, unique=True)),
                ('not_before', models.DateTimeField()),
                ('not_after', models.DateTimeField()),
                ('certificate', models.BinaryField()),
                ('private_key', models.BinaryField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ('name', 'topic'),
            },
        ),
        migrations.AddField(
            model_name='metabusinessunitpushcertificate',
            name='push_certificate',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='mdm.PushCertificate'),
        ),
    ]
