##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################
import time

from litp.core.plugin import Plugin
from litp.core.validators import ValidationError
from litp.core.plugin_context_api import NoSnapshotItemError
from litp.core.extension import ViewError
from litp.core.litp_logging import LitpLogger
from litp.core.task import CallbackTask
from litp.plan_types.restore_snapshot import restore_snapshot_tags

from litp.core.execution_manager import (
    CallbackExecutionException,
    PluginError,
    PlanStoppedException
)

from litp.core.node_utils import (
    wait_for_node_timestamp,
    reboot_node_force
)

from litp.core.rpc_commands import (
    run_rpc_application,
    RpcExecutionException,
    RpcCommandProcessorBase,
    RpcCommandOutputNoStderrProcessor,
    reduce_errs
)

from .volmgr_utils import (
    VolMgrUtils,
    UPGRADE_SNAPSHOT_NAME,
    MAX_VXVM_VOL_LENGTH,
    LITP_EXTRA_CHARS,
    TAG_RESERVED_LENGTH,
    MAX_LVM_SNAP_VOL_LENGTH,
    DEFAULT_RPC_TIMEOUT,
    EXPIRED_STR,
    MS_ROOT_VG_GROUP_NAME,
    LVM_FS_TYPES,
    LVM_FS_TYPES_NON_SWAP,
    ROOT_FS_MOUNT_POINT
)

from .drivers.lvm import LvmDriver, KGB
from .drivers.vxvm import VxvmDriver
from .drivers.vxvm_errors import DgNotImportedError
from .drivers.vxvm_snapshot_data_mgr import VxvmSnapshotDataMgr
from volmgr_extension.volmgr_extension import LVM_FS_DEFAULT_MOUNT_OPTIONS

log = LitpLogger()

PING_TIMEOUT = 10
PING_SUCCESS = 1
UPDATABLE_FS_TYPES = LVM_FS_TYPES_NON_SWAP + ('vxfs',)


class Timeout(object):
    def __init__(self, seconds):
        self._wait_for = seconds
        self._start_time = Timeout.time()

    @staticmethod
    def time():
        return time.time()

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)

    def has_elapsed(self):
        return self.get_elapsed_time() >= self._wait_for

    def get_elapsed_time(self):
        return Timeout.time() - self._start_time

    def get_remaining_time(self):
        return self._wait_for - self.get_elapsed_time()


class IgnoredCodesAgentProcessor(RpcCommandProcessorBase):
    # pylint: disable=W0221
    def _errors_in_agent(self, node, agent_result, ignored_codes):
        err = ""
        if agent_result['data']['status']:
            if agent_result['data']['status'] not in ignored_codes:
                err = "{0} failed with message: {1}".format(
                                            node, agent_result['data']['err']
                                                            )
            else:
                log.trace.info("Warning received in {0}: {1}".format(
                                            node, agent_result['data']['err']
                                                                     ))
        return err


class SnapshotRebootPlanner(object):
    '''Generates tasks to reboot ms and cluster nodes for snapshot restore'''

    # LITPCDS-11448: we reboot (or otherwise) whole clusters based on disk
    # type, not actual presence of volume snapshots being restored/merged
    #
    # LITPCDS-11872: This returns an ordered list of tasks, order is derived
    # from cluster dependency_list properties for clusters in a deployment
    #
    # This approach is strictly limited to clusters rebooted by the volmgr
    # plugin.  In order to properly order reboots from the volmgr plugin and
    # reboots or poweroff/poweron sequences from other plugins all according
    # to cluster dependency_list, a different approach may be required.

    def __init__(
            self,
            reboot_ms_callback,
            reboot_node_callback,
            wait_node_callback,
            force_reboot_wait_node_callback,
            warn_invalid_cluster_order):
        self.reboot_ms_callback = reboot_ms_callback
        self.reboot_node_callback = reboot_node_callback
        self.wait_node_callback = wait_node_callback
        self.force_reboot_wait_node_callback = force_reboot_wait_node_callback
        self.warn_invalid_cluster_order = warn_invalid_cluster_order

    def gen_tasks(self, context):
        '''return list of tasks to reboot nodes in deployment'''
        tasks = []
        tasks.extend(self._gen_tasks_for_deployments(context))
        tasks.extend(self._gen_tasks_for_mses(context))
        return tasks

    def _gen_tasks_for_mses(self, context):
        tasks = []
        for ms in context.snapshot_model().query("ms"):
            tasks.extend(self._gen_tasks_for_ms(ms))
        return tasks

    def _gen_tasks_for_ms(self, ms):
        tasks = []
        tasks.append(
            CallbackTask(
                ms.system if ms.system else ms,
                'Restart node "%s"' % ms.hostname,
                self.reboot_ms_callback,
                [ms.hostname],
                tag_name=restore_snapshot_tags.LMS_REBOOT_TAG,
        ))
        return tasks

    def _gen_tasks_for_deployments(self, context):
        tasks = []
        # There cannot be ordering dependencies between clusters of different
        # deployments, so just loop over deployments.
        for deployment in context.snapshot_model().query('deployment'):
            tasks.extend(self._gen_tasks_for_deployment(context, deployment))
        return tasks

    def _gen_tasks_for_deployment(self, context, deployment):
        tasks = []

        clusters = deployment.query('cluster')

        try:
            clusters = deployment.ordered_clusters
        except ViewError as ve:
            self.warn_invalid_cluster_order(deployment.item_id, ve)

        for cluster in clusters:
            if not cluster.is_initial():
                tasks.extend(self._gen_tasks_for_cluster(context, cluster))
        return tasks

    def _gen_tasks_for_cluster(self, context, cluster):
        tasks = []

        if self._should_cluster_reboot(cluster):
            tasks.extend(
                self._gen_tasks_for_cluster_reboot(context, cluster))

        return tasks

    def _gen_tasks_for_cluster_reboot(self, context, cluster):
        tasks = []
        reboot_nodes = [node
            for node in cluster.nodes
            if not node.is_ms() and not node.is_initial()]

        msg = VolMgrUtils.format_list(
            [node.hostname for node in reboot_nodes], quotes_char="double"
        )

        if not context.is_snapshot_action_forced():
            tasks.append(
                CallbackTask(
                    cluster,
                    'Restart node(s) %s' % msg,
                    self.reboot_node_callback,
                    [node.hostname for node in reboot_nodes],
                    tag_name=restore_snapshot_tags.PEER_NODE_REBOOT_TAG,
            ))
            for node in reboot_nodes:
                tasks.append(
                    CallbackTask(
                        node.system if node.system else node,
                        'Wait for node "%s" to restart' % node.hostname,
                        self.wait_node_callback,
                        [node.hostname],
                        tag_name=(
                            restore_snapshot_tags.PEER_NODE_REBOOT_TAG),
                    ))
        else:
            tasks.append(
                CallbackTask(
                    cluster,
                    'Restart and wait for nodes',
                    self.force_reboot_wait_node_callback,
                    [node.hostname for node in reboot_nodes],
                    tag_name=restore_snapshot_tags.PEER_NODE_REBOOT_TAG,
            ))

        return tasks

    def _should_cluster_reboot(self, cluster):

        found_disk = False

        for vg in cluster.query('volume-group'):
            if vg.is_initial():
                continue
            if not vg.query('file-system'):
                continue
            the_system = VolMgrUtils.vg_system(vg)

            if the_system:
                for pd in vg.physical_devices:
                    # disk has sub-itemtypes in core, but as they should be
                    # treated equivalently for our purposes this query is okay
                    if the_system.query('disk', name=pd.device_name):
                        found_disk = True
                        break
            if found_disk:
                break

        return found_disk


class VolMgrPlugin(Plugin):
    """
    LITP Volume Manager plugin allows the creation and configuration of
    logical volumes providing increased flexibility over direct use of
    physical storage. The plugin also provides snapshotting functionality

    Update reconfiguration actions are supported for this plugin
    (with some exceptions - see the Validation section).
    """

    def __init__(self):
        '''
        Constructor to instantiate Drivers
        '''

        super(VolMgrPlugin, self).__init__()

        lvm_callbacks = {
            'base': self._base_rpc_task,
            'nodes_reachable_and_snaps_exist':
                       self._check_restore_nodes_reachable_and_snaps_exist_cb,
            'check_valid_snaps': self._check_valid_ss_rpc_task,
            'restore': self._restore_ss_rpc_task,
            'stop_service': self._stop_service,
            'future_uuid': self._task_with_future_uuid,

            'create_snapshot': self._create_snapshot_cb,
            'remove_snapshot': self._remove_snapshot_cb,
            'check_remove_nodes_reachable':
                      self._check_remove_nodes_reachable_cb,
            'check_restores_in_progress':
                self._check_lvm_restores_in_progress_cb,
            'ping_and_remove_snapshot': self._ping_and_remove_snapshot_cb,
            'ping_and_remove_grub': self._ping_and_remove_grub_cb,
            'remove_grub': self._remove_grub_cb,
            'restore_grub': self._base_rpc_task,
            'ping_and_restore_grub': self._ping_and_restore_grub_cb,
            'noop_grub_task': self._noop_grub_task_cb
        }

        vxvm_callbacks = {
            'base': self._base_rpc_task,
            'stop_service': self._stop_service,
            'base_with_imported_dg': self._base_with_imported_dg,
            'vx_disk_group_setup': self._vx_diskgroup_setup,
            'vx_init': self._vx_init_rpc_task,
            'vx_restore': self._wait_for_vxvm_restore_rpc,

            'check_active_nodes': self._check_active_nodes_cb,
            'check_presence': self._check_presence_cb,
            'check_validity': self._check_validity_cb,
            'check_restores_in_progress': self._check_restores_in_progress_cb,
            'restore_snapshot': self._restore_vxvm_snapshot_cb,
            'remove_snapshot': self._remove_vxvm_snapshot_cb,
            'create_snapshot': self._create_vxvm_snapshot_cb,
            'vxvm_uuid_updated': self._vxvm_disk_uuid_update_cb
        }

        self.lvm_driver = LvmDriver(lvm_callbacks)
        self.vxvm_driver = VxvmDriver(vxvm_callbacks)
        self.base_processor = RpcCommandProcessorBase()
        self.ic_processor = IgnoredCodesAgentProcessor()
        self.reboot_planner = SnapshotRebootPlanner(
            reboot_ms_callback=self._restart_node,
            reboot_node_callback=self._restart_node,
            wait_node_callback=self._wait_for_node_up,
            force_reboot_wait_node_callback=self._wait_for_nodes_up,
            warn_invalid_cluster_order=self._warn_invalid_cluster_order)

    @staticmethod
    def _is_cloud_disk(disk):
        if disk.uuid:
            return KGB == VolMgrUtils.get_canonical_uuid(disk)
        else:
            return False

    @staticmethod
    def _present_disks(node):
        return [disk for disk in VolMgrUtils.system_disks(node) \
                if not disk.is_for_removal()]

    @staticmethod
    def _validate_unique_fs_mountpoint(profile):
        '''
        Validate that a file-system mount point is unique
        for a given Storage Profile.
        '''

        preamble = '_validate_unique_fs_mountpoint: '

        errors = []

        for vg in profile.volume_groups:
            for fs in vg.file_systems:
                mounts = [fs1.mount_point for fs1 in
                            [fs2 for vg1 in profile.volume_groups
                                 for fs2 in vg1.file_systems]
                          if hasattr(fs1, 'mount_point') and fs1.mount_point \
                             and fs1 != fs]
                if hasattr(fs, 'mount_point') and fs.mount_point in mounts:
                    message = "File System mount_point is not " + \
                              "unique for this Storage profile"

                    log.trace.debug((preamble + "VG:%s FS:%s Error: %s") %
                                    (vg.item_id, fs.item_id, message))

                    errors.append(ValidationError(item_path=fs.get_vpath(),
                                                  error_message=message))

        return errors

    @staticmethod
    def _validate_unique_vg_name(profile):
        '''
        Validate that a volume group name is unique
        in a given storage profile
        '''

        preamble = '_validate_unique_vg_name: '

        errors = []

        for vg in profile.volume_groups:
            vg_names = [vg1.volume_group_name
                        for vg1 in profile.volume_groups
                        if vg1 != vg]
            if vg.volume_group_name in vg_names:
                msg = 'Property "volume_group_name" is not unique for ' + \
                      'this storage-profile.'

                log.trace.debug((preamble + "VG:%s Error: %s") %
                                (vg.item_id, msg))

                errors.append(ValidationError(item_path=vg.get_vpath(),
                                              error_message=msg))

        return errors

    @staticmethod
    def _validate_unique_disk_name(node):
        '''
        Validate that a system disk name is unique
        '''

        preamble = '_validate_unique_disk_name: %s: ' % node.item_id

        errors = []

        for disk in VolMgrUtils.system_disks(node):
            disk_names = [disk1.name for disk1 in
                          VolMgrUtils.system_disks(node)
                          if disk != disk1]
            if disk.name in disk_names:
                message = "System Disk name '%s' is not unique" % disk.name

                log.trace.debug(preamble + "Error: %s" % message)

                errors.append(ValidationError(item_path=disk.get_vpath(),
                                              error_message=message))

        return errors

    @staticmethod
    def _is_disk_not_for_update(node, pd, disk):

        preamble = '._is_disk_not_for_update: '
        should_update = False

        if not disk:
            msg = ("physical-device '%s' on node '%s' does "
                   "not reference a disk") % (pd.item_id, node.hostname)
            log.trace.debug(preamble + msg)
            should_update = True

        elif not disk.is_updated():
            should_update = True

        return should_update

    @staticmethod
    def _validate_ms_disk_properties(node):
        """
            LITPCDS-12928 allow update of disks in LVM storage profiles on
            management servers.
        """

        preamble = "._validate_ms_disk_properties: "
        msg = "validating disk properties on node '%s'" % node.hostname
        log.trace.debug(preamble + msg)
        errors = []

        for vg in node.storage_profile.volume_groups:
            for pd in vg.physical_devices:

                disk = VolMgrUtils.get_node_disk_for_pd(node, pd)

                if VolMgrPlugin._is_disk_not_for_update(node, pd, disk):
                    continue

                if VolMgrUtils._is_disk_size_decreased(disk):

                    msg = (
                        'Decreasing the "size" property of disk '
                        '"{device_name}" associated with LVM '
                        'storage-profile on management '
                        'server "{host}" is not supported'
                    ).format(
                        device_name=disk.name,
                        host=node.hostname
                    )

                    errors.append(
                        ValidationError(
                            disk.get_vpath(), error_message=msg
                        )
                    )

        return errors

    @staticmethod
    def _validate_mn_disk_properties(node):
        """
            LITPCDS-12928 prohibit update of disks in LVM storage profiles on
            managed nodes
        """

        preamble = "._validate_mn_disk_properties: "
        msg = "validating disk properties on node '%s'" % node.hostname
        log.trace.debug(preamble + msg)

        errors = []

        for vg in node.storage_profile.volume_groups:
            for pd in vg.physical_devices:

                disk = VolMgrUtils.get_node_disk_for_pd(node, pd)

                # Added for LITPCDS-13765
                if not disk or disk.item_type_id != 'disk':
                    continue

                if VolMgrPlugin._is_disk_not_for_update(node, pd, disk):
                    continue

                if VolMgrUtils._item_property_has_changed(disk, 'size'):

                    if VolMgrUtils.has_disk_size_decreased(disk):
                        msg = (
                            'Decreasing the "size" property of disk '
                            '"{device_name}" associated with LVM '
                            'storage-profile on peer node "{host}" '
                            'is not supported'
                            ).format(device_name=disk.name,
                                     host=node.hostname)

                        errors.append(
                            ValidationError(
                                disk.get_vpath(), error_message=msg)
                            )

                if VolMgrUtils._item_property_has_changed(disk, 'uuid'):

                    msg = (
                        'Updating the "uuid" property of disk "{device_name}" '
                        'associated with LVM storage-profile on peer node '
                        '"{host}" is not supported'
                    ).format(
                        device_name=disk.name,
                        host=node.hostname
                    )

                    errors.append(
                        ValidationError(
                            disk.get_vpath(), error_message=msg
                        )
                    )

        return errors

    @staticmethod
    def _validate_node_disk_properties(node):
        """
            Validate the properties of a disk, specifically the uuid and size
            properties. Update of these properties is allowed for disks on the
            MS but not MN disks.

            Introduced in LITPCDS-12928 that relaxes the restriction on
            updating uuid and size via REST.
        """

        preamble = '._validate_node_disk_properties: '
        msg = "validating disk properties for node '%s'" % node.hostname
        log.trace.debug(preamble + msg)

        if node.is_ms():
            return VolMgrPlugin._validate_ms_disk_properties(node)
        else:
            return VolMgrPlugin._validate_mn_disk_properties(node)

    @staticmethod
    def _validate_cluster_disk_properties(cluster):

        preamble = '._validate_cluster_disk_properties: '
        msg = "validating disk properties for cluster '%s'" % cluster.item_id
        log.trace.debug(preamble + msg)

        errors = []

        if not cluster.storage_profile:
            return errors

        storage_profiles = cluster.query(
            'storage-profile', volume_driver='vxvm')

        if not storage_profiles:
            return errors

        # See LITPCDS-10695, LITPCDS-10895 and LITPCDS-10909
        first_sp = storage_profiles[0]

        for vg in first_sp.volume_groups:

            for pd in vg.physical_devices:
                for node in cluster.nodes:

                    disk = VolMgrUtils.get_node_disk_for_pd(node, pd)

                    # Added for LITPCDS-13765
                    if not disk or disk.item_type_id != 'disk':
                        continue

                    if VolMgrPlugin._is_disk_not_for_update(node, pd, disk):
                        continue

                    if VolMgrUtils._is_disk_size_decreased(disk):
                        msg = (
                            'Decreasing the "size" property of disk '
                            '"{device_name}" associated with VxVM '
                            'storage-profile on peer node "{hosts}" '
                            'in cluster "{cluster}" is not supported'
                        ).format(
                            device_name=disk.name,
                            hosts=node.hostname,
                            cluster=cluster.item_id
                        )

                        errors.append(
                            ValidationError(
                                disk.get_vpath(), error_message=msg
                            )
                        )

                    if VolMgrUtils._item_property_has_changed(disk, 'uuid'):
                        msg = (
                            'Updating the "uuid" property of disk '
                            '"{device_name}" associated with VxVM '
                            'storage-profile on peer node "{hosts}" '
                            'in cluster "{cluster}" is not supported'
                        ).format(
                            device_name=disk.name,
                            hosts=node.hostname,
                            cluster=cluster.item_id
                        )

                        errors.append(
                            ValidationError(
                                disk.get_vpath(), error_message=msg
                            )
                        )

        return errors

    @staticmethod
    def _validate_unique_pd_name(cluster):
        '''
        Validate that a physical device name is unique
        in a given storage profile
        '''
        preamble = '_validate_unique_pd_name: '

        errors = []
        vxvm_storage_profiles = cluster.query("storage-profile",
                                               volume_driver='vxvm')
        for sp in vxvm_storage_profiles:
            for vg in sp.volume_groups:
                vg_pds = VolMgrUtils.vg_pds(vg)
                device_names = [pd.device_name
                                for pd in vg_pds]
                for pd in vg_pds:
                    if device_names.count(pd.device_name) > 1:
                        msg = "Physical device name is not " + \
                              "unique for the VxVM {0} profile"
                        msg = msg.format(sp.item_id)
                        log.trace.debug((preamble + "PD:%s Error: %s") %
                                        (pd.item_id, msg))
                        errors.append(ValidationError(item_path=pd.get_vpath(),
                                                      error_message=msg))

        return errors

    def _validate_unique_disk_uuid(self, node):
        '''
        Validate that a system disk uuid is unique
        '''

        preamble = '_validate_unique_disk_uuid: %s: ' % node.item_id

        errors = []

        non_cloud_disks = [disk for disk in VolMgrPlugin._present_disks(node) \
                           if not VolMgrPlugin._is_cloud_disk(disk)]

        cloud_disks = [disk for disk in VolMgrPlugin._present_disks(node) \
                       if VolMgrPlugin._is_cloud_disk(disk)]

        # XXX(xigomil) This enables KGB cloud VMs to
        # not look for uuid values other than special KGB value
        # else respect uuid uniqueness requirement
        if len(cloud_disks) > 0:
            for disk in self._present_disks(node):
                if not VolMgrPlugin._is_cloud_disk(disk):
                    message = ("System Disk uuid '{0}' must be the same on "
                               "all cloud disks and must be '{1}'."
                               .format(disk.uuid, KGB))

                    log.trace.debug(preamble + "Error: %s" % message)

                    errors.append(ValidationError(item_path=disk.get_vpath(),
                                                  error_message=message))

        else:  # len(cloud_disks) == 0
            # No cloud disks on this system, make sure each uuid is unique
            for disk in non_cloud_disks:
                if disk.item_type_id == 'disk':
                    disk_uuids = [VolMgrUtils.get_canonical_uuid(disk1)
                                  for disk1 in non_cloud_disks
                                  if disk != disk1 and
                                     disk1.item_type_id == 'disk']
                    if VolMgrUtils.get_canonical_uuid(disk) in disk_uuids:
                        message = ("System Disk uuid '%s' is not unique" %
                                   disk.uuid)

                        log.trace.debug(preamble + "Error: %s" % message)

                        error = ValidationError(item_path=disk.get_vpath(),
                                                error_message=message)
                        errors.append(error)
        return errors

    @staticmethod
    def _validate_bootable_disk(node):
        '''
        Validate that only 1 system disk has "bootable" set to 'true'
        '''

        preamble = '_validate_bootable_disk: %s : ' % node.item_id

        errors = []

        num_boot_disks = len([disk for disk in \
                              VolMgrUtils.system_disks(node) \
                              if disk.bootable == "true"])

        msg = None

        if (node.item_type_id == 'node') and (num_boot_disks != 1):
            msg = "Exactly one System Disk should have 'bootable' " + \
                  "Property set to 'true'"
        elif node.is_ms() and (num_boot_disks > 1):
            msg = "At most one system disk may have the 'bootable' " + \
                  "property set to 'true'"

        if msg:
            log.trace.debug(preamble + msg)
            error = ValidationError(item_path=node.system.get_vpath(),
                                    error_message=msg)
            errors.append(error)

        return errors

    @staticmethod
    def _validate_disk_exists(node):
        '''
        Validate that the physical device exists as a system disk
        '''

        preamble = '_validate_disk_exists: %s : ' % node.item_id

        errors = []
        for vg in node.storage_profile.volume_groups:

            for pd in vg.physical_devices:

                system_disk_found = False

                for disk in VolMgrUtils.system_disks(node):
                    if disk.name and (disk.name == pd.device_name):
                        system_disk_found = True
                        break

                if not system_disk_found:
                    message = ("Failed to find System disk '%s' " +
                               "for Node '%s'") % \
                              (pd.device_name, node.hostname)
                    log.trace.debug((preamble + "VG:%s PD:%s " + message) %
                                    (vg.item_id, pd.item_id))
                    error = ValidationError(item_path=pd.get_vpath(),
                                            error_message=message)
                    errors.append(error)
        return errors

    @staticmethod
    def _validate_node_vg_uniform_disk_types(node):
        """
        Validate that all disks associated with a volume group
        on a node are of uniform type
        """
        errors = []
        node_vgs = [vg for vg in node.storage_profile.volume_groups
                    if not vg.is_for_removal()]
        for vg in node_vgs:
            errors += VolMgrPlugin._validate_uniform_disk_types(vg, node)
        return errors

    @staticmethod
    def _validate_cluster_vg_uniform_disk_types(cluster):
        """
        Validate that all disks associated with a volume group
        on a cluster are of uniform type
        """
        errors = []
        storage_profiles = [sp for sp in cluster.query("storage-profile",
                                                       volume_driver="vxvm")
                            if not sp.is_for_removal()]
        if not storage_profiles:
            return errors
        sp = storage_profiles[0]
        clus_vgs = [vg for vg in sp.volume_groups
                    if not vg.is_for_removal()]
        for vg in clus_vgs:
            for node in cluster.nodes:
                errors += VolMgrPlugin._validate_uniform_disk_types(vg, node)
        return errors

    @staticmethod
    def _validate_uniform_disk_types(vg, node):
        preamble = '_validate_uniform_disk_types: {0}'.format(node.hostname)
        errors = []
        vg_disks = VolMgrUtils.get_disks_for_pds(vg.physical_devices, node)
        disk_types = set([disk.item_type_id for disk in vg_disks])

        if len(disk_types) > 1:

            err_msg = 'Disk is of type "{0}". All disks associated '\
                'with volume group "{1}" on node "{2}" must be of '\
                'identical item type'

            for disk in vg_disks:
                msg = err_msg.format(disk.item_type_id,
                                     vg.volume_group_name,
                                     node.hostname)
                log.trace.debug(preamble + msg)
                error = ValidationError(item_path=disk.get_vpath(),
                                    error_message=msg)
                errors.append(error)
        return errors

    @staticmethod
    def _validate_pd_disks(node):
        """
        Validate that each system disk is referenced by
        at most 1 physical device.
        """
        preamble = '_validate_pd_disks: %s : ' % node.item_id

        errors = []
        all_pds = [pd for vg in node.storage_profile.volume_groups
                   for pd in vg.physical_devices]
        for disk in VolMgrUtils.system_disks(node):
            pd_refs = [pd for pd in all_pds if pd.device_name == disk.name]

            if len(pd_refs) > 1:
                msg = "Disk '%s' referenced by multiple Physical Devices" % \
                      disk.name
                log.trace.debug(preamble + msg)

                error = ValidationError(item_path=disk.get_vpath(),
                                        error_message=msg)
                errors.append(error)

        return errors

    @staticmethod
    def _validate_non_root_vg_disks(node):
        '''
        Validate that non vg-root Volume groups will not
        be assigned to a Bootable System disk
        '''

        preamble = '_validate_non_root_vg_disks: %s : ' % node.item_id

        errors = []

        the_root_vg = VolMgrUtils.get_node_root_vg_name(node)

        if not the_root_vg:
            return errors

        for vg in node.storage_profile.volume_groups:
            if vg.volume_group_name != the_root_vg:
                for pd in vg.physical_devices:
                    for disk in VolMgrUtils.system_disks(node):
                        if disk.name and (disk.name == pd.device_name):
                            if disk.bootable == 'true':
                                msg = ("Non vg-root '%s' is configured to be" +
                                       " created on system disk '%s' which " +
                                       "is bootable. This is invalid.") % \
                                       (vg.item_id, disk.name)
                                error = ValidationError(
                                                      item_path=vg.get_vpath(),
                                                      error_message=msg)
                                log.trace.debug(preamble + msg)
                                errors.append(error)
                            break

        return errors

    @staticmethod
    def _validate_root_vg_boot_disk(node):
        '''
        Validate that the vg-root will be assigned to
        a bootable system disk
        '''

        preamble = '_validate_root_vg_boot_disk: %s : ' % node.item_id

        errors = []

        sys_disks = VolMgrUtils.system_disks(node)
        if not sys_disks:
            return errors

        the_root_vg = VolMgrUtils.get_node_root_vg_name(node)
        if not the_root_vg:
            return errors

        for vg in node.storage_profile.volume_groups:
            if vg.volume_group_name == the_root_vg:
                vg_bootable_disks = []
                for pd in vg.physical_devices:
                    for disk in sys_disks:
                        if disk.name and (disk.name == pd.device_name):
                            if disk.bootable == 'true':
                                vg_bootable_disks.append(disk)

                if len(vg_bootable_disks) != 1:
                    msg = ("For node '%s' the vg-root must be created " +
                           "on disks that include one bootable disk") % \
                           node.hostname
                    error = ValidationError( \
                                        item_path=vg.get_vpath(),
                                        error_message=msg)
                    log.trace.debug(preamble + msg)
                    errors.append(error)
                break

        return errors

    @staticmethod
    def _validate_one_root_mount_point(profile):
        '''
        Validates that vg-root has root mount point ('/').
        '''

        preamble = '_validate_one_root_mount_point: profile %s : ' % \
                   profile.item_id

        errors = []

        try:
            profile.view_root_vg
        except ViewError as e:
            log.trace.debug(preamble + str(e))
            errors.append(ValidationError(item_path=profile.get_vpath(),
                                          error_message=str(e)))

        return errors

    @staticmethod
    def _validate_unique_dg_name(cluster):
        '''
        Validate that a disk group name is unique
        in a given storage profile
        '''

        preamble = '_validate_unique_dg_name: '

        errors = []
        dg_names = []

        for profile in cluster.query("storage-profile",
                                     volume_driver='vxvm'):

            dg_names += [(dg.get_vpath(), dg.volume_group_name)
                         for dg in profile.volume_groups]

        seen = set()
        uniq_ids = [item for item in dg_names if item[1] not in seen
                      and not seen.add(item[1])]
        duplicated_ids = [item for item in dg_names if item
                            not in uniq_ids]

        if duplicated_ids:
            for name in duplicated_ids:
                message = (
                    'VxVM volume_group_name "%s" is not '
                    'unique for cluster "%s"' %
                    (name[1], cluster.item_id)
                )
                log.trace.debug((preamble + "DG:%s Error: %s") %
                                (name[1], message))

                errors.append(
                    ValidationError(
                        item_path=name[0],
                        error_message=message
                    )
                )

        return errors

    @staticmethod
    def _validate_vxvm_fs_type(vxvm_profiles):

        errors = []

        for profile in vxvm_profiles:
            for vg in profile.volume_groups:
                for fs in vg.file_systems:
                    if fs.type != 'vxfs':
                        msg = 'The "volume_driver" property of the ' + \
                              'storage-profile has a value "vxvm"; the ' + \
                              '"type" property on all file-systems must ' + \
                              'have a value of "vxfs".'
                        error = ValidationError(item_path=fs.get_vpath(),
                                                error_message=msg)
                        log.trace.debug(msg)
                        errors.append(error)

        return errors

    @staticmethod
    def _validate_vxfs_fs_name(vxvm_profiles):

        errors = []

        max_len = MAX_VXVM_VOL_LENGTH - LITP_EXTRA_CHARS - TAG_RESERVED_LENGTH

        for profile in vxvm_profiles:
            for vg in profile.volume_groups:
                for fs in vg.file_systems:
                    if len(fs.item_id) > max_len:
                        msg = ('Filesystem "%s" exceeds %s characters ' \
                               'maximum length.') % (fs.item_id, max_len)
                        error = ValidationError(item_path=fs.get_vpath(),
                                                error_message=msg)
                        log.trace.debug(msg)
                        errors.append(error)

        return errors

    @staticmethod
    def _get_vxfs_tag_max_length(vxvm_profiles):

        max_len = 0
        for profile in vxvm_profiles:
            for vg in profile.volume_groups:
                for fs in vg.file_systems:
                    if len(fs.item_id) > max_len:
                        max_len = len(fs.item_id)

        return max(MAX_VXVM_VOL_LENGTH - LITP_EXTRA_CHARS - max_len, 0)

    @staticmethod
    def _get_lvmfs_tag_max_length(lvm_profiles):

        # LITPCDS-12687: also consider un-modeled kickstart lvm volumes when
        # generating snapshot names that have a tag
        max_len = VolMgrUtils.get_ks_fs_max_length()

        for profile in lvm_profiles:
            for vg in profile.volume_groups:

                # consider file-systems of type 'ext4' and xfs only
                lvm_fss = [fs for fs in vg.file_systems
                           if fs.type in LVM_FS_TYPES_NON_SWAP]

                for fs in lvm_fss:
                    fs_name = '{0}/{1}'.format(vg.volume_group_name,
                                LvmDriver.generate_LV_name(vg, fs))
                    if len(fs_name) > max_len:
                        max_len = len(fs_name) + fs_name.count('-')

        return max(MAX_LVM_SNAP_VOL_LENGTH - LITP_EXTRA_CHARS - max_len, 0)

    def _should_use_vxvm_for_tag_length(self, vxvm_profiles):
        for profile in vxvm_profiles:
            for vg in profile.volume_groups:
                for fs in vg.file_systems:
                    if fs.current_snap_size != '0':
                        return True
        return False

    def _validate_snap_tag_length(self, vxvm_profiles, lvm_profiles, context):
        errors = []

        vxvm_max_tag = VolMgrPlugin._get_vxfs_tag_max_length(vxvm_profiles)
        lvm_max_tag = VolMgrPlugin._get_lvmfs_tag_max_length(lvm_profiles)
        tag_length = 0
        name_tag = self.lvm_driver.get_snapshot_tag(context)

        if vxvm_profiles and \
            self._should_use_vxvm_for_tag_length(vxvm_profiles):
            tag_available = min(vxvm_max_tag, lvm_max_tag)
        else:
            tag_available = lvm_max_tag

        if tag_available == vxvm_max_tag:
            fs_type_msg = "a VxFS"
            tag_length = len(name_tag)
        else:
            fs_type_msg = 'an ' + ' or '.join(LVM_FS_TYPES_NON_SWAP)
            tag_length = len(name_tag) + name_tag.count('-')

        if name_tag and tag_length > tag_available:

            msg = "Snapshot name tag cannot exceed %s characters "\
                  "which is the maximum available length "\
                  "for %s file system." % \
                  (tag_available, fs_type_msg)
            error = ValidationError(error_message=msg)
            log.trace.debug(msg)
            errors.append(error)

        return errors

    @staticmethod
    def _validate_lvm_fs_type(lvm_profiles):

        errors = []

        for profile in lvm_profiles:
            for vg in profile.volume_groups:
                for fs in vg.file_systems:
                    if fs.type not in LVM_FS_TYPES:
                        msg = 'The "volume_driver" property of the ' + \
                              'storage-profile has a value "lvm"; the ' + \
                              '"type" property on all file-systems must ' + \
                              'have a value of "ext4", "xfs" or "swap".'

                        error = ValidationError(item_path=fs.get_vpath(),
                                                error_message=msg)
                        log.trace.debug(msg)
                        errors.append(error)

        return errors

    def _validate_node_profile_is_lvm(self, node):

        errors = []
        if node.storage_profile.volume_driver != 'lvm':
            msg = "A storage_profile at node level must " + \
                  "have its volume_driver property set to 'lvm'"
            log.trace.debug(msg)
            errors.append(ValidationError(
                            item_path=node.storage_profile.get_vpath(),
                            error_message=msg))

        return errors

    @staticmethod
    def _validate_cluster_type_sfha(cluster):

        errors = []

        vxvm_profiles = cluster.query("storage-profile",
                                      volume_driver='vxvm')
        if vxvm_profiles and str(cluster.cluster_type) != 'sfha':
            msg = "A cluster with vxvm storage profile must be of "\
                  "type 'sfha'"
            log.trace.debug(msg)
            errors.append(ValidationError(item_path=cluster.get_vpath(),
                                          error_message=msg))
        return errors

    def _validate_disk_in_cluster(self, cluster):

        errors = []
        for sp in cluster.storage_profile:
            for vg in sp.volume_groups:
                for pd in vg.physical_devices:
                    errors += self._validate_disk_exist(cluster, pd)
        return errors

    def _validate_disk_exist(self, cluster, pd):
        """
        Validate that every node in cluster must have same VxVM
        disk
        """

        preamble = '_validate_disk_exist: %s: ' % cluster.item_id

        log.trace.debug(preamble)

        errors = []
        found_disks = []
        found_nodes = []

        cluster_nodes = [node for node in cluster.nodes
                         if not node.is_for_removal()]

        for node in cluster_nodes:
            for disk in VolMgrUtils.system_disks(node):
                if disk.name == pd.device_name:
                    found_disks.append(disk)
                    found_nodes.append(node)
                    break

        infra_item = pd.get_source()

        node_diff = [node for node in cluster_nodes
                     if node not in found_nodes]

        for node in node_diff:
            errors.append(
                VolMgrPlugin._get_error_disk_non_exist(infra_item, node)
            )

        if not VolMgrPlugin._validate_all_same_disks(found_disks):
            param_errors = VolMgrPlugin._get_error_parameter_not_same(
                found_disks,
                cluster)
            # We only validate properties we know of, so we ignore other
            # errors since they will likely be caught in the external plugin
            if param_errors:
                errors.extend(param_errors)

        return errors

    @staticmethod
    def _validate_ms_ks_fs_mount_point_not_changed(profile):

        errors = []

        ks_fs_mnts = [ks_fs.mount_point for ks_fs
                      in VolMgrUtils.get_kickstart_fss()]

        vgs = [vg for vg in profile.volume_groups
               if not vg.is_for_removal()]

        for vg in vgs:
            fss = [fs for fs in vg.file_systems
                   if not fs.is_for_removal()]

            for fs in fss:
                fs_mnt = fs.applied_properties.get('mount_point')
                if fs_mnt in ks_fs_mnts and \
                   VolMgrUtils._mount_point_has_changed(fs):
                    msg = 'A "mount_point" cannot be updated for ' \
                          'a Kickstarted "file-system"'
                    error = ValidationError(item_path=fs.get_vpath(),
                                            error_message=msg)
                    errors.append(error)
        return errors

    @staticmethod
    def _validate_fs_is_updatable(profile):
        errors = []
        preamble = "_validate_fs_is_updatable"

        vgs = [vg for vg in profile.volume_groups
               if not vg.is_for_removal()
               and profile.volume_driver != 'vxvm']

        for vg in vgs:
            fss = [fs for fs in vg.file_systems
                   if not fs.is_for_removal()]

            for fs in fss:
                if fs.type not in LVM_FS_TYPES_NON_SWAP and \
                   VolMgrUtils._item_property_has_changed(fs, 'mount_point'):

                    message = 'A "mount_point" cannot be updated for a ' \
                              '"file-system" of "type" ' \
                              "'{0}'".format(fs.type)

                    log.trace.debug((preamble +
                         "VG:{0} FS:{1} Error: {2}").format(vg.item_id,
                                                            fs.item_id,
                                                            message))
                    error = ValidationError(item_path=fs.get_vpath(),
                                            error_message=message)
                    errors.append(error)

        return errors

    @staticmethod
    def _get_error_disk_non_exist(infra_item, node):

        msg = "VxVM disk '%s' does not exist on node '%s'" % \
              (infra_item.device_name, node.hostname)

        return ValidationError(item_path=infra_item.get_vpath(),
                               error_message=msg)

    @staticmethod
    def _format_error_disk_property_not_same(disks, cluster, property_name):
        errors = []
        for disk in disks:
            msg = ('Property "%s" with value "%s" on VxVM disk must be '
                   'the same on every node in cluster "%s"') % \
                   (property_name, getattr(disk, property_name),
                      cluster.item_id)
            errors.append(ValidationError(item_path=disk.get_vpath(),
                                          error_message=msg))
        return errors

    @staticmethod
    def _get_error_parameter_not_same(disks, cluster):

        errors = []

        applied_uuids = [disk.uuid for disk in disks if not disk.is_initial()]
        initial_uuids = [disk.uuid for disk in disks if disk.is_initial() \
                          and disk.uuid]
        uuids = set(applied_uuids + initial_uuids)
        sizes = set([disk.size for disk in disks])
        names = set([disk.name for disk in disks])
        bootable = set([disk.bootable for disk in disks])

        if len(uuids) > 1:
            errors.extend(
                VolMgrPlugin._format_error_disk_property_not_same(disks,
                                                                  cluster,
                                                                  "uuid"))
        if len(sizes) > 1:
            errors.extend(
                VolMgrPlugin._format_error_disk_property_not_same(disks,
                                                                  cluster,
                                                                  "size"))
        if len(names) > 1:
            errors.extend(
                VolMgrPlugin._format_error_disk_property_not_same(disks,
                                                                  cluster,
                                                                  "name"))
        if len(bootable) > 1:
            errors.extend(
                VolMgrPlugin._format_error_disk_property_not_same(disks,
                                                                  cluster,
                                                                  "bootable"))
        return errors

    @staticmethod
    def _validate_all_same_disks(disks):
        """
        Checks that every disk in node are the same
        """
        disks = iter([disk.properties for disk in disks])
        first = next(disks, None)
        return all(disk == first for disk in disks)

    def validate_model_snapshot(self, plugin_api_context):
        '''
        Validates snapshot actions passed to this plugin.
        Validation rules used in this method are:

        - A ``create`` action requires that:

            - The length of the snapshot tag is valid.
            - No updates to the ``size`` property of any \
              ``file-system`` items are pending.
        '''

        errors = []

        try:
            action = plugin_api_context.snapshot_action()
        except NoSnapshotItemError:
            action = 'unknown'

        if 'create' == action:
            errors = self._validate_model_create_snapshot(plugin_api_context)

        return errors

    def _validate_model_create_snapshot(self, context):

        errors = []

        infras = context.query("infrastructure")

        if infras:
            vxvm_profiles = []
            lvm_profiles = []

            for profile in infras[0].storage.storage_profiles:
                if profile.volume_driver == "vxvm":
                    vxvm_profiles.append(profile)
                elif profile.volume_driver == "lvm":
                    lvm_profiles.append(profile)

            if self.lvm_driver.get_snapshot_tag(context):
                errors += self._validate_snap_tag_length(vxvm_profiles,
                                                         lvm_profiles,
                                                         context)

        errors += self._validate_no_fs_size_updates(context)

        nodes = VolMgrPlugin._get_nodes(context)
        mses = VolMgrPlugin._get_mses(context)

        all_nodes = nodes + mses

        for node in all_nodes:
            if node.system and node.storage_profile:
                errors += self.lvm_driver.validate_node_snapshot(node)

        return errors

    def _validate_no_fs_size_updates(self, context):

        errors = []

        # LITPCDS-9496
        profiles = VolMgrPlugin._get_all_referenced_storage_profiles(context)

        for profile in profiles:

            volume_groups = [vg for vg in profile.volume_groups \
                             if not vg.is_for_removal()]

            for vg in volume_groups:

                for fs in vg.file_systems:
                    if VolMgrUtils._item_property_has_changed(fs, 'size'):
                        message = 'A snapshot may not be created while ' \
                                  'a file system size update is pending.'
                        errors.append(ValidationError(item_path=fs.get_vpath(),
                                                      error_message=message))

        return errors

    @staticmethod
    def _validate_fs_mount_point_not_reused(profile):

        errors = []
        preamble = "._validate_fs_mount_points_not_reused: "

        fss = []
        for vg in profile.volume_groups:
            fss.extend([fs for fs in vg.file_systems if
                            fs.is_initial() or
                            VolMgrUtils._mount_point_has_changed(fs)])

        for fs in fss:
            for fs2 in fss:

                if 'mount_point' not in fs.applied_properties.keys():
                    continue

                if fs2 != fs and fs2.mount_point == \
                                 fs.applied_properties['mount_point']:

                    msg = 'Cannot change mount point "%s" from file '\
                          'system "%s" to "%s".' % \
                          (fs.applied_properties['mount_point'],
                           fs.item_id, fs2.item_id)

                    log.trace.debug(preamble + msg)
                    error = ValidationError(item_path=fs.get_vpath(),
                                            error_message=msg)
                    errors.append(error)

        return errors

    @staticmethod
    def _get_nodes(context):
        return [node for node in context.query("node")
                if not node.is_for_removal()]

    @staticmethod
    def _get_del_snap_nodes(context, snapshot_model):
        '''
        Get the intersection between nodes in the context and
        nodes in the snapshot_model
        '''
        return VolMgrUtils.get_intersection_items_by_itemtype(
            snapshot_model, context, 'node')

    @staticmethod
    def _get_mses(context):
        return [ms for ms in context.query("ms")
                if not ms.is_for_removal()]

    @staticmethod
    def _get_clusters(context):
        return [cluster \
                for cluster in context.query("vcs-cluster")
                if not cluster.is_for_removal()]

    @staticmethod
    def _get_all_referenced_storage_profiles(context):
        (node_profiles, cluster_profiles, ms_profiles) = \
                        VolMgrPlugin._get_referenced_storage_profiles(context)
        return node_profiles + cluster_profiles + ms_profiles

    @staticmethod
    def _get_referenced_storage_profiles(context):

        nodes = VolMgrPlugin._get_nodes(context)

        node_profiles = [node.storage_profile for node in nodes
                         if node.storage_profile]

        mses = VolMgrPlugin._get_mses(context)

        ms_profiles = [ms.storage_profile for ms in mses
                       if ms.storage_profile]

        cluster_profiles = [
            sp for sp in
            VolMgrPlugin._get_all_cluster_profiles(context)
            if not sp.is_for_removal()]

        return (node_profiles, cluster_profiles, ms_profiles)

    @staticmethod
    def _get_all_cluster_profiles(context):
        clusters = VolMgrPlugin._get_clusters(context)
        return [sp for cluster in clusters
                for sp in cluster.storage_profile if sp]

    def set_default_mount_options(self, fs):
        """
        Utility method to set default value for mount_options prop
        :param fs: file system
        :type fs: file-system Item type
        """
        if (fs.is_initial() or fs.is_updated()) \
                and hasattr(fs, 'mount_options') and \
                fs.mount_options == None \
                and hasattr(fs, 'mount_point') and fs.mount_point and \
                fs.mount_point != ROOT_FS_MOUNT_POINT and \
                fs.mount_point != 'swap':
            fs.mount_options = LVM_FS_DEFAULT_MOUNT_OPTIONS

    def update_model(self, plugin_api_context):
        """
        Make any plugin specific updates to model items, before validation and
        create_configuration.
        :param plugin_api_context: access to the model manager
        """
        infras = plugin_api_context.query("infrastructure")

        for profile in infras[0].storage.storage_profiles:
            if profile.volume_driver == "lvm":
                for vg in profile.volume_groups:
                    for fs in vg.file_systems:
                        self.set_default_mount_options(fs)

    def validate_model(self, plugin_api_context):
        """
        Validates ``storage-profile`` model integrity. Validation rules used in
        this method are:

        - A ``storage-profile`` inherited to a peer ``node`` must have \
          its ``volume_driver`` property set to 'lvm'

        - A ``cluster``, which has a ``storage-profile`` with a property \
          ``volume_driver`` set to 'vxvm', must have a ``cluster_type`` \
          property set to 'sfha'

        - A ``file-system`` inherited to a ``lsb-runtime`` item must be \
          of ``type`` 'vxfs' and be within a ``storage-profile`` with \
          ``volume_driver`` property set to 'vxvm'

        - A ``file-system`` within a ``storage-profile`` with \
          ``volume_driver`` property set to 'lvm' must not be of \
          ``type`` 'vxfs'

        - A ``file-system`` ``mount_point`` property must be unique within a \
          ``storage-profile``

        - The ``name`` property of a ``disk`` must be unique \
          within a ``system``

        - The ``uuid`` property of a ``disk`` must be unique \
          within a ``system``

        - For peer nodes a ``file-system`` with a ``type`` property of 'swap' \
          must also have its ``mount_point`` property set to 'swap'

        - Each ``physical-device`` must have a ``device_name`` property which \
          uniquely matches the name of one ``disk`` in the ``system`` \
          ``disks`` collection

        - The removal of an active ``physical-device`` is not permitted if \
          that device is defined within a ``storage profile`` inherited to \
          a ``node``, ``ms`` or ``vcs-cluster``

        - A ``physical-device`` must be unique across all ``storage-profile`` \
          items for a given ``vcs-cluster``

        - The ``uuid`` of disks used in conjunction with a \
          ``storage-profile`` with the ``volume_driver`` property set to \
          'lvm' must be globally unique

        - Disks used in conjunction with a ``storage-profile`` with the \
          ``volume_driver`` property set to ``vxvm`` must be present on each \
          node on the ``vcs-cluster`` and have identical properties

        - The disks associated with a ``volume-group`` must be of identical \
          item type.

        - The disks associated with a ``volume-group`` must be large enough \
          to accommodate the sum total of all the ``file-systems`` in the \
          group plus an additional snapshot of the same size for each \
          ``file-system``

        - A ``volume-group`` is 'vg-root' if either of the following \
          conditions is true:

          - It is modeled on a peer server and has a ``file-system`` mounted \
            to '/'

          - It is modeled on the management server and the value of its \
            ``volume_group_name`` property is set to 'vg_root'

        - A non 'vg-root' ``volume-group`` must be created on a ``disk`` with \
          the ``bootable`` property set to 'false' or on a collection of \
          disks where all of the disks have the ``bootable`` property \
          set to 'false'

        - The 'vg-root' ``volume-group`` must be created on a ``disk`` with \
          the ``bootable`` property set to 'true' or on a collection of disks \
          where only one of the disks has the ``bootable`` property set \
          to 'true'

        - The 'vg-root' ``volume-group`` may contain many ``file-systems``; \
          on a peer ``node`` one of those 'vg-root' ``file-system`` items \
          must have a ``mount_point`` property set to slash ('/')

        - The ``size`` property of a ``disk`` may not be decreased but may
          be increased where the ``disk`` is modeled on the ``ms`` or
          attached to a VxVM storage profile

        - The ``uuid`` of a ``disk`` can be updated only on the ``ms``
          where the ``uuid`` specified matches the ``uuid`` of the underlying
          device

        - The ``uuid`` and ``size`` of a ``disk`` cannot be updated on peer
          nodes

        - Only one ``disk`` across all disks defined on a ``system`` should \
          have a ``bootable`` property set to 'true'. Once the ``system`` is \
          installed this property cannot be changed

        - Only a ``file-system`` of ``type`` 'ext4', 'xfs' or 'vxfs' may be \
          increased in size. It may not be decreased. The cumulative \
          size of the ``system`` disks attached to the file-system's parent \
          ``volume-group`` must be large enough to accommodate the increased \
          ``file-system`` size

        - Only a ``file-system`` of ``type`` 'ext4' or 'xfs' can omit \
          the ``mount_point`` property

        - The ``size`` property value of a ``file-system`` of type \
          'ext4' must be an exact multiple of the LVM logical extent \
          size ('4 MB')

        - The ``size`` property value of a ``file-system`` of type \
          'xfs' must be a minimum of size ('16 MB')

        - The ``volume_group_name`` property of a ``volume-group`` must be:

          - unique within a ``storage-profile``
          - cannot begin with ``'-'``
          - if it is the 'vg-root' ``volume-group``:
              - it cannot exceed 50 characters
              - it cannot begin with ``'_'``

        - Logical Volume names, derived from ``volume-group`` ``item_id`` and \
          ``file-system`` ``item_id``, have the following constraints:

          - They must contain only the following characters: \
            ``a-z A-z 0-9 + _ . -``.
          - They cannot be ``'.'``, ``'..'``, ``'snapshot'`` or ``'pvmove'``
          - They cannot contain any of the following strings: \
            ``'_mlog'``, ``'_mimage'``, ``'_rimage'``, ``'_tdata'``, \
            ``'_tmeta'``
          - If they are in the 'vg-root' ``volume-group``:
              - they cannot contain ``'-'``
              - they cannot exceed 50 characters

        - Logical Volume File System device names, derived from \
          ``volume-group`` ``item_id``, ``volume-group`` name and \
          ``file-system`` ``item_id``  have the following constraints:

          - If they are not in the 'vg-root' ``volume group``:
              - They cannot exceed 118 characters

        - The VxVM ``file-system`` name must be a maximum of 11 characters \
          in length. The ``file-system`` name is the ``item_id`` of \
          the ``file-system``

        - If a ``storage-profile`` is inherited to a management server, \
          then a ``system`` must also be inherited to the management server

        - A ``storage-profile`` inherited to a management server:
            - must have its ``volume_driver`` property set to 'lvm'

            - if there is a 'vg-root' ``volume-group``:
                - it may only span a single ``physical-device``
                - new ``physical-device`` items may not be added
                - the ``physical-device`` ``disk`` must have the \
                  ``bootable`` property set to 'true'
                - the bootable ``disk`` ``uuid`` property value must \
                  exactly match the uuid used at installation
                - it may contain ``file-systems`` created by kickstart
                - these kickstarted ``file-systems``:
                    - are identified by their ``mount_point`` property which \
                      must match the mount point of the original kickstarted \
                      file system
                    - have a ``size`` property greater than or equal to but \
                      not less than the size of the original kickstarted \
                      file system
                    - have a ``snap_size`` property greater than or equal to \
                      0 and less than or equal to 100
                    - cannot be removed once they are applied
                    - cannot have their ``mount_point`` property removed once \
                      they are applied
                    - cannot be added or re-sized if a deployment or named \
                      backup snapshot exists

            - for a non 'vg-root' ``volume-group``:
                - the ``physical-device`` ``disks`` must have the \
                  ``bootable`` property set to 'false'
                - the ``uuid`` property value of each ``physical-device`` \
                  ``disk`` item must not match the ``uuid`` of the ``disk`` \
                  used at installation

        - The ``uuid`` of a ``disk`` is updateable only on the ``ms``
          where ``uuid`` specified matches the ``uuid`` of the underlying
          device

        - The ``uuid`` and ``size`` of a ``disk`` are not updateable on peer
          nodes or where the ``disk`` will be used by a VxVM storage profiles

        - A snapshot exists when a VxVM ``volume-group`` is
          marked ``ForRemoval``
        """

        node_profiles, cluster_profiles, ms_profiles = \
            self._get_referenced_storage_profiles(plugin_api_context)

        errors = []
        # The MN only validators
        for profile in node_profiles:
            errors += VolMgrPlugin._validate_one_root_mount_point(profile)

        # The MS only validators
        for ms_profile in ms_profiles:
            errors += VolMgrPlugin._validate_ms_ks_fs_size_update(ms_profile)
            errors += VolMgrPlugin._validate_ms_lvm_profile(ms_profile)
            errors += VolMgrPlugin._validate_ms_profile_no_kickstart_clash(
                                                                  ms_profile)
            errors += VolMgrPlugin._validate_ms_veto_removal_of_ks_fs(
                                                                  ms_profile)
            errors += VolMgrPlugin._validate_ms_ks_fs_mount_point_not_changed(
                                                                  ms_profile)
            errors += VolMgrPlugin._validate_ms_ks_fs_snapshot_not_present(
                                                                  ms_profile,
                                                         plugin_api_context)

        # The common validators
        for profile in node_profiles + ms_profiles:
            errors += VolMgrPlugin._validate_unique_vg_name(profile)
            errors += VolMgrPlugin._validate_unique_fs_mountpoint(profile)
            errors += VolMgrPlugin.\
                      _validate_fs_mount_point_not_reused(profile)
            errors += VolMgrPlugin._validate_veto_removal_of_pd(profile)

        for profile in node_profiles + ms_profiles + cluster_profiles:
            errors += VolMgrPlugin._validate_fs_size_update(profile,
                                                            plugin_api_context)
            errors += VolMgrPlugin._validate_fs_is_updatable(profile)

        all_cluster_profiles = self._get_all_cluster_profiles(
                plugin_api_context)
        for profile in all_cluster_profiles:
            if profile.volume_driver == "vxvm":
                errors += self.vxvm_driver.validate_for_removal_vg(profile,
                    plugin_api_context)

        nodes = VolMgrPlugin._get_nodes(plugin_api_context)
        mses = VolMgrPlugin._get_mses(plugin_api_context)

        all_nodes = nodes + mses

        all_profiles = node_profiles + ms_profiles + cluster_profiles

        unique_source_profiles = set()
        for profile in all_profiles:
            unique_source_profiles.add(profile.get_source())

        for source_profile in unique_source_profiles:
            errors += self.lvm_driver.validate_profile_globally(source_profile,
                                                                all_nodes)

        # Now validate Node related issues (System against Profile)
        for node in all_nodes:

            if node.is_ms():
                errors += \
                   VolMgrPlugin._validate_storage_profile_and_system_pair(node)

            if node.system:
                errors += VolMgrPlugin._validate_bootable_disk(node)

                errors += VolMgrPlugin._validate_unique_disk_name(node)
                errors += self._validate_unique_disk_uuid(node)

                if node.storage_profile:

                    if node.item_type_id == 'node':
                        errors += self._validate_node_profile_is_lvm(node)

                    errors += VolMgrPlugin._validate_disk_exists(node)
                    errors += VolMgrPlugin._validate_pd_disks(node)
                    errors += VolMgrPlugin._validate_node_disk_properties(node)
                    errors += VolMgrPlugin._validate_root_vg_boot_disk(node)
                    errors += VolMgrPlugin._validate_non_root_vg_disks(node)
                    errors += \
                        VolMgrPlugin._validate_node_vg_uniform_disk_types(node)
                    errors += self.lvm_driver.validate_node(plugin_api_context,
                                                            node)

        vcs_clusters = plugin_api_context.query("vcs-cluster")

        for cluster in vcs_clusters:
            errors += self.vxvm_driver.validate_disk_sizes(cluster)
            errors += self._validate_disk_in_cluster(cluster)
            errors += VolMgrPlugin._validate_cluster_type_sfha(cluster)
            errors += VolMgrPlugin._validate_unique_dg_name(cluster)
            errors += VolMgrPlugin._validate_unique_pd_name(cluster)
            errors += VolMgrPlugin._validate_cluster_disk_properties(cluster)
            errors +=\
                VolMgrPlugin._validate_cluster_vg_uniform_disk_types(cluster)

        errors += \
            self.vxvm_driver.validate_pd_to_sp_mapping(plugin_api_context)

        infras = plugin_api_context.query("infrastructure")
        if infras:
            vxvm_profiles = []
            lvm_profiles = []

            for profile in infras[0].storage.storage_profiles:
                if profile.volume_driver == "vxvm":
                    vxvm_profiles.append(profile)

                elif profile.volume_driver == "lvm":
                    lvm_profiles.append(profile)

            if vxvm_profiles:
                errors += VolMgrPlugin._validate_vxvm_fs_type(vxvm_profiles)
                errors += VolMgrPlugin._validate_vxfs_fs_name(vxvm_profiles)

            if lvm_profiles:
                errors += VolMgrPlugin._validate_lvm_fs_type(lvm_profiles)

        return errors

    @staticmethod
    def _validate_storage_profile_and_system_pair(ms):

        preamble = '._validate_storage_profile_and_system_pair: %s:' % \
                   ms.item_id

        errors = []
        if ms.storage_profile and not ms.system:
            msg = 'A "storage-profile" is inherited to the MS; a "system" ' + \
                  'must also be inherited to the MS.'
            log.trace.debug(preamble + msg)
            error = ValidationError(item_path=ms.get_vpath(),
                                    error_message=msg)
            errors.append(error)

        return errors

    @staticmethod
    def _validate_ms_lvm_profile(profile):
        '''
        Validate that a ``storage-profile`` referenced by a ``MS`` has a
        ``volume_driver`` property value of 'lvm' only.
        '''

        preamble = '._validate_ms_lvm_profile: '
        errors = []

        if profile.volume_driver != 'lvm':
            msg = 'The "volume_driver" property on an MS ' + \
                  'storage-profile must have a value of "lvm"'
            log.trace.debug(preamble + msg)
            error = ValidationError(item_path=profile.get_vpath(),
                                    error_message=msg)
            errors.append(error)

        return errors

    @staticmethod
    def _validate_ms_profile_no_kickstart_clash(profile):
        '''
        Validate that non vg-root volume groups on the MS do not contain file
        systems with mount points reserved for kickstarted LVM file systems
        '''

        preamble = '._validate_ms_profile_no_kickstart_clash: '

        ks_fss = VolMgrUtils.get_kickstart_fss()

        ks_fs_mounts = [ks_fs.mount_point for ks_fs in ks_fss]

        ks_fs_suffix = ("Reserved MS Kickstarted LVM mount points: " +
                        ', '.join(ks_fs_mounts))

        errors = []

        vgs = [vg for vg in profile.volume_groups
               if not vg.is_for_removal() and
               not vg.volume_group_name == MS_ROOT_VG_GROUP_NAME]

        for vg in vgs:

            fss = [fs for fs in vg.file_systems
                   if not fs.is_for_removal()]

            for fs in fss:
                if fs.mount_point in ks_fs_mounts:
                    msg = ('mount_point "%s" conflicts with MS ' +
                           'Kickstarted LVM file-system. ' +
                           ks_fs_suffix) % fs.mount_point
                    log.trace.debug(preamble + msg)
                    error = ValidationError(item_path=fs.get_vpath(),
                                            error_message=msg)
                    errors.append(error)
        return errors

    @staticmethod
    def _veto_size_chg_by_fs_type(fs):

        preamble = '._veto_size_chg_on_defined_types: '
        log.trace.debug(preamble)
        errors = []

        if fs.type not in UPDATABLE_FS_TYPES:
            msg = "A change of 'size' is only permitted " + \
                  "on a file-system of type 'ext4', 'xfs' or 'vxfs'"
            log.trace.debug(preamble + msg)
            err = ValidationError(item_path=fs.get_vpath(),
                                  error_message=msg)
            errors.append(err)

        return errors

    @staticmethod
    def _veto_fs_size_decrease(fs, new_size, previous_size):

        preamble = '._veto_fs_size_decrease: '
        log.trace.debug(preamble)
        errors = []

        if int(new_size) < int(previous_size):
            msg = "Decreasing the 'size' property of a " + \
                  "file-system is not supported"
            log.trace.debug(preamble + msg)
            err = ValidationError(item_path=fs.get_vpath(),
                                  error_message=msg)
            errors.append(err)

        return errors

    @staticmethod
    def _veto_fs_size_chg_if_snap_present(fs, context):

        preamble = '._veto_fs_size_chg_if_snap_present: '
        log.trace.debug(preamble)
        errors = []

        # Disallow resize if snapshot is present
        snapshot_names = VolMgrPlugin._find_snapshot_names(context)
        if snapshot_names:
            msg = 'Snapshot(s) with name(s) "%s" exist. ' \
                  'Changing the "size" property of an "%s" or ' \
                  '"vxfs" file system while a snapshot exists ' \
                  'is not supported' % \
                  (snapshot_names, '", "'.join(LVM_FS_TYPES_NON_SWAP))

            error = ValidationError(item_path=fs.get_vpath(),
                                           error_message=msg)
            log.trace.debug(msg)
            errors.append(error)
        return errors

    @staticmethod
    def _find_snapshot_names(context):
        snapshots = VolMgrPlugin._get_active_snapshots(context)
        if snapshots:
            snapshot_names = []
            for snapshot in snapshots:
                snapshot_names.append(
                    str(snapshot.get_vpath().split("/")[-1]))
            return (", ").join(snapshot_names)

    @staticmethod
    def _validate_ms_veto_removal_of_ks_fs(profile):
        '''
        Validate that a ks file-system is not
        permitted to be removed.
        '''

        preamble = '_validate_ms_veto_removal_of_ks_fs: '

        errors = []

        for vg in profile.volume_groups:
            for fs in vg.file_systems:
                if fs.is_for_removal() and \
                    VolMgrUtils.is_a_ks_filesystem(vg, fs):
                    message = 'The removal of an MS Kickstarted ' \
                              'LVM file-system is not supported'

                    log.trace.debug((preamble + "VG:%s FS:%s Error: %s") %
                            (vg.item_id, fs.item_id, message))

                    errors.append(ValidationError(item_path=fs.get_vpath(),
                                                     error_message=message))
        return errors

    @staticmethod
    def _validate_ms_ks_fs_size_update(ms_profile):
        '''
        Validates that:
        1. decreasing the size of a ks-fs is not permitted.
        2. size changes are only permitted on fs types ext4, xfs and vxfs.
        '''

        preamble = '._validate_ms_ks_fs_size_update: '
        log.trace.debug(preamble)
        errors = []

        for vg in ms_profile.volume_groups:
            if vg.is_for_removal():
                continue

            for fs in vg.file_systems:
                if fs.is_initial() and \
                        VolMgrUtils.is_a_ks_filesystem(vg, fs):

                    new_size_mb = VolMgrUtils.get_size_megabytes(fs.size)

                    kss_size = VolMgrUtils.get_ks_filesystem_size(fs)
                    previous_size_mb = VolMgrUtils.get_size_megabytes(
                                                             kss_size)

                    if new_size_mb != previous_size_mb:
                        errors += VolMgrPlugin._veto_fs_size_decrease(fs,
                                                            new_size_mb,
                                                       previous_size_mb)
                        errors += VolMgrPlugin._veto_size_chg_by_fs_type(fs)

        return errors

    @staticmethod
    def _validate_ms_ks_fs_snapshot_not_present(profile, context):
        """
        Validates that initial modeling of a kickstarted file-system
        while a snapshot is present is not permitted.
        """

        snapshots = VolMgrPlugin._get_active_snapshots(context)

        if not snapshots:
            return []

        msg = 'Modeling an MS Kickstarted file-system ' \
              'while a snapshot exists is not supported'

        errors = []

        for fs in VolMgrUtils._get_modeled_ks_filesystems(profile):
            if fs.is_initial():
                error = ValidationError(item_path=fs.get_vpath(),
                                        error_message=msg)
                errors.append(error)

        return errors

    @staticmethod
    def _validate_fs_size_update(profile, context):
        '''
        Validates that:
        1. Decreasing the size of an fs is not permitted.
        2. Changing the size of a fs while a snapshot is
           present is not permitted.
        3. A fs size update is only allowed for file systems
           of type ext4, xfs and vxfs
        '''

        preamble = '._validate_fs_size_update: '
        log.trace.debug(preamble)
        errors = []

        for vg in profile.volume_groups:
            for fs in vg.file_systems:
                if VolMgrUtils._item_property_has_changed(fs, 'size'):

                    new_size_mb = VolMgrUtils.get_size_megabytes(fs.size)
                    previous_size_mb = VolMgrUtils.get_size_megabytes(
                                                fs.applied_properties['size'])

                    errors += VolMgrPlugin._veto_fs_size_decrease(fs,
                                                         new_size_mb,
                                                    previous_size_mb)
                    errors += VolMgrPlugin._veto_size_chg_by_fs_type(fs)
                    errors += VolMgrPlugin._veto_fs_size_chg_if_snap_present(
                                                                          fs,
                                                                     context)

        return errors

    @staticmethod
    def _get_active_snapshots(context):
        all_snaps = [snap for snap in context.query('snapshot-base')]

        return_all = False

        try:
            if context.snapshot_action() == 'remove':
                return_all = True
        except NoSnapshotItemError:
            return_all = False

        if return_all:
            return all_snaps
        else:
            return [snap
                    for snap in all_snaps
                    if not snap.is_for_removal()]

    @staticmethod
    def _snap_size_toggled(fs):
        if 'snap_size' not in fs.applied_properties:
            return False

        return ((fs.applied_properties['snap_size'] != '0') and
                (fs.snap_size == '0')) or \
               ((fs.applied_properties['snap_size'] == '0') and
                 (fs.snap_size != '0'))

    @staticmethod
    def _snap_external_added(fs):
        return ('snap_external' not in fs.applied_properties) and \
                hasattr(fs, 'snap_external') and fs.snap_external

    @staticmethod
    def _snap_external_changed(fs):
        return ('snap_external' in fs.applied_properties) and \
               (not hasattr(fs, 'snap_external') or
                (hasattr(fs, 'snap_external') and
                 (fs.snap_external != fs.applied_properties['snap_external'])))

    @staticmethod
    def _validate_veto_removal_of_pd(profile):
        '''
        Disallows removal of PD from VG post-runplan.
        '''

        preamble = '._validate_veto_removal_of_pd: '

        errors = []

        for vg in [vg for vg in profile.volume_groups if vg.is_applied()]:
            for pd in vg.physical_devices:
                if pd.is_for_removal():
                    msg = "Removal of active physical device " \
                          "is not permitted."
                    log.trace.debug(preamble + msg)
                    err = ValidationError(item_path=pd.get_vpath(),
                                          error_message=msg)
                    errors.append(err)

        return errors

    def _gen_tasks_for_node(self, node, context):
        '''
        Generate all tasks for a given node
        '''

        preamble = '._gen_tasks_for_node: %s : ' % node.item_id

        log.trace.debug((preamble + "Generating tasks for %s '%s'") %
                        (node.item_type_id, node.item_id))

        tasks = []

        if node.storage_profile.volume_driver == 'lvm':
            for vg in node.storage_profile.volume_groups:
                tasks += self.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                    context)
            if not node.is_ms():
                tasks += self.lvm_driver.gen_grub_tasks(node,
                                        node.storage_profile.volume_groups)

        return tasks

    def create_configuration(self, plugin_api_context):
        """
        Creates tasks to enforce physical volumes, volume groups
        and file-systems from the model.

        *Example CLI for the plugin:*

        .. code-block:: bash
            :linenos:

            litp create \
-t storage-profile -p /infrastructure/storage/storage_profiles/sp1 \
-o volume_driver="lvm"
            litp create -t volume-group \
-p /infrastructure/storage/storage_profiles/sp1/\
volume_groups/vg1 -o volume_group_name="root_vg"
            litp create \
-t file-system \
-p /infrastructure/storage/storage_profiles/sp1/\
volume_groups/vg1/file_systems/fs1 \
-o type="xfs" mount_point="/" size="10G"
            litp create \
-t file-system \
-p /infrastructure/storage/storage_profiles/sp1/\
volume_groups/vg1/file_systems/fs2 \
-o type="swap" mount_point="swap" size="12G"
            litp create \
-t physical-device \
-p /infrastructure/storage/storage_profiles/sp1/\
volume_groups/vg1/physical_devices/internal \
-o device_name="hd0"
            litp create \
-t blade \
-p /infrastructure/systems/sys1 \
-o system_name="CZJ33308HF"
            litp create \
-t disk \
-p /infrastructure/systems/sys1/disks/disk0 \
-o uuid="600508b1001c992b3a403cab03508cfc" \
size="35G" name="hd0" bootable="true"
            litp inherit \
-p /deployments/d1/clusters/c1/nodes/n1/system \
-s /infrastructure/systems/sys1
            litp inherit \
-p /deployments/d1/clusters/c1/nodes/n1/storage_profile \
-s /infrastructure/storage/storage_profiles/sp1



        *Example XML for the plugin:*

        .. code-block:: xml

            <litp:storage-profile id="sp1">
              <volume_driver>lvm</volume_driver>
              <litp:storage-profile-volume_groups-collection \
id="volume_groups">
                <litp:volume-group id="vg1">
                  <volume_group_name>root_vg</volume_group_name>
                  <litp:volume-group-file_systems-collection id="file_systems">
                    <litp:file-system id="fs1">
                      <type>xfs</type>
                      <mount_point>/</mount_point>
                      <size>10G</size>
                    </litp:file-system>
                    <litp:file-system id="fs2">
                      <type>swap</type>
                      <mount_point>swap</mount_point>
                      <size>12G</size>
                    </litp:file-system>
                  </litp:volume-group-file_systems-collection>
                  <litp:volume-group-physical_devices-collection \
id="physical_devices">
                    <litp:physical-device id="internal">
                      <device_name>hd0</device_name>
                    </litp:physical-device>
                  </litp:volume-group-physical_devices-collection>
                </litp:volume-group>
              </litp:storage-profile-volume_groups-collection>
            </litp:storage-profile>

            <litp:blade id="sys1">
              <system_name>CZJ33308HF</system_name>
              <litp:disk id="disk0">
                <uuid>600508b1001c992b3a403cab03508cfc</uuid>
                <size>35G</size>
                <name>hd0</name>
                <bootable>true</bootable>
              </litp:disk>
            </litp:blade>

        For more information, see \
"Logical Volume Management" and "VxVM Management" \
from :ref:`LITP References <litp-references>`.

        """
        tasks = []
        preamble = '.create_configuration: '

        vcs_cluster = plugin_api_context.query("vcs-cluster")
        for cluster in vcs_cluster:
            log.trace.debug((preamble +
                            "Processing cluster '%s'") %
                            cluster.item_id)

            for sp in cluster.storage_profile:
                if sp.volume_driver != 'vxvm':
                    continue

                log.trace.debug((preamble +
                                 "Processing storage profile '%s'") %
                                sp.item_id)

                tasks += self.vxvm_driver.gen_tasks_for_storage_profile(
                                                            plugin_api_context,
                                                            cluster, sp)

            tasks += self.vxvm_driver.gen_tasks_for_fencing_disks(cluster)

            tasks += self.vxvm_driver.gen_noop_tasks_for_disk_updates(cluster)

        nodes = VolMgrPlugin._get_nodes(plugin_api_context)
        mses = VolMgrPlugin._get_mses(plugin_api_context)

        all_nodes = nodes + mses

        for node in all_nodes:
            log.trace.debug((preamble + "Examining %s '%s'") %
                            (node.item_type_id, node.item_id))

            if node.storage_profile and node.system:
                log.trace.debug((preamble +
                                "Processing storage profile on %s '%s'") %
                                (node.item_type_id, node.item_id))
                tasks += self._gen_tasks_for_node(node, plugin_api_context)
            else:
                log.trace.debug((preamble +
                                 "Node '%s' does not have both a " +
                                 "system and storage profile") %
                                 node.get_vpath())

        for task in tasks:
            log.trace.debug(preamble + "Task: %s" % task)

        return tasks

    def create_snapshot_plan(self, plugin_api_context):
        '''
        Create a plan for ``create``, ``remove`` or ``restore``
        snapshot actions. Generates tasks for snapshot creation,
        deletion and restoration on management server and managed nodes
        for LVM and VxVM volumes.

        Plan tasks to restart clusters during restoration of a
        deployment snapshot are issued by this plugin in the order
        derived from the user-specified dependency_list property of the
        clusters in the deployment at snapshot creation time.  If no
        order is specified, or an invalid order (such as a circular
        dependency) is specified, then the tasks to restart clusters
        are still issued, but the clusters are restarted in an unordered
        fashion. A restart task is generated for a cluster if any node
        in the cluster contains a ``file-system`` that is part of a
        ``volume-group`` used by a ``disk`` type.
        '''
        try:
            action = plugin_api_context.snapshot_action()
        except Exception as e:
            raise PluginError(e)

        if action == 'create' and (
                plugin_api_context.snapshot_name() == UPGRADE_SNAPSHOT_NAME):
            for deployment in plugin_api_context.query('deployment'):
                try:
                    dummy = deployment.ordered_clusters
                except ViewError as ve:
                    self._warn_invalid_cluster_order(deployment.item_id, ve)

        return self._get_snapshot_tasks(plugin_api_context, action)

    def _get_snapshot_tasks(self, context, action):

        snapshot_model = None
        mnnodes = []
        tasks = []

        if action in ('remove', 'restore'):

            snapshot_model = context.snapshot_model()

            if not snapshot_model:
                if action == 'remove':
                    return tasks
                else:
                    msg = 'Snapshot model missing for %s_snapshot action' % \
                          action
                    log.trace.error(msg)
                    raise PluginError(msg)
            else:
                msnodes = snapshot_model.query("ms")
                mnnodes = VolMgrPlugin._get_del_snap_nodes(context,
                                                    snapshot_model)
        else:
            msnodes = context.query("ms")
            mnnodes = context.query("node")

        if action in ('create', 'remove'):
            mnnodes = [node for node in mnnodes
                       if node not in context.exclude_nodes]

        all_nodes = mnnodes + msnodes

        # It is presently assumed here that snapshot plan builder and plan
        # executor in litp core run callback tasks entirely serially (even
        # within one phase) and in the order the plugin returns, other than a
        # stable reordering pass based on tasktags->rulesets->groups.
        # The snapshot plan builder does not honour inter-task dependencies.
        # The assumed behaviour of the core snapshot plan builder is thus
        # significantly different to that of the core deployment plan builder.

        # 1. VxVM tasks
        tasks += self.vxvm_driver.gen_tasks_for_snapshot(context)

        # TODO: >>> The following block of code should be encapsulated
        # TODO: >>> and provided as a member function by LVM driver
        # TODO: >>> e.g. self.lvm_driver.gen_tasks_for_snapshot(context)

        # 2. LVM modeled storage-profile tasks
        snappable_nodes = VolMgrPlugin._snappable_nodes(
            all_nodes, action, 'lvm')

        # LITPCDS-12784: when a remove snapshot action is specified, the
        # plugin should generate;
        #
        #  1. A single task that checks if nodes are reachable,
        #     skipped if -f option specified
        #
        #  2. A single task that checks if any LVM snapshot is in a
        #     merging state, regardless of -f option
        if action == 'remove':

            task = self.lvm_driver.check_remove_nodes_reachable_task(
                context, snappable_nodes)

            # will return None if no peer nodes are reachable or
            if task is not None:
                tasks.append(task)

            tasks.append(
                self.lvm_driver.check_restore_in_progress_task(
                    context, snappable_nodes
                )
            )

        if snappable_nodes:
            tasks += self.lvm_driver.gen_tasks_for_modeled_snapshot(
                                    context, snapshot_model, snappable_nodes)

        # 3. LVM MS Kickstarted tasks
        if msnodes:
            tasks += self.lvm_driver.gen_tasks_for_ms_non_modeled_ks_snapshots(
                                                              context, msnodes)

        # TODO: >>>

        # 4. Reboots on restoration
        if action == 'restore':
            tasks += self.reboot_planner.gen_tasks(context)

        return tasks

    def _is_reachable_node(self, callback_api, hostname):

        reachable = False
        timeout = Timeout(PING_TIMEOUT)

        # if the node is not reachable and timeout not elapsed
        while not (reachable or timeout.has_elapsed()):
            if not callback_api.is_running():
                raise PlanStoppedException("Plan execution has been stopped.")

            elapsed_time = int(timeout.get_elapsed_time())

            # ping every 2 seconds
            if elapsed_time % 2 == 0:

                response = run_rpc_application([hostname], ["ping"])
                remaining_time = int(timeout.get_remaining_time())

                log.trace.debug(
                    "Pinging node %s, %d secs left rc %d" %
                    (hostname, remaining_time, response)
                )

                if response in (0, 1):
                    # setting this will exit the timeout loop
                    reachable = True

                timeout.sleep(1)

        return reachable

    def _filter_reachable_nodes(self, cb_api, hostnames):

        log.trace.debug("_filter_reachable_nodes")

        nodes_up = []
        nodes_down = []

        for hostname in hostnames:
            if self._is_reachable_node(cb_api, hostname):
                nodes_up.append(hostname)
            else:
                log.trace.info("Node %s is down" % hostname)
                nodes_down.append(hostname)

        if nodes_down:
            log.trace.debug("%d node(s) down" % len(nodes_down))
        else:
            log.trace.debug("all nodes are up ")

        return nodes_up, nodes_down

    def _warn_invalid_cluster_order(self, deployment_id, view_error):
        log.event.warning(
            'Order of clusters is invalid.  Unordered cluster reboot sequence'
            ' will be used during deployment snapshot restore for deployment'
            ' "{deployment_id}": {view_error}'.format(
                deployment_id=deployment_id,
                view_error=view_error),
            exc_info=False)

    def _check_active_nodes_cb(self, cb_api, data_set):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        if data_manager.get_action() == 'restore':
            data_manager.gather_active_nodes(cb_api,
                                         stop_on_unreachable_nodes=True)
            unreachable_nodes = data_manager.get_all_unreachable_nodes()
            if unreachable_nodes:
                msg = "The following nodes are unreachable: %s " \
                             % VolMgrUtils.format_list(unreachable_nodes)
                raise CallbackExecutionException(msg)
        else:
            data_manager.gather_active_nodes(cb_api)

        self.vxvm_driver.check_disk_groups_active_nodes(data_manager)

    def _check_presence_cb(self, cb_api, data_set):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        data_manager.gather_active_nodes(cb_api)

        data_manager.gather_snapshots_are_present(cb_api)

        self.vxvm_driver.check_snapshots_exist(data_manager)

    def _check_validity_cb(self, cb_api, data_set):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        data_manager.gather_active_nodes(cb_api)

        data_manager.gather_snapshots_are_present(cb_api)
        data_manager.gather_snapshots_are_valid(cb_api)

        self.vxvm_driver.check_snapshots_valid(data_manager)

    def _check_restores_in_progress_cb(self, cb_api, data_set):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        data_manager.gather_active_nodes(cb_api)

        data_manager.gather_snapshots_are_present(cb_api)
        data_manager.gather_restores_in_progress(cb_api)

        self.vxvm_driver.check_restores_in_progress(data_manager)

    def _create_vxvm_snapshot_cb(self, cb_api,
                                 data_set, fs_filter):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        data_manager.set_filters(vg_vpaths=[fs_filter['vg']],
                                 fs_ids=[fs_filter['fs']])

        data_manager.gather_active_nodes(cb_api)

        self.vxvm_driver.create_snapshot(cb_api, data_manager)

    def _remove_vxvm_snapshot_cb(self, cb_api, data_set, fs_filter):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        data_manager.set_filters(vg_vpaths=[fs_filter['vg']],
                                 fs_ids=[fs_filter['fs']])

        data_manager.gather_active_nodes(cb_api)

        data_manager.gather_snapshots_are_present(cb_api,
                                                  check_for_cache=True)
        data_manager.gather_snapshots_are_valid(cb_api)

        self.vxvm_driver.remove_snapshot(cb_api, data_manager)

    def _restore_vxvm_snapshot_cb(self, cb_api, data_set, fs_filter):

        data_manager = VxvmSnapshotDataMgr(driver=self.vxvm_driver,
                                           data_set=data_set)

        data_manager.set_filters(vg_vpaths=[fs_filter['vg']],
                                 fs_ids=[fs_filter['fs']])

        data_manager.gather_active_nodes(cb_api)

        data_manager.gather_snapshots_are_present(cb_api)
        data_manager.gather_snapshots_are_valid(cb_api)

        self.vxvm_driver.restore_snapshot(cb_api, data_manager)

    def _check_valid_ss_rpc_task(
            self, cb_api, nodes, agent, action, **kwargs):

        expected_snapshots = kwargs.pop('expected_snapshots', None)

        log.trace.debug("_check_valid_ss_rpc_task")
        nodes_down = []

        timeout = kwargs.pop('timeout', DEFAULT_RPC_TIMEOUT)
        snap_name = kwargs.pop('snap_name', 'snapshot')
        action_forced = kwargs.pop('force', False)

        if action_forced:
            nodes_up, nodes_down = self._filter_reachable_nodes(cb_api, nodes)
            nodes = nodes_up

        if nodes_down:
            msg = "Node(s) %s down" % VolMgrUtils.format_list(nodes_down)
            log.trace.info(msg)

        action_output, errors = self._run_rpc_action(
            cb_api, nodes, agent, action, timeout, **kwargs
        )
        if errors:
            self._scan_for_lvs_errs(errors)

        lv_output = self._scan_lv_data(action_output, snap_name)

        invalid_snapshot_errors = []

        for node_name, expected_ss_data in expected_snapshots.items():
            for expected_snapshot in expected_ss_data.get('snapshots', []):

                lv_ss = [lv_snapshot for lv_snapshot in lv_output if \
                        lv_snapshot.get('node_name') == node_name \
                        and lv_snapshot['lv_name'] == expected_snapshot]

                if not lv_ss:
                    msg = 'Snapshot %s is missing on node %s' % \
                           (expected_snapshot, node_name)
                    if action_forced:
                        log.trace.debug(msg)
                    else:
                        log.trace.error(msg)
                else:
                    attributes = lv_ss[0]['attrs']

                    if not VolMgrUtils.check_snapshot_is_valid(attributes):

                        msg = "snap {0} on node {1} is invalid".format(
                            expected_snapshot,
                            node_name)

                        log.trace.debug(msg)
                        invalid_snapshot_errors.append(msg)

        if invalid_snapshot_errors:
            raise CallbackExecutionException(invalid_snapshot_errors)

    def _check_restore_nodes_reachable_and_snaps_exist_cb(
            self, callback_api, host_data, agent, action, **kwargs):

        preamble = "_check_restore_nodes_reachable_and_snaps_exist_cb: "

        peer_nodes = [hostname for (hostname, is_ms) in host_data if not is_ms]

        _, unreachable_peers = self._filter_reachable_nodes(callback_api,
                                                            peer_nodes)

        if unreachable_peers:
            peers_csl = VolMgrUtils.format_list(unreachable_peers)
            unreachable_node_err = "Unreachable node(s): %s" % peers_csl
            log.trace.debug(preamble + unreachable_node_err)
            log.trace.error(unreachable_node_err)

        hosts_up = [hostname for (hostname, is_ms) in host_data \
                              if not hostname in unreachable_peers]

        msgs = self._check_snapshots_exist(callback_api, hosts_up, agent,
                                        action, **kwargs)
        if unreachable_peers and unreachable_node_err:
            msgs.append(unreachable_node_err)

        if msgs:
            raise CallbackExecutionException(', '.join(msg for msg in msgs))

    def _check_snapshots_exist(self, callback_api, hosts,
                               agent, action, **kwargs):

        msgs = []
        expected_snapshots = kwargs.pop('expected_snapshots', [])

        snap_name, timeout = kwargs.pop('snap_name', 'snapshot'), \
                             kwargs.pop('timeout', DEFAULT_RPC_TIMEOUT)

        action_output, errors = VolMgrPlugin._run_rpc_action(callback_api,
                                                     hosts, agent, action,
                                                        timeout, **kwargs)
        if errors:
            self._scan_for_lvs_errs(errors)

        lv_output = self._scan_lv_data(action_output, snap_name)

        for node_name, expected_ss_data in expected_snapshots.items():
            if node_name in hosts:
                for expected_snapshot in expected_ss_data.get('snapshots', []):

                    exists = [lv_snapshot for lv_snapshot in lv_output if \
                              lv_snapshot.get('node_name') == node_name \
                              and lv_snapshot['lv_name'] == expected_snapshot]
                    if not exists:
                        msg = 'Snapshot %s is missing on node %s' % \
                               (expected_snapshot, node_name)
                        log.trace.error(msg)
                        msgs.append(msg)
        return msgs

    def _scan_for_lvs_errs(self, errors):
        log.trace.debug("_scan_for_lvs_errs %s " % errors)

        if errors and errors != "":
            expired_errors = []
            for node in errors.keys():
                errs = errors[node][0]
                if EXPIRED_STR in errs:
                    log.trace.error("lvs error %s on %s" % (errs, node))
                    expired_errors.append(errs)
            if expired_errors:
                raise CallbackExecutionException(expired_errors)

    @staticmethod
    def _run_rpc_action(callback_api, hosts, agent, action, timeout, **kwargs):

        output, errors = RpcCommandOutputNoStderrProcessor().\
                           execute_rpc_and_process_result(callback_api,
                                                              hosts,
                                                              agent,
                                                              action,
                                                              kwargs, timeout)
        return output, errors

    def _scan_lv_data(self, lvs_data, snap_name):

        lv_metadata = []
        for node_name in lvs_data.keys():

            node_output = [lv_info for lv_info in lvs_data[node_name].\
                           split('\n') if lv_info]

            log.trace.debug("node is %s" % node_name)

            if not snap_name:
                snap_name = 'snapshot'

            for line in node_output:
                path, size, vg_name, lv_name, attrs, mount = \
                                        VolMgrUtils.parse_lvscan_line(line)
                log.trace.debug("lv_name is %s" % lv_name)

                snapshot_data = {'path': path,
                                 'size': size,
                                 'vg_name': vg_name,
                                 'lv_name': lv_name,
                                 'attrs': attrs,
                                 'node_name': node_name,
                                 'mount': mount}

                lv_metadata.append(snapshot_data)

        return lv_metadata

    def _restore_ss_rpc_task(self, cb_api, node, agent, action,
                            timeout=DEFAULT_RPC_TIMEOUT, **kwargs):
        action_forced = kwargs.pop('force', False)
        log.trace.debug("restore_ss_rpc_task")
        node_down = []

        if action_forced:
            _, node_down = self._filter_reachable_nodes(cb_api, node)

        if not node_down:
            try:
                _, errors = self.ic_processor.execute_rpc_and_process_result(
                               cb_api, node, agent, action, kwargs, timeout,
                               extra_args=([12, 404],))
            except RpcExecutionException as e:
                raise CallbackExecutionException(e)
            if errors:
                for error in reduce_errs(errors):
                    if "Unable to merge invalidated snapshot LV" in error:
                        raise CallbackExecutionException("Unable to merge " + \
                                                     "invalidated snapshot LV")
                raise CallbackExecutionException(
                                                ', '.join(reduce_errs(errors)))

    def _base_with_imported_dg(self, cb_api, cluster, agent, action,
                                    dg_name, **kwargs):

        # makes sure the dg is imported before doing anything else
        cluster_obj = [c for c in cb_api.query('cluster')
                       if c.vpath == cluster][0]
        try:
            ignore_unreachable = kwargs.pop('ignore_unreachable', False)
            dg_metadata = self.vxvm_driver.\
                                  get_dg_hostname(cb_api,
                                                  cluster_obj,
                                                  [dg_name],
                                        ignore_unreachable=ignore_unreachable)
            dg_imported_in, _ = dg_metadata[dg_name]
        except (DgNotImportedError, PluginError) as e:
            raise CallbackExecutionException(e)

        self._base_rpc_task(
              cb_api, [dg_imported_in.hostname], agent, action, **kwargs)

    def _task_with_future_uuid(self, cb_api, nodes, agent, action, **kwargs):
        # base rpc task, but picks the disk uuid when the task is run and not
        # at create_plan time. This is kind of an implementation of
        # FuturePropertyValues in callback tasks
        # assumes there is a kwarg named uuid with the item id of the disk
        if not kwargs.get('uuidfromview'):
            raise CallbackExecutionException("UUID not found in the task args")

        kwargs['uuid'] = cb_api.query_by_vpath(kwargs.get('uuidfromview')).uuid
        self._base_rpc_task(cb_api, nodes, agent, action, **kwargs)

    def _base_rpc_task(self, cb_api, nodes, agent, action, **kwargs):

        timeout = kwargs.pop('timeout', None)
        retries = kwargs.pop('retries', 0)

        try:
            _, errors = self.base_processor.execute_rpc_and_process_result(
                           cb_api, nodes, agent,
                           action, kwargs, timeout, retries)

        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def _create_snapshot_cb(self, cb_api, hostname, agent, action, **kwargs):

        preamble = "_create_snapshot_cb"

        timeout = kwargs.pop('timeout', None)
        retries = kwargs.pop('retries', 0)

        nodes = [hostname]

        try:
            _, errors = self.base_processor.execute_rpc_and_process_result(
                cb_api, nodes, agent, action, kwargs, timeout, retries
            )

        except RpcExecutionException as e:

            log.trace.debug(
                preamble + " - has timed out after %s secs, "
                           "node '%s' didn't respond" % (timeout, hostname)
            )

            log.trace.debug(preamble + " - %s" % e)

            raise CallbackExecutionException(e)

        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def __remove_rpc(self, cb_api, hostname, agent, action, **kwargs):

        timeout = kwargs.pop('timeout', None)
        retries = kwargs.pop('retries', 2)
        nodes = [hostname]

        try:
            _, errors = self.ic_processor.execute_rpc_and_process_result(
                cb_api, nodes, agent, action, kwargs, timeout, retries,
                extra_args=([12],)
            )

        except RpcExecutionException as e:
            raise CallbackExecutionException(e)

        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def _ping_and_remove_snapshot_cb(self, cb_api, hostname, **kwargs):

        if not self._is_reachable_node(cb_api, hostname):
            msg = 'Node "%s" not currently reachable. Continuing.' % hostname
            log.trace.debug(msg)
            log.trace.info(msg)
        else:
            self._remove_snapshot_cb(cb_api, hostname, **kwargs)

    def ___is_lvm_merging_cb(self, cb_api, hostname, **kwargs):

        try:

            agent = 'snapshot'
            action = 'is_merging'
            nodes = [hostname]
            timeout = None
            retries = 2

            _, err = self.base_processor.execute_rpc_and_process_result(
                cb_api, nodes, agent, action, kwargs, timeout, retries
            )

            if err:
                if 'is merging' in err:
                    return True
                else:
                    raise CallbackExecutionException(
                                        ', '.join(reduce_errs(err)))

        except RpcExecutionException as e:
            raise CallbackExecutionException(e)

        return False

    def _check_lvm_restores_in_progress_cb(
            self, cb_api, snap_data, action_forced, **kwargs):

        # Example data passed to the callback
        #
        # snap_data = {
        #    'host1': {
        #       'vg_root' : [
        #           {'snapshot_name': 'L_x1_'},
        #           {'snapshot_name': 'L_x2_'},
        #       ],
        #    'host2': {
        #       'vg_foo' : [
        #           {'snapshot_name': 'L_x1_'},
        #           {'snapshot_name': 'L_x2_'},
        #       ],
        #               .....
        # }

        preamble = "_check_lvm_restores_in_progress_cb: "

        debug_template = (
            "Snapshot '{snap1}' in volume group '{vg}' on node "
            "'{host}' is in a merging state")

        nodes_with_merging_snapshots = []

        hostnames = snap_data.keys()

        if action_forced:

            # if the action is forced, filter out any unreachable hosts
            hostnames, unreachable = self._filter_reachable_nodes(cb_api,
                                                                  hostnames)
            lst = VolMgrUtils.format_list(unreachable)

            # log if there are unreachable nodes
            if unreachable:
                msg = "Could not contact the following node(s) {0}".format(lst)
                log.trace.debug(preamble + msg)
                log.trace.info(preamble + msg)

        for hostname in hostnames:

            vg_data = snap_data[hostname]

            # this flag indicates if a merging snapshot exists on the host
            host_has_merging_snapshot = False

            for vg_name, snap_list in vg_data.iteritems():

                for snap in snap_list:

                    snapshot_name = snap['snapshot_name']

                    if self.___is_lvm_merging_cb(
                            cb_api, hostname,
                            vg=vg_name,
                            name=snapshot_name):

                        msg = debug_template.format(snap1=snapshot_name,
                                                    vg=vg_name, host=hostname)

                        log.trace.debug(preamble + msg)

                        host_has_merging_snapshot = True

            # append the host if it has any merging snapshots
            if host_has_merging_snapshot:
                nodes_with_merging_snapshots.append(hostname)

        if nodes_with_merging_snapshots:
            msg = ('The following node(s) have snapshot(s) '
                   'in a merging state: {nodes}')

            node_list = VolMgrUtils.format_list(nodes_with_merging_snapshots)

            raise CallbackExecutionException(msg.format(nodes=node_list))

    def _check_remove_nodes_reachable_cb(self, cb_api, hostnames, **kwargs):

        _, unreachable_nodes = self._filter_reachable_nodes(cb_api, hostnames)

        if unreachable_nodes:
            msg = 'The following nodes are unreachable: %s' % \
                  VolMgrUtils.format_list(unreachable_nodes)

            raise CallbackExecutionException(msg)

    def _remove_snapshot_cb(self, cb_api, hostname, **kwargs):
        self.__remove_rpc(cb_api,
                          hostname,
                          agent="snapshot",
                          action="remove",
                          **kwargs)

    def _ping_and_restore_grub_cb(self, cb_api, node, agent, action, **kwargs):

        _, node_down = self._filter_reachable_nodes(cb_api, node)

        timeout = kwargs.pop('timeout', None)

        if node_down:
            log.trace.info("Node %s is down" % node)
        else:
            try:
                _, errors = self.ic_processor.execute_rpc_and_process_result(
                    cb_api, node, agent, action, kwargs, timeout,
                    extra_args=([2],)
                )

            except RpcExecutionException as e:
                raise CallbackExecutionException(e)

            if errors:
                raise CallbackExecutionException(
                                        ', '.join(reduce_errs(errors)))

    def _ping_and_remove_grub_cb(self, cb_api, hostname, **kwargs):

        if not self._is_reachable_node(cb_api, hostname):
            msg = 'Node "%s" not currently reachable. Continuing.' % hostname
            log.trace.debug(msg)
            log.trace.info(msg)
        else:
            self._remove_grub_cb(cb_api, hostname, **kwargs)

    def _remove_grub_cb(self, cb_api, hostname, **kwargs):
        self.__remove_rpc(cb_api,
                          hostname,
                          agent="snapshot",
                          action="remove_grub",
                          **kwargs)

    def _stop_service(self, callback_api, service, hostnames):
        if not hostnames:
            raise CallbackExecutionException("service {0} was requested to " \
                      "be stopped, but the node list is empty".format(service))
        call_args = ['service', service, 'stop', '-y']
        callback_api.rpc_application(hostnames, call_args)

    @staticmethod
    def _set_reboot_time(cb_api):
        log.trace.debug("Setting reboot_time")
        ss = cb_api.query('snapshot-base')[0]
        # set this to '' as this field is no longer relevant
        ss.rebooted_clusters = ''
        ss.reboot_issued_at = str(time.time())
        # the snapshot item will be in updated state now. We want it to be
        # in applied state, even if we just added a property
        ss._model_item.set_applied()

    def _restart_node(self, cb_api, node):
        log.trace.debug("Restarting node %s" % node)
        VolMgrPlugin._set_reboot_time(cb_api)
        reboot_node_force(cb_api, node)

    # TODO: >>> Driver specific code like this should not be in the plugin
    @staticmethod
    def _snappable_nodes(nodes, action, driver=''):
        if driver:
            nodes = [n for n in nodes \
                     if n.storage_profile is not None and \
                        n.storage_profile.volume_driver == driver]
        snappable = []
        for node in nodes:
            if action == "restore":
                if not node.is_initial():
                    snappable.append(node)
            else:
                if node.system and not node.system.is_initial():
                    snappable.append(node)

        return snappable

    @staticmethod
    def _get_tasks_hostnames(tasks):
        nodes = set([])

        for task in tasks:
            task_nodes_list = task.args[0]
            nodes |= set(task_nodes_list)
        return nodes

    def _wait_for_node_up(self, callback_api, hostname):
        timestamp = callback_api.query('snapshot-base')[0].reboot_issued_at
        wait_for_node_timestamp(callback_api, hostname, timestamp, False)

    def _wait_for_nodes_up(self, cb_api, nodes):

        reachable_nodes, _ = self._filter_reachable_nodes(cb_api, nodes)
        VolMgrPlugin._set_reboot_time(cb_api)
        for node in reachable_nodes:
            log.trace.debug("About to reboot %s" % node)
            reboot_node_force(cb_api, [node])
        for node in reachable_nodes:
            self._wait_for_node_up(cb_api, [node])

    def _vxvm_disk_uuid_update_cb(self, cb_api, itemname, disk_name):
        # No operation task
        msg = 'Executing task for UUID update of disk "%s" on %s' % \
              (disk_name, itemname)
        log.trace.info(msg)

    def _noop_grub_task_cb(self, cb_api, cluster):
        """
        This method is a stub and exists only for noop task in the plan to
        execute and set the cluster to Applied state.

        :param cb_api: Callback API context reference
        :param cluster: cluster ID
        """
        msg = 'Executing noop grub task to configure cluster "%s" ' \
              'into applied state' % (cluster)
        log.trace.info(msg)

    def _vx_diskgroup_setup(self, cb_api, disk_group_name, nodes, cluster):
        self.vxvm_driver.vx_disk_group_setup(cb_api,
                                             disk_group_name,
                                             nodes,
                                             cluster)

    def _vx_init_rpc_task(self, cb_api, nodes, agent, action, **kwargs):
        timeout, retries = kwargs.pop('timeout', None), \
                           kwargs.pop('retries', 0)

        disk_path = kwargs.pop('disk_name', None)
        if disk_path:
            disk = cb_api.query_by_vpath(disk_path)
            kwargs['disk_name'] = \
                VxvmDriver.get_disk_fact_name(disk.uuid)

            # If adding new disks to a volume group we need to find out the
            # active node. The active node will replace the node passed in
            # the arguments

            get_active_node = kwargs.pop('get_active_node', None)
            if get_active_node == 'true':
                vg_name = kwargs['disk_group']
                cluster_path = kwargs.pop('cluster_path', None)

                cluster = [c for c in cb_api.query('cluster')
                               if c.get_vpath() == cluster_path][0]

                try:
                    node, _ = self.vxvm_driver.get_dg_hostname(
                        cb_api,
                        cluster,
                        [vg_name])[vg_name]
                except DgNotImportedError as e:
                    raise PluginError(e)

                nodes = [node.hostname]
        else:
            disk_paths = kwargs.pop('disk_names', None)
            if not disk_paths:
                raise CallbackExecutionException("No disk name found "
                                                 "in task args")
            disks_names = []
            for disk_path in disk_paths:
                disk = cb_api.query_by_vpath(disk_path)
                disks_names.append(
                    VxvmDriver.get_disk_fact_name(disk.uuid))

            disks_names.sort()
            kwargs['disk_names'] = " ".join(disks_names)

        try:
            _, errors = self.base_processor.execute_rpc_and_process_result(
                           cb_api, nodes, agent,
                           action, kwargs, timeout, retries)
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def _wait_for_vxvm_restore_rpc(self, callback_api, nodes, **kwargs):
        max_wait = kwargs.pop('max_wait', 3600)
        disk_group = kwargs.pop('disk_group', None)
        volume_name = kwargs.pop('volume_name', None)
        if not disk_group or not volume_name:
            raise CallbackExecutionException(
                    "The disk group name or the volume name are missing"
                )
        epoch = int(time.time())
        while not self.vxvm_driver.check_restore_completed(callback_api,
                                                nodes[0], disk_group,
                                                volume_name):

            if not callback_api.is_running():
                raise PlanStoppedException("Plan execution has been stopped.")

            counter = int(time.time()) - epoch
            if counter % 60 == 0:
                log.trace.debug("Waiting for restore to complete")
            if counter >= max_wait:
                raise CallbackExecutionException(
                    "Restore has not completed within {0} seconds".format(
                        max_wait)
                )
            time.sleep(1.0)
