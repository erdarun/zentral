from importlib import import_module
from itertools import chain
import logging
import os
import plistlib
import shutil
from subprocess import check_call, check_output
import tempfile
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from django import forms
from django.utils.translation import ugettext_lazy as _
from django.http import HttpResponse
from zentral.conf import settings
from zentral.contrib.inventory.models import MetaBusinessUnit
from zentral.utils.api_views import make_secret

logger = logging.getLogger("zentral.utils.osx_package")


class EnrollmentForm(forms.Form):
    meta_business_unit = forms.ModelChoiceField(
        label=_("Business unit"),
        queryset=MetaBusinessUnit.objects.available_for_api_enrollment(),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        self.standalone = kwargs.pop("standalone", False)
        self.update_for = kwargs.pop("update_for", None)
        super().__init__(*args, **kwargs)
        if "initial" in kwargs and "meta_business_unit" in kwargs["initial"]:
            self.fields["meta_business_unit"].widget = forms.HiddenInput()
        if self.standalone:
            self.fields["product_archive_title"] = forms.CharField(label="name", required=True, max_length=256)

    def get_build_kwargs(self):
        kwargs = {}
        pat = self.cleaned_data.get("product_archive_title")
        if pat:
            kwargs["product_archive_title"] = pat
        return kwargs


class BasePackageBuilder(object):
    def __init__(self):
        self.tempdir = tempfile.mkdtemp(suffix=self.__module__)
        self.builddir = os.path.join(self.tempdir, "build")

    def _extract_xar_archive(self, xar_archive, destination):
        check_call(["/usr/local/bin/xar", "-x", "-C", destination, "-f", xar_archive])

    def _get_signature_size(self, private_key):
        return len(check_output(": | openssl dgst -sign '{}' -binary".format(private_key), shell=True))

    def _add_cert_and_get_digest_info(self, pkg_path, certificate, signature_size):
        digest_info_file = os.path.join(self.tempdir, "digestinfo.dat")
        check_call(["/usr/local/bin/xar", "--sign",
                    "-f", pkg_path,
                    "--digestinfo", digest_info_file,
                    "--sig-size", str(signature_size),
                    "--cert-loc", certificate])
        return digest_info_file

    def _sign_digest_info(self, digest_info_file, private_key):
        signature_file = os.path.join(self.tempdir, "signature.dat")
        check_call(["/usr/bin/openssl", "rsautl", "-sign",
                    "-inkey", private_key,
                    "-in", digest_info_file,
                    "-out", signature_file])
        return signature_file

    def _inject_signature(self, signature_file, pkg_path):
        check_call(["/usr/local/bin/xar",
                    "--inject-sig", signature_file,
                    "-f", pkg_path])

    def _sign_pkg(self, pkg_path, certificate, private_key):
        signature_size = self._get_signature_size(private_key)
        digest_info_file = self._add_cert_and_get_digest_info(pkg_path, certificate, signature_size)
        signature_file = self._sign_digest_info(digest_info_file, private_key)
        self._inject_signature(signature_file, pkg_path)

    def _get_certificate_and_private_key(self):
        signature_cfg = {}
        if "developer_id" in settings["api"]:
            dev_id = settings["api"]["developer_id"]
            for attr in ("certificate", "private_key"):
                filepath = dev_id.get(attr)
                if not filepath:
                    logger.error("Missing %s in developer id configuration", attr)
                elif not os.path.isfile(filepath):
                    logger.error("File %s does not exist", filepath)
                else:
                    signature_cfg[attr] = filepath
        return signature_cfg.get("certificate"), signature_cfg.get("private_key")

    def _clean(self):
        shutil.rmtree(self.tempdir)

    def _build_pkg(self):
        package_path = os.path.join(self.tempdir, self.package_name)
        check_call('cd "{}" && '
                   'xar --compression none -cf "{}" .'.format(self.package_dir, package_path),
                   shell=True)
        certificate, private_key = self._get_certificate_and_private_key()
        if certificate and private_key:
            self._sign_pkg(package_path, certificate, private_key)
        # TODO: MEMORY
        with open(package_path, 'rb') as f:
            package_content = f.read()
        self._clean()
        return package_content


class ProductArchiveBuilder(BasePackageBuilder):
    def __init__(self, title, product_archive=None):
        super().__init__()
        self.title = title
        self.package_name = "{}.pkg".format("_".join(s.lower() for s in self.title.split()))
        self.distribution = os.path.join(self.builddir, "Distribution")
        os.makedirs(self.builddir)
        if product_archive:
            self._extract_xar_archive(product_archive, self.builddir)
            self._set_distribution_title()
        else:
            self._build_empty_distribution()
        self.package_dir = self.builddir

    def _set_distribution_title(self):
        tree = ET.parse(self.distribution)
        title = tree.find("title")
        title.text = self.title
        tree.write(self.distribution, encoding="utf-8", xml_declaration=True)

    def _build_empty_distribution(self):
        root = ET.Element("installer-gui-script")
        root.set("minSpecVersion", "1")
        title = ET.Element("title")
        title.text = self.title
        root.append(title)
        options = ET.Element("options")
        options.set("customize", "allow")
        options.set("allow-external-scripts", "no")
        root.append(options)
        domains = ET.Element("domains")
        domains.set("enable_anywhere", "true")
        root.append(domains)
        choices_outline = ET.Element("choices-outline")
        root.append(choices_outline)
        tree = ET.ElementTree(root)
        tree.write(self.distribution, encoding='utf-8', xml_declaration=True)

    def add_package(self, package):
        if os.path.isdir(package):
            # nothing to do
            pkg_tmp_dir = None
            pkg_info = os.path.join(package, "PackageInfo")
        else:
            # extract package to a temporary dir
            pkg_tmp_dir = tempfile.mkdtemp(suffix=self.__module__)
            self._extract_xar_archive(package, pkg_tmp_dir)
            pkg_info = os.path.join(pkg_tmp_dir, "PackageInfo")
        # find a dirname for the package in the product archive
        pkg_info_tree = ET.parse(pkg_info)
        pkg_info_elm = pkg_info_tree.getroot()
        pkg_identifier = pkg_info_elm.attrib["identifier"]
        pkg_version = pkg_info_elm.attrib["version"]
        pkg_short_identifier = pkg_identifier.rsplit(".", 1)[-1]
        pa_pkg_dirname = "{}-{}.pkg".format(pkg_short_identifier, pkg_version)
        pa_pkg_path = os.path.join(self.builddir, pa_pkg_dirname)
        if pkg_tmp_dir:
            # move the temporary extracted files
            shutil.move(pkg_tmp_dir, pa_pkg_path)
        else:
            # copy the package content
            shutil.copytree(package, pa_pkg_path)
        # update the distribution file
        tree = ET.parse(self.distribution)
        root = tree.getroot()
        # Add line to choices-outline
        choice_id = pkg_short_identifier
        choices_outline = tree.find("choices-outline")
        line = ET.Element("line")
        line.set("choice", choice_id)
        choices_outline.append(line)
        # Add choice
        choice = ET.Element("choice")
        choice.set("id", choice_id)
        choice.set("title", "{} title".format(choice_id))
        choice.set("description", "{} description".format(choice_id))
        pkg_ref = ET.Element("pkg-ref")
        pkg_ref.set("id", pkg_identifier)
        choice.append(pkg_ref)
        root.append(choice)
        # Add first pkg-ref
        pkg_ref = ET.Element("pkg-ref")
        pkg_ref.set("id", pkg_identifier)
        pkg_payload_elm = pkg_info_elm.find("payload")
        pkg_ref.set("installKBytes", pkg_payload_elm.attrib["installKBytes"])
        pkg_ref.set("version", pkg_version)
        pkg_ref.set("auth", pkg_info_elm.attrib["auth"])
        pkg_ref.text = "#{}".format(pa_pkg_dirname)
        root.append(pkg_ref)
        # Add second pkg-ref
        pkg_ref = ET.Element("pkg-ref")
        pkg_ref.set("id", pkg_identifier)
        bundle_version = ET.Element("bundle-version")
        for pkg_bundle_elm in pkg_info_elm.findall("bundle"):
            bundle = ET.Element("bundle")
            for attr in ("CFBundleShortVersionString",
                         "CFBundleVersion",
                         "id",
                         "path"):
                bundle.set(attr, pkg_bundle_elm.attrib[attr])
            bundle_version.append(bundle)
        pkg_ref.append(bundle_version)
        root.append(pkg_ref)
        tree.write(self.distribution, encoding='utf-8', xml_declaration=True)


class APIConfigToolsMixin(object):
    def make_api_secret(self):
        return make_secret(self.zentral_module, self.business_unit)

    def get_tls_hostname(self):
        if not hasattr(self, "tls_hostname"):
            tls_hostname_p = urlparse(settings['api']['tls_hostname'])
            self.tls_hostname = tls_hostname_p.netloc
        return self.tls_hostname

    def get_tls_server_certs(self):
        return settings["api"]["tls_server_certs"]


class PackageBuilder(BasePackageBuilder, APIConfigToolsMixin):
    standalone = False
    zentral_module = None
    package_name = None
    base_package_identifier = None
    build_tmpl_dir = None

    def __init__(self, business_unit, **kwargs):
        # build template dir
        super().__init__()
        shutil.copytree(self.build_tmpl_dir, self.builddir)
        self.package_dir = self.get_build_path("base.pkg")
        package_identifier_suffix = kwargs.pop("package_identifier_suffix", None)
        self.package_identifier = self._get_package_identifier(business_unit,
                                                               package_identifier_suffix)
        self.package_version = kwargs.pop("version", "1.0")
        self.business_unit = business_unit
        self.build_kwargs = kwargs

    #
    # common build steps
    #

    def _get_package_identifier(self, business_unit, package_identifier_suffix):
        l = [self.base_package_identifier]
        if business_unit:
            l.append("bu_{}".format(business_unit.get_short_key()))
        if package_identifier_suffix:
            l.append(package_identifier_suffix)
        return ".".join(l)

    def _prepare_package_info(self):
        number_of_files = install_bytes = 0
        for root, dirs, files in os.walk(self.get_root_path()):
            for name in chain(dirs, files):
                number_of_files += 1
                install_bytes += os.path.getsize(os.path.join(root, name))
        number_of_files = str(number_of_files)
        install_kbytes = str(install_bytes // 1024)
        self.replace_in_file(self.get_build_path("base.pkg", "PackageInfo"),
                             (("%NUMBER_OF_FILES%", number_of_files),
                              ("%INSTALL_KBYTES%", install_kbytes),
                              ("%PKG_IDENTIFIER%", self.package_identifier),
                              ("%VERSION%", self.package_version),))

    def _build_gziped_cpio_arch(self, dirname, arch_name):
        input_path = self.get_build_path(dirname)
        output_path = self.get_build_path("base.pkg", arch_name)
        check_call('(cd "{}" && find . | '
                   'cpio -o --quiet --format odc --owner 0:0 | '
                   'gzip -c) > "{}"'.format(input_path, output_path), shell=True)

    def _build_payload(self):
        self._build_gziped_cpio_arch("root", "Payload")

    def _build_scripts(self):
        self._build_gziped_cpio_arch("scripts", "Scripts")

    def _build_bom(self):
        check_call(["/usr/bin/mkbom", "-u", "0", "-g", "0",
                    self.get_root_path(),
                    self.get_build_path("base.pkg", "Bom")])

    #
    # API
    #

    # build

    def extra_build_steps(self):
        pass

    def get_product_archive(self):
        return None

    def get_product_archive_title(self):
        return None

    def get_extra_packages(self):
        return []

    def build(self):
        # prepare package content
        self.extra_build_steps(**self.build_kwargs)
        self._prepare_package_info()
        self._build_payload()
        self._build_scripts()
        self._build_bom()

        product_archive = self.get_product_archive()
        product_archive_title = self.get_product_archive_title()
        extra_packages = self.get_extra_packages()

        if product_archive or product_archive_title or extra_packages:
            # build a product archive
            builder = ProductArchiveBuilder(product_archive_title, product_archive)
            for extra_package in extra_packages:
                builder.add_package(extra_package)
            builder.add_package(self.package_dir)
            self._clean()
        else:
            # build a component package
            builder = self

        return builder.package_name, builder._build_pkg()

    def build_and_make_response(self):
        package_name, content = self.build()
        # TODO: memory
        response = HttpResponse(content, "application/octet-stream")
        response['Content-Length'] = len(content)
        response['Content-Disposition'] = 'attachment; filename="{}"'.format(package_name)
        return response

    # build tools

    def get_build_path(self, *args):
        return os.path.join(self.builddir, *args)

    def get_root_path(self, *args):
        return self.get_build_path("root", *args)

    def replace_in_file(self, filename, patterns):
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
        for pattern, replacement in patterns:
            content = content.replace(pattern, replacement)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)

    def set_plist_keys(self, filename, keyvals):
        if not keyvals:
            return
        with open(filename, "rb") as f:
            pl = plistlib.load(f)
        for key, val in keyvals:
            pl[key] = val
        with open(filename, "wb") as f:
            plistlib.dump(pl, f)

    def append_to_plist_key(self, filename, key, values):
        if not values:
            return
        with open(filename, "rb") as f:
            pl = plistlib.load(f)
        for val in values:
            pl.setdefault(key, []).append(val)
        with open(filename, "wb") as f:
            plistlib.dump(pl, f)

    def include_tls_server_certs(self):
        tls_server_certs_rel_path = "usr/local/zentral/tls_server_certs.crt"  # TODO: hardcoded
        shutil.copy(self.get_tls_server_certs(),
                    self.get_root_path(tls_server_certs_rel_path))
        return "/{}".format(tls_server_certs_rel_path)

    def include_tls_ca_cert(self):
        # extract root CA cert
        with open(self.get_tls_server_certs()) as f:
            fullchain = f.read()
        begin_certificate_line = "-----BEGIN CERTIFICATE-----"
        tls_ca_cert_content = "{}{}".format(
            begin_certificate_line,
            fullchain.split(begin_certificate_line)[-1]
        )
        # add it to package
        tls_ca_cert_rel_path = "usr/local/zentral/tls_ca_cert.crt"  # TODO: hardcoded
        with open(self.get_root_path(tls_ca_cert_rel_path), "w") as f:
            f.write(tls_ca_cert_content)
        return "/{}".format(tls_ca_cert_rel_path)

    def is_product_archive(self):
        return self.get_product_archive() is not None or len(self.get_extra_packages()) > 0


def get_package_builders():
    d = {}
    for app in settings['apps']:
        try:
            builder_module = import_module("{}.osx_package.builder".format(app))
        except ImportError:
            continue
        else:
            for o in builder_module.__dict__.values():
                if isinstance(o, type) and issubclass(o, PackageBuilder):
                    d["{}.{}".format(o.__module__, o.__name__)] = o
    return d


def get_standalone_package_builders():
    return {builder_key: builder_class
            for builder_key, builder_class in get_package_builders().items()
            if builder_class.standalone}
