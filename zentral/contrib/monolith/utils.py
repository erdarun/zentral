import hashlib
import logging
from django.core.files.base import ContentFile
from django.template.loader import get_template

logger = logging.getLogger('zentral.contrib.monolith.utils')


# special munki catalogs and packages for zentral enrollment


def make_package_info(builder, manifest_enrollment_package, package_content):
    h = hashlib.sha256(package_content)
    installer_item_hash = h.hexdigest()
    installer_item_size = len(package_content)
    installed_size = installer_item_size * 10  # TODO: bug
    postinstall_script = """#!/usr/bin/python
import os

RECEIPTS_DIR = "/var/db/receipts/"

for filename in os.listdir(RECEIPTS_DIR):
    if filename.startswith("%s") and not filename.startswith("%s"):
        os.unlink(os.path.join(RECEIPTS_DIR, filename))
""" % (builder.base_package_identifier, builder.package_identifier)
    return {'autoremove': True,
            'description': '{} package'.format(builder.name),
            'display_name': builder.name,
            'installed_size': installed_size,
            'installer_item_hash': installer_item_hash,
            'installer_item_size': installer_item_size,
            'minimum_os_version': '10.9.0',  # TODO: hardcoded
            'name': manifest_enrollment_package.get_name(),
            'postinstall_script': postinstall_script,
            'receipts': [
                {'installed_size': installed_size,
                 'packageid': builder.package_identifier,
                 'version': builder.package_version},
            ],
            'unattended_install': True,
            'unattended_uninstall': True,
            'uninstallable': True,
            'uninstall_method': 'removepackages',
            'update_for': [manifest_enrollment_package.get_update_for()],
            'version': builder.package_version}


def build_manifest_enrollment_package(mep):
    mbu = mep.manifest.meta_business_unit
    bu = mbu.api_enrollment_business_units()[0]
    build_kwargs = mep.build_kwargs.copy()
    build_kwargs["version"] = "{}.0".format(mep.version)
    builder = mep.builder_class(bu, package_identifier_suffix="pk_{}".format(mep.id), **build_kwargs)
    _, package_content = builder.build()
    mep.pkg_info = make_package_info(builder, mep, package_content)
    mep.file.delete(False)
    mep.file.save(mep.get_installer_item_filename(),
                  ContentFile(package_content),
                  save=True)


def make_printer_package_info(printer):
    pkg_info = {
        'name': printer.get_pkg_info_name(),
        'version': "{}.0".format(printer.version),
        'display_name': "Printer '{}'".format(printer.name),
        'description': "Printer '{}' installer".format(printer.name),
        'autoremove': True,
        'unattended_install': True,
        'uninstall_method': 'uninstall_script',
        'installer_type': 'nopkg',
        'uninstallable': True,
        'unattended_uninstall': True,
        'minimum_munki_version': '2.2',
        'minimum_os_version': '10.6.0',  # TODO: HARDCODED !!!
    }
    # installcheck script
    for template_name, key in (("install_check.sh", "installcheck_script"),
                               ("postinstall.sh", "postinstall_script"),
                               ("uninstall_check.sh", "uninstallcheck_script"),  # TODO needed for autoremove, why?
                               ("uninstall.sh", "uninstall_script")):
        tmpl = get_template("monolith/printer_pkginfo/{}".format(template_name))
        pkg_info[key] = tmpl.render({"printer": printer})
    required_package = printer.required_package
    if required_package:
        pkg_info["requires"] = [required_package.name]
    return pkg_info
