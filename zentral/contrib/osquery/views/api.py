from base64 import b64decode
import logging
from django.core.exceptions import SuspiciousOperation
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Q
from django.utils.crypto import get_random_string
from zentral.contrib.inventory.models import MachineSnapshot, MetaMachine
from zentral.contrib.inventory.utils import commit_machine_snapshot_and_trigger_events
from zentral.core.events.base import post_machine_conflict_event
from zentral.core.probes.models import ProbeSource
from zentral.utils.api_views import JSONPostAPIView, verify_secret, APIAuthError
from zentral.contrib.inventory.conf import MACOS, platform_with_os_name
from zentral.contrib.osquery.conf import (build_osquery_conf,
                                          get_distributed_inventory_queries,
                                          INVENTORY_QUERY_NAME,
                                          INVENTORY_DISTRIBUTED_QUERY_PREFIX)
from zentral.contrib.osquery.events import (post_distributed_query_result, post_enrollment_event,
                                            post_file_carve_events, post_finished_file_carve_session,
                                            post_events_from_osquery_log, post_request_event)
from zentral.contrib.osquery.models import (enroll,
                                            DistributedQueryProbeMachine,
                                            CarveBlock, CarveSession)

logger = logging.getLogger('zentral.contrib.osquery.views.api')


class EnrollView(JSONPostAPIView):
    def check_data_secret(self, data):
        try:
            data = verify_secret(data['enroll_secret'], "zentral.contrib.osquery")
        except KeyError:
            raise SuspiciousOperation("Osquery enroll request without enroll secret")
        try:
            self.machine_serial_number = data['machine_serial_number']
        except KeyError:
            raise SuspiciousOperation("Osquery enroll secret without machine serial number")
        self.business_unit = data.get('business_unit', None)

    def do_post(self, data):
        ms, action = enroll(self.machine_serial_number,
                            self.business_unit,
                            data.get("host_identifier"),
                            self.ip)
        if ms and action:
            post_enrollment_event(ms.serial_number,
                                  self.user_agent, self.ip,
                                  {'action': action})
            return {'node_key': ms.reference}
        else:
            raise RuntimeError("Could not enroll client")


class BaseNodeView(JSONPostAPIView):
    def check_data_secret(self, data):
        auth_err = None
        try:
            self.ms = MachineSnapshot.objects.current().get(source__module='zentral.contrib.osquery',
                                                            reference=data['node_key'])
        except KeyError:
            auth_err = "Missing node_key"
        except MachineSnapshot.DoesNotExist:
            auth_err = "Wrong node_key"
        except MachineSnapshot.MultipleObjectsReturned:
            auth_err = "Multiple current osquery machine snapshots for node key '{}'".format(data['node_key'])
        if auth_err:
            logger.error("APIAuthError %s", auth_err, extra=data)
            raise APIAuthError(auth_err)
        # TODO: Better verification ?
        self.machine_serial_number = self.ms.serial_number
        self.business_unit = self.ms.business_unit

    def do_post(self, data):
        post_request_event(self.machine_serial_number,
                           self.user_agent, self.ip,
                           self.request_type)
        return self.do_node_post(data)

    def commit_inventory_query_result(self, snapshot):
        tree = self.ms.serialize()
        tree["serial_number"] = self.machine_serial_number
        tree["public_ip_address"] = self.ip
        if self.business_unit:
            tree['business_unit'] = self.business_unit.serialize()

        def clean_dict(d):
            for k, v in list(d.items()):
                if v is None or v == "":
                    del d[k]
            return d

        deb_packages = []
        network_interfaces = []
        osx_app_instances = []
        for t in snapshot:
            table_name = t.pop('table_name')
            if table_name == 'os_version':
                os_version = clean_dict(t)
                if os_version:
                    tree['os_version'] = os_version
            elif table_name == 'system_info':
                system_info = clean_dict(t)
                if system_info:
                    tree['system_info'] = system_info
            elif table_name == 'uptime':
                try:
                    system_uptime = int(t['total_seconds'])
                except (KeyError, TypeError, ValueError):
                    pass
                else:
                    if system_uptime > 0:
                        tree['system_uptime'] = system_uptime
            elif table_name == 'network_interface':
                network_interface = clean_dict(t)
                if network_interface:
                    if network_interface not in network_interfaces:
                        network_interfaces.append(network_interface)
                    else:
                        logger.warning("Duplicated network interface")
            elif table_name == 'deb_packages':
                deb_package = clean_dict(t)
                if deb_package:
                    if deb_package not in deb_packages:
                        deb_packages.append(deb_package)
                    else:
                        logger.warning("Duplicated deb package")
            elif table_name == 'apps':
                bundle_path = t.pop('bundle_path')
                osx_app = clean_dict(t)
                if osx_app and bundle_path:
                    osx_app_instance = {'app': osx_app,
                                        'bundle_path': bundle_path}
                    if osx_app_instance not in osx_app_instances:
                        osx_app_instances.append(osx_app_instance)
                    else:
                        logger.warning("Duplicated osx app instance")
        if deb_packages:
            tree["deb_packages"] = deb_packages
        if network_interfaces:
            tree["network_interfaces"] = network_interfaces
        if osx_app_instances:
            tree["osx_app_instances"] = osx_app_instances
        commit_machine_snapshot_and_trigger_events(tree)


class ConfigView(BaseNodeView):
    request_type = "config"

    def do_node_post(self, data):
        # TODO: The machine serial number is included in the string used to authenticate the requests
        # This is done in the osx pkg builder. The machine serial number should always be present here.
        # Maybe we could code a fallback to the available mbu probes if the serial number is not present.
        return build_osquery_conf(MetaMachine(self.machine_serial_number))


class CarverStartView(BaseNodeView):
    request_type = "carve_start"

    def do_node_post(self, data):
        probe_source_id = int(data["request_id"].split("_")[-1])
        probe_source = ProbeSource.objects.get(pk=probe_source_id)
        session_id = get_random_string(64)
        CarveSession.objects.create(probe_source=probe_source,
                                    machine_serial_number=self.machine_serial_number,
                                    session_id=session_id,
                                    carve_guid=data["carve_id"],
                                    carve_size=int(data["carve_size"]),
                                    block_size=int(data["block_size"]),
                                    block_count=int(data["block_count"]))
        post_file_carve_events(self.machine_serial_number, self.user_agent, self.ip,
                               [{"probe": {"id": probe_source.pk,
                                           "name": probe_source.name},
                                 "action": "start",
                                 "session_id": session_id}])
        return {"session_id": session_id}


class CarverContinueView(BaseNodeView):
    request_type = "carve_continue"

    def check_data_secret(self, data):
        # no node id => check carve session id
        auth_err = None
        try:
            self.session_id = data["session_id"]
            self.carve_session = CarveSession.objects.get(session_id=self.session_id)
            self.machine_serial_number = self.carve_session.machine_serial_number
            self.ms = MachineSnapshot.objects.current().get(
                source__module='zentral.contrib.osquery',
                serial_number=self.machine_serial_number
            )
        except KeyError:
            auth_err = "Missing session id"
        except CarveSession.DoesNotExist:
            auth_err = "Unknown session id"
        except MachineSnapshot.DoesNotExist:
            auth_err = "Unknown machine serial number"
        if auth_err:
            logger.error("APIAuthError %s", auth_err, extra=data)
            raise APIAuthError(auth_err)
        self.business_unit = self.ms.business_unit

    def do_node_post(self, data):
        data_data = data.pop("data")

        block_id = data["block_id"]
        cb = CarveBlock.objects.create(carve_session=self.carve_session,
                                       block_id=int(block_id))
        cb.file.save(block_id, SimpleUploadedFile(block_id, b64decode(data_data)))

        session_finished = (CarveBlock.objects.filter(carve_session=self.carve_session).count()
                            == self.carve_session.block_count)
        probe_source = self.carve_session.probe_source
        post_file_carve_events(self.machine_serial_number, self.user_agent, self.ip,
                               [{"probe": {"id": probe_source.pk,
                                           "name": probe_source.name},
                                 "action": "continue",
                                 "block_id": block_id,
                                 "block_size": len(data_data),
                                 "session_finished": session_finished,
                                 "session_id": self.session_id}])
        if session_finished:
            post_finished_file_carve_session(self.session_id)
        return {}


class DistributedReadView(BaseNodeView):
    request_type = "distributed_read"

    def do_node_post(self, data):
        queries = {}
        if self.machine_serial_number:
            machine = MetaMachine(self.machine_serial_number)
            queries = DistributedQueryProbeMachine.objects.new_queries_for_machine(machine)
            for query_name, query in get_distributed_inventory_queries(machine, self.ms):
                if query_name in queries:
                    logger.error("Conflict on the distributed query name %s", query_name)
                else:
                    queries[query_name] = query
        return {'queries': queries}


class DistributedWriteView(BaseNodeView):
    request_type = "distributed_write"

    @transaction.non_atomic_requests
    def do_node_post(self, data):
        dq_payloads = []
        fc_payloads = []

        def get_probe_pk(key):
            return int(key.split('_')[-1])

        queries = data['queries']

        ps_d = {ps.id: ps
                for ps in ProbeSource.objects.filter(
                    pk__in=[get_probe_pk(k) for k in queries.keys()
                            if not k.startswith(INVENTORY_DISTRIBUTED_QUERY_PREFIX)]
                ).filter(
                    Q(model='OsqueryDistributedQueryProbe') | Q(model='OsqueryFileCarveProbe')
                )}
        inventory_snapshot = []
        for key, val in queries.items():
            try:
                status = int(data['statuses'][key])
            except KeyError:
                # osquery < 2.1.2 has no statuses
                status = 0
            if key.startswith(INVENTORY_DISTRIBUTED_QUERY_PREFIX):
                if status == 0 and val:
                    inventory_snapshot.extend(val)
                else:
                    logger.warning("Inventory distributed query write with status = %s and val = %s",
                                   status, val)
            else:
                try:
                    probe_source = ps_d[get_probe_pk(key)]
                except KeyError:
                    logger.error("Unknown distributed query probe %s", key)
                else:
                    payload = {'probe': {'id': probe_source.pk,
                                         'name': probe_source.name}}
                    if status > 0:
                        # error
                        payload["error"] = True
                    elif status == 0:
                        payload["error"] = False
                        if val:
                            payload["result"] = val
                        else:
                            payload["empty"] = True
                    else:
                        raise ValueError("Unknown distributed query status '{}'".format(status))
                    if probe_source.model == 'OsqueryDistributedQueryProbe':
                        dq_payloads.append(payload)
                    else:
                        fc_payloads.append(payload)
            if dq_payloads:
                post_distributed_query_result(self.machine_serial_number,
                                              self.user_agent, self.ip,
                                              dq_payloads)
            if fc_payloads:
                post_file_carve_events(self.machine_serial_number,
                                       self.user_agent, self.ip,
                                       fc_payloads)
        if inventory_snapshot:
            self.commit_inventory_query_result(inventory_snapshot)
        return {}


class LogView(BaseNodeView):
    request_type = "log"

    def check_data_secret(self, data):
        super().check_data_secret(data)
        self.data_data = data.pop("data")
        for r in self.data_data:
            decorations = r.pop("decorations", None)
            if decorations:
                platform = platform_with_os_name(decorations.get("os_name"))
                if platform == MACOS:
                    hardware_serial = decorations.get("hardware_serial")
                    if hardware_serial and hardware_serial != self.machine_serial_number:
                        # The SN reported by osquery is not the one configured in the enrollment secret.
                        # For other platforms than MACOS, it could happen. For example, we take the GCE instance ID as
                        # serial number in the enrollment secret for linux, if possible.
                        # Osquery builds one from the SMBIOS/DMI.
                        auth_err = "osquery reported SN {} different from enrollment SN {}".format(
                            hardware_serial,
                            self.machine_serial_number
                        )
                        post_machine_conflict_event(self.request, "zentral.contrib.osquery",
                                                    hardware_serial, self.machine_serial_number,
                                                    decorations)
                        raise APIAuthError(auth_err)

    @transaction.non_atomic_requests
    def do_node_post(self, data):
        inventory_results = []
        other_results = []
        for r in self.data_data:
            if r.get('name', None) == INVENTORY_QUERY_NAME:
                inventory_results.append((r['unixTime'], r['snapshot']))
            else:
                other_results.append(r)
        if inventory_results:
            inventory_results.sort(reverse=True)
            last_snapshot = inventory_results[0][1]
            self.commit_inventory_query_result(last_snapshot)
        data['data'] = other_results
        post_events_from_osquery_log(self.machine_serial_number,
                                     self.user_agent, self.ip, data)
        return {}
