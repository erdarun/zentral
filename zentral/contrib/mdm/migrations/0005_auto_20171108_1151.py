# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2017-11-08 11:51
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0032_auto_20171107_2157'),
        ('mdm', '0004_auto_20171108_0002'),
    ]

    operations = [
        migrations.CreateModel(
            name='OTAEnrollmentSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('PHASE_2', 'Phase 2'), ('PHASE_2_SCEP_VERIFIED', 'Phase 2 SCEP verified'), ('PHASE_3', 'Phase 3'), ('PHASE_3_SCEP_VERIFIED', 'Phase 3 SCEP verified'), ('AUTHENTICATED', 'Authenticated'), ('COMPLETED', 'Completed')], max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('enrolled_device', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='mdm.EnrolledDevice')),
                ('enrollment_secret', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ota_enrollment_session', to='inventory.EnrollmentSecret')),
                ('ota_enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='mdm.OTAEnrollment')),
                ('phase2_scep_request', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='inventory.EnrollmentSecretRequest')),
                ('phase3_scep_request', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='+', to='inventory.EnrollmentSecretRequest')),
            ],
        ),
        migrations.RemoveField(
            model_name='mdmscepchallenge',
            name='enrollment_secret',
        ),
        migrations.RemoveField(
            model_name='mdmscepchallenge',
            name='ota_enrollment',
        ),
        migrations.RemoveField(
            model_name='otascepchallenge',
            name='enrollment_secret',
        ),
        migrations.RemoveField(
            model_name='otascepchallenge',
            name='ota_enrollment',
        ),
        migrations.DeleteModel(
            name='MDMSCEPChallenge',
        ),
        migrations.DeleteModel(
            name='OTASCEPChallenge',
        ),
    ]
