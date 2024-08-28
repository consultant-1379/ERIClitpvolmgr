##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

from litp.core.constants import UPGRADE_SNAPSHOT_NAME
from litp.core.task import OrderedTaskList
from litp.core.litp_logging import LitpLogger
from litp.core.execution_manager import (
    CallbackTask,
    PluginError,
    CallbackExecutionException
)
from litp.core.rpc_commands import (
    RpcExecutionException,
    RpcCommandOutputProcessor,
    RpcCommandProcessorBase,
    run_rpc_command,
    reduce_errs
)
from litp.core.validators import ValidationError
from litp.plan_types.create_snapshot import create_snapshot_tags
from litp.plan_types.remove_snapshot import remove_snapshot_tags
from litp.plan_types.restore_snapshot import restore_snapshot_tags
from volmgr_plugin.volmgr_utils import VolMgrUtils
from volmgr_plugin.drivers.driver import DriverBase
from volmgr_plugin.drivers.vxvm_errors import DgNotImportedError
from volmgr_plugin.drivers.vxvm_snapshot_data_mgr import VxvmSnapshotDataMgr

import math
import binascii
import json

log = LitpLogger()

VXVM_AGENT = "vxvm"
SNAPSHOT_AGENT = "vxvmsnapshot"


class VxvmSnapshotReport(object):
    """
        Container to collect broken snapshots and format error reporting
    """
    def __init__(self):
        self.snapshots = set()

    def add_snapshot(self, vg, fs):
        self.snapshots.add((vg.name, fs.id))

    def get_snapshots(self):
        return self.snapshots

    def get_snapshots_txt(self):
        msg = "Volume Group {0}, File-system {1}"
        messages = [msg.format(vg_name, fs_id)
                    for (vg_name, fs_id) in self.snapshots]
        return ', '.join(messages)

    def as_string(self):
        return "%s(%s)" % (self.__class__.__name__, self.get_snapshots_txt())

    def __str__(self):
        return self.as_string()

    def __repr__(self):
        return self.as_string()


class VxvmDriver(DriverBase):

    """
    LITP VxVm Driver
    """

    @staticmethod
    def get_disk_fact_name(disk):
        # Use serial number from the model (disk.uuid)
        # to get the fact disk name

        disk_fact_name = 'wxvm_disk_' + disk.lower().replace('-', '_')
        disk_fact_name += '_dev'
        return disk_fact_name

    @staticmethod
    def _generate_vx_name(vg, fs):
        return "_".join((vg.volume_group_name, fs.item_id))

    @staticmethod
    def _get_disk_from_cluster_node(cluster, pd):
        """
        Returns a disk from vxvm storage profile
        """

        for node in cluster.nodes:
            for disk in VolMgrUtils.system_disks(node):
                if pd.device_name == disk.name:
                    return disk

    @staticmethod
    def _get_all_updated_disks_from_cluster_node(cluster, pd):
        """
        Returns all disks from vxvm storage profile
        """
        disks = []
        for node in cluster.nodes:
            for disk in VolMgrUtils.system_disks(node):
                if (pd.device_name == disk.name) and disk.is_updated():
                    disks.append(disk)
        return disks

    @staticmethod
    def _condense_name(name, size=60):
        """
        A method to condense a string, but still keep the uniqueness of the
        string. The method removes the last 10 characters of the string, and
        replaces it with the crc32 hex representation of the entire string. It
        is called to ensure all VCS group and resource names are 60 characters
        or less, to improve readability for the user.

        Note: This same method must go into both vcs and vol manager plugins!
        """
        if len(name) > size - 1:
            name = name[:size - 10] + '_' + \
                hex(binascii.crc32(name) & 0xffffffff).lstrip('0x').rstrip('L')
            # . and - are unsupported characters in VCS naming
        return name.replace(".", "_").replace("-", "_")

    @staticmethod
    def _compute_total_disk_size(disks):
        return sum(
            [VolMgrUtils.get_size_megabytes(disk.size) for disk in disks]
        )

    @staticmethod
    def _compute_file_systems_size(volume_group):
        return sum(
            [VolMgrUtils.get_size_megabytes(fs.size)
             for fs in volume_group.file_systems]
        )

    @staticmethod
    def _compute_snapshots_size(volume_group):
        return sum(
            [VxvmDriver._effective_snapshot_size(fs)
             for fs in volume_group.file_systems]
        )

    @staticmethod
    def _compute_volume_group_size(vg):
        return sum(
            [VxvmDriver._effective_fs_size(fs) for fs in vg.file_systems]
        )

    @staticmethod
    def _effective_snapshot_size(fs):
        size = VolMgrUtils.get_size_megabytes(fs.size)
        return int(math.ceil((size * (1. + int(fs.snap_size))) / 100))

    @staticmethod
    def _effective_fs_size(fs):
        return VolMgrUtils.get_size_megabytes(fs.size) + \
            VxvmDriver._effective_snapshot_size(fs)

    @staticmethod
    def _only_disk_uuid_changed(disk):

        if not disk.is_updated() or \
           not hasattr(disk, 'uuid'):
            return False

        # Check all applied properties against current properties
        applied_property_names = disk.applied_properties.keys()

        if 'uuid' not in applied_property_names:
            return False

        for applied_property_name in applied_property_names:
            if applied_property_name == 'uuid':
                continue

            if hasattr(disk, applied_property_name) and \
               getattr(disk, applied_property_name, None) and \
               disk.applied_properties.get(applied_property_name) != \
                                 getattr(disk, applied_property_name, None):
                return False

        # Check all current properties against applied properties
        current_property_names = disk.properties.keys()

        for current_property_name in current_property_names:
            if current_property_name == 'uuid':
                continue

            if current_property_name in applied_property_names and\
               getattr(disk, current_property_name, None) and \
               disk.applied_properties.get(current_property_name) != \
                                 getattr(disk, current_property_name, None):
                return False

        # No other discernible property change thus far ...

        # Special case: has the UUID actually changed?
        if disk.uuid and disk.applied_properties.get('uuid') and \
           disk.uuid != disk.applied_properties.get('uuid'):
            return True

        return False

    def _gen_noop_task_for_disk_update(self, disk, itemname):
        return CallbackTask(disk,
                            'Configure UUID on VxVM disk "%s"' % \
                              disk.name,
                            self.rpc_callbacks['vxvm_uuid_updated'],
                            itemname,
                            disk.name)

    def gen_noop_tasks_for_disk_updates(self, cluster):
        tasks = []

        cluster_device_names = [pd.device_name
                                for sp in cluster.storage_profile
                                for vg in sp.volume_groups
                                for pd in vg.physical_devices]

        for node in cluster.nodes:
            uuid_updated_disks = [disk for disk in node.system.disks
                                  if disk.name in cluster_device_names and
                                     VxvmDriver._only_disk_uuid_changed(disk)]

            for disk in uuid_updated_disks:
                task = self._gen_noop_task_for_disk_update(disk,
                                               'node "%s"' % node.hostname)
                tasks.append(task)

        return tasks

    def gen_tasks_for_storage_profile(self, context, cluster, storage_profile):

        tasks = []
        node = self._get_cluster_node(cluster)

        for vg in storage_profile.volume_groups:
            tasks += self.resize_disk_group_tasks(vg, cluster)

            if vg.is_initial():
                tasks += self.initialize_vg_tasks(node,
                                                  vg,
                                                  cluster,
                                                  context)
            if vg.is_for_removal():
                tasks += self.destroy_vg_tasks(node,
                                               vg,
                                               storage_profile,
                                               cluster)

            if vg.is_applied():
                new_pds = [pd for pd in vg.physical_devices if pd.is_initial()]
                if new_pds:
                    tasks += self._add_new_disks_tasks(node,
                                                      vg,
                                                      new_pds,
                                                      cluster,
                                                      context)

            for fs in vg.file_systems:

                if vg.is_applied() and fs.is_initial():
                    try:
                        node, _ = self.get_dg_hostname(
                            context,
                            cluster,
                            [vg.volume_group_name])[vg.volume_group_name]
                    except DgNotImportedError as e:
                        raise PluginError(e)

                if vg.is_initial() or vg.is_applied():
                    if fs.is_initial():
                        tasks += self.initialize_volume_tasks(node,
                                                        vg.volume_group_name,
                                                        fs)
                    elif VolMgrUtils._item_property_has_changed(fs, 'size'):
                        resize_task = self._resize_file_system_task(cluster,
                                                    vg.volume_group_name,
                                                    fs)
                        service = VxvmDriver.get_cs_for_fs(cluster, fs)
                        if service and self.is_service_for_redeploy(service):
                            # If APD=False on the service, let it run first
                            resize_task.requires.add(service)
                        tasks.append(resize_task)
        if tasks:
            return [OrderedTaskList(cluster.software, tasks)]
        else:
            return tasks

# Note:  The function name here is open for debate.
# If resizing the disk, it needs to come after the service when its online.
    def is_service_for_redeploy(self, service):
        """
        Checks to see if the service is getting redeploy"
        """
        if service.is_initial() or service.is_for_removal():
            return False
        if not service.applied_properties_determinable or \
               self.does_service_need_to_be_migrated(service):
            return True

        return False

    def does_service_need_to_be_migrated(self, service):
        """
        Checks if the service is for migration or
        expansion/contraction.
        If node_list is a subset or superset of the applied_node
        list, then its for expansion/contraction.
        Otherwise returns True if its for migration
        """
        if service.applied_properties.get('node_list'):
            applied_nodes = \
            service.applied_properties.get('node_list').split(",")
            nodes = set(service.node_list.split(','))
            if (not nodes.issubset(applied_nodes)
                and not nodes.issuperset(applied_nodes)):
                return True
        return False

    @staticmethod
    def get_cs_for_fs(cluster, fs):
        """
        Returns the `vcs-clustered-service` item that uses a VxVM fs
        """
        for service in cluster.query('vcs-clustered-service'):
            for _fs in service.filesystems:
                # Check that the filesystem under the clustered-service was
                # inherited from the cluster-level storage-profile
                if _fs.get_source().vpath == fs.vpath:
                    # VCS plugin has a restriction that a VxVM volume can only
                    # be managed by one, and only one, vcs-clustered-service
                    return service
        return None

    @staticmethod
    def is_fs_in_snap_list(fs, snap_list, tag):
        """
        Check if fs in snap list.
        Return exact name of snap found.
        """
        func_set = (VolMgrUtils.gen_snapshot_name,
                    VolMgrUtils._gen_cache_object_name,
                    VolMgrUtils._gen_cache_volume_name)

        names = []
        for f, k in zip(func_set, ('snaps', 'co', 'cv')):
            names.append(f(fs.item_id, tag))
            if names[-1] in snap_list[k]:
                return (True, names[0])
        return False, None

    @staticmethod
    def _is_openstack_env(context):
        """
        Return True if the deployment type is openstack.
        :param context: An instance of PluginApiContext.
        :type context: litp.core.plugin_context_api.PluginApiContext
        :return: True or False
        :rtype: boolean
        """

        return any([('enm_deployment_type' == gprop.key and
                     'vLITP_ENM_On_Rack_Servers' == gprop.value)
                    for cmngr in context.query('config-manager')
                    for gprop in cmngr.global_properties])

    def _gen_setup_disks_tasks(self, node, volume_group, disks, cluster,
                               context):
        """
        Return tasks for generating setup disks.
        :param node: Node to apply tasks to.
        :type node: LitpObject
        :param volume_group: Volume group to apply tasks to.
        :type volume_group: LitpObject
        :param disks: List of disks to generate tasks for.
        :type disks: List of LitpObjects
        :param cluster: Cluster to get disks from.
        :type cluster: LitpObject
        :param context: An instance of PluginApiContext.
        :type context: litp.core.plugin_context_api.PluginApiContext
        :return: List of tasks
        :rtype: List
        """

        tasks = []

        for disk in disks:

            preamble = '._setup_disks_tasks: %s : ' % disk.name
            log.trace.debug(preamble + "Generating tasks for PD")

            pd = VolMgrUtils.get_pd_for_disk(volume_group, disk)
            if self._is_openstack_env(context):
                tasks.extend(
                    [
                        self._setup_disk_task(
                            node, disk, volume_group, pd, cluster
                        )
                    ]
                )
                continue
            tasks.extend(
                [
                    self._clear_SCSI3_keys(
                        node, disk, volume_group, pd, cluster
                    ),
                    self._setup_disk_task(
                        node, disk, volume_group, pd, cluster
                    )
                ]
            )
        return tasks

    def _add_new_disks_tasks(self, node, volume_group, new_pds, cluster,
                             context):
        """
        Return tasks for adding new disks.
        :param node: Node to apply tasks to.
        :type node: LitpObject
        :param volume_group: Volume group to apply tasks to.
        :type volume_group: LitpObject
        :param new_pds: Physical devices
        :type new_pds: LitpObject
        :param cluster: Cluster to get disks from.
        :type cluster: LitpObject
        :param context: An instance of PluginApiContext.
        :type context: litp.core.plugin_context_api.PluginApiContext
        :return: List of tasks
        :rtype: List
        """

        preamble = '._add_new_disks_tasks: %s : ' % \
                   volume_group.volume_group_name

        tasks = []
        disks = [VxvmDriver._get_disk_from_cluster_node(cluster, pd)
                 for pd in new_pds]

        tasks.extend(
            self._gen_setup_disks_tasks(
                node, volume_group, disks, cluster, context
            )
        )

        log.trace.debug(preamble + "Generating tasks for disk group")
        for pd in new_pds:
            disk = self._get_disk_from_cluster_node(cluster, pd)
            tasks.append(
                self._add_disk_to_group_task(
                    node,
                    volume_group,
                    disk,
                    cluster.get_vpath(),
                    pd
                )
            )
        return tasks

    def initialize_vg_tasks(self, node, volume_group, cluster,
                            context):
        """
        Return tasks for initialising volume groups.
        :param node: Node to apply tasks to.
        :type node: LitpObject
        :param volume_group: Volume group to apply tasks to.
        :type volume_group: LitpObject
        :param cluster: Cluster to get disks from.
        :type cluster: LitpObject
        :param context: An instance of PluginApiContext.
        :type context: litp.core.plugin_context_api.PluginApiContext
        :return: List of tasks
        :rtype: List
        """

        preamble = '.initialize_vg_tasks: %s : ' % \
                   volume_group.volume_group_name

        vg_tasks = []
        disks = [VxvmDriver._get_disk_from_cluster_node(cluster, pd) for
                 pd in VolMgrUtils.vg_pds(volume_group)]

        vg_tasks.extend(
            self._gen_setup_disks_tasks(
                node,
                volume_group,
                disks,
                cluster,
                context
            )
        )

        log.trace.debug(preamble + "Generating tasks for disk group")
        vg_tasks.append(
            self._create_disk_group_task(
                node,
                volume_group,
                disks
            )
        )
        return vg_tasks

    def resize_disk_group_tasks(self, volume_group, cluster):

        preamble = '.resize_disk_group_tasks: %s : ' % \
                   volume_group.volume_group_name
        log.trace.debug(preamble + "Generating tasks for disk group")

        tasks = []
        disks = [VxvmDriver._get_all_updated_disks_from_cluster_node(
                    cluster,
                    pd
                   ) for pd in \
                VolMgrUtils.vg_pds(volume_group) if pd.is_applied()]

        # disks is a sequence of type [[lun0, lun0, lun0], [..]]
        # so we can count the sequence to reliably tell how many pds we have
        use_force_flag = (len(disks) < 2)

        for disk_set in disks:
            if (len(disk_set) > 0) and \
                    VolMgrUtils.get_size_megabytes(disk_set[0].size) \
                    > VolMgrUtils.get_size_megabytes(
                        disk_set[0].applied_properties['size']):
                tasks.append(
                    self._resize_disk_task(
                        cluster,
                        volume_group,
                        disk_set,
                        use_force_flag
                    )
                )

        return tasks

    def destroy_vg_tasks(self, node, volume_group, storage_profile, cluster):

        preamble = '.destroy_vg_tasks: %s : ' % volume_group.volume_group_name

        vg_tasks = list()

        vg_tasks.append(self._stop_volumes_task(node,
                                            volume_group.volume_group_name,
                                            storage_profile))

        log.trace.debug(preamble + "Generating tasks to destroy disk group")
        vg_tasks.append(self._destroy_disk_group_task(node,
                                              volume_group.volume_group_name,
                                              storage_profile))

        for pd in volume_group.physical_devices:
            disk = VxvmDriver._get_disk_from_cluster_node(cluster, pd)

            fact_disk_name = VxvmDriver.get_disk_fact_name(disk.uuid)
            log.trace.debug(preamble + "Generating tasks to stop all volumes")

            log.trace.debug(preamble + "Generating tasks for PD")
            vg_tasks.append(self._unsetup_disk_task(node,
                                                    fact_disk_name,
                                                    disk,
                                                    storage_profile))

        return vg_tasks

    def initialize_volume_tasks(self, node, vg_name, fs):

        preamble = '.initialize_volume_tasks: %s : ' % fs.item_id
        log.trace.debug(preamble + "Generating tasks to initialise volume")

        vol_tasks = []

        vol_tasks.append(self._create_volume_task(node,
                                                  vg_name,
                                                  fs.item_id,
                                                  fs.size,
                                                  fs))

        log.trace.debug(preamble + "Generating tasks for filesystem ")
        vol_tasks.append(self._create_filesystem_task(node,
                                                      vg_name,
                                                      fs.item_id,
                                                      fs))

        log.trace.debug(preamble + "Generating tasks to prepare volume")
        vol_tasks.append(self._prepare_volume_task(node,
                                                   vg_name,
                                                   fs.item_id,
                                                   fs))
        return vol_tasks

    def _get_cluster_node(self, cluster):
        """
        Returns first node from vcs cluster
        """
        for node in cluster.nodes:
            return node

    def _create_disk_group_task(self, node, vg, disks):
        '''
        Create the callback task to create disk group
        '''

        return CallbackTask(vg,
                            'Create VxVM disk group "%s" on node "%s"' %
                             (vg.volume_group_name, node.hostname),
                            self.rpc_callbacks['vx_init'],
                            [node.hostname],
                            VXVM_AGENT,
                            'create_disk_group',
                            timeout=299,
                            disk_group=vg.volume_group_name,
                            disk_names=[disk.get_vpath() for disk in disks])

    def _add_disk_to_group_task(self, node, vg, disk,
                                    cluster_path, pd):
        '''
        Create the callback task to extend disk group
        '''

        cb_task = CallbackTask(pd,
            'Extend VxVM disk group "%s" on node "%s" with new disk "%s"' %
            (vg.volume_group_name, node.hostname, disk.name),
            self.rpc_callbacks['vx_init'],
            [node.hostname],
            VXVM_AGENT,
            'add_disk_to_group',
            timeout=299,
            disk_group=vg.volume_group_name,
            disk_name=disk.get_vpath(),
            get_active_node='true',
            cluster_path=cluster_path)
        cb_task.model_items.add(vg)
        cb_task.model_items.add(disk)

        return cb_task

    def _destroy_disk_group_task(self, node, vg_name, storage_profile):
        '''
        Create the callback task to destroy disk group
        '''

        return CallbackTask(storage_profile,
                            'Destroy VxVM disk group "%s" on node "%s"' %
                             (vg_name, node.hostname),
                            self.rpc_callbacks['base'],
                            [node.hostname],
                            VXVM_AGENT,
                            'destroy_disk_group',
                            disk_group=vg_name)

    def _stop_volumes_task(self, node, vg_name, storage_profile):
        """
        Create the callback task for stop all
        disk group volumes
        """

        return CallbackTask(
            storage_profile,
            'Stop VxVM volumes from disk group "%s" on node "%s"' %
            (vg_name, node.hostname),
            self.rpc_callbacks['base'],
            [node.hostname],
            VXVM_AGENT,
            'stop_volumes',
            disk_group=vg_name
        )

    @staticmethod
    def _add_fencing_disk_requirement(task, fencing_disks):
        for disk in fencing_disks:
            task.requires.add(disk)

    def _clear_SCSI3_keys(self, node, disk, volume_group, pd, cluster):
        """
        Create the callback task for removal of any
        SCSI-3 Registration keys from disk
        """

        task = CallbackTask(
            pd,
            'Clear SCSI-3 registration keys from disk "%s" on node "%s"' %
            (disk.name, node.hostname),
            self.rpc_callbacks['vx_init'],
            [node.hostname],
            VXVM_AGENT,
            'clear_keys',
            timeout=299,
            disk_name=disk.get_vpath(),
        )
        # LITPCDS-6163: Ensure Fencing Disk tasks are done before this one
        VxvmDriver._add_fencing_disk_requirement(task, cluster.fencing_disks)

        task.model_items.add(disk)
        task.model_items.add(volume_group)

        return task

    def _setup_disk_task(self, node, disk, vg, pd, cluster):
        """
        Create the callback task for prepare
        disk for vxvm
        """
        task = CallbackTask(
            pd,
            'Setup VxVM disk "%s" on node "%s"' %
            (disk.name, node.hostname),
            self.rpc_callbacks['vx_init'],
            [node.hostname],
            VXVM_AGENT,
            'setup_disk',
            timeout=299,
            disk_name=disk.get_vpath(),
            disk_group=vg.volume_group_name,
        )
        # LITPCDS-6163: Ensure Fencing Disk tasks are done before this one
        VxvmDriver._add_fencing_disk_requirement(task, cluster.fencing_disks)
        task.model_items.add(disk)
        task.model_items.add(vg)
        return task

    def _unsetup_disk_task(self, node, fact_disk_name, disk, storage_profile):
        '''
        Create the callback task for unsetup
        disk for vxvm
        '''
        return CallbackTask(
            storage_profile,
            'Unsetup VxVM disk "%s" on node "%s"' %
            (disk.name, node.hostname),
            self.rpc_callbacks['base'],
            [node.hostname],
            VXVM_AGENT,
            'unsetup_disk',
            timeout=299,
            disk_name=fact_disk_name
        )

    def _create_volume_task(self, node, vg_name, volume_name, volume_size,
                            fs):
        '''
        Create the callback task for prepare
        disk for vxvm
        '''

        return CallbackTask(
            fs,
            'Create VxVM volume on node "%s" - VOL: "%s", VG: "%s"' %
            (node.hostname, volume_name, vg_name),
            self.rpc_callbacks['base'],
            [node.hostname],
            VXVM_AGENT,
            'create_volume',
            disk_group=vg_name,
            volume_name=volume_name,
            volume_size=volume_size,
        )

    def _prepare_volume_task(self, node, vg_name, volume_name,
                             fs):
        '''
        Create the callback task for prepare
        disk for vxvm
        '''

        return CallbackTask(
            fs,
            'Prepare VxVM volume for snapshot '
            'on node "%s" - VOL: "%s", VG: "%s"' %
            (node.hostname, volume_name, vg_name),
            self.rpc_callbacks['base'],
            [node.hostname],
            VXVM_AGENT,
            'prepare_volume',
            disk_group=vg_name,
            volume_name=volume_name,
        )

    def _resize_file_system_task(self, cluster, vg_name, fs):
        '''
        Create the callback task for resizing both the vxvm volume and the
        vxfs filesystem
        '''
        preamble = '_resize_file_system_task: %s : ' % fs.item_id
        log.trace.debug(preamble)
        return CallbackTask(
            fs,
            'Resize VxFS file system in cluster "%s" - FS: "%s", VG: "%s" ' \
            'to "%s"' % (cluster.item_id, fs.item_id, vg_name, fs.size),
            self.rpc_callbacks['base_with_imported_dg'],
            cluster.vpath,
            VXVM_AGENT,
            'resize_volume',
            vg_name,
            disk_group=vg_name,
            volume_name=fs.item_id,
            volume_size=fs.size,
            ignore_unreachable=True
        )

    def _resize_disk_task(self, cluster, vg, disk_set, use_force_flag):
        '''
        Create the callback task for resizing the VxVM disk in the disk group
        '''
        preamble = '_resize_disk_task: %s : ' % disk_set[0].name
        log.trace.debug(preamble)
        disk_fact_name = VxvmDriver.get_disk_fact_name(disk_set[0].uuid)
        task = CallbackTask(
            vg,
            'Resize VxVM disk on volume group "%s" - Disk: "%s"' \
            % (vg.volume_group_name, disk_set[0].name),
            self.rpc_callbacks['base_with_imported_dg'],
            cluster.vpath,
            VXVM_AGENT,
            'resize_disk',
            vg.volume_group_name,
            disk_group=vg.volume_group_name,
            disk_name=disk_fact_name,
            force_flag=str(use_force_flag),
            ignore_unreachable=True,
            timeout=299
        )
        for disk in disk_set:
            task.model_items.add(disk)
        return task

    def _create_filesystem_task(self, node, vg_name, volume_name,
                                fs):
        '''
        Create the callback task for creating
        vxfs filesystem
        '''

        return CallbackTask(
            fs,
            'Create VxFS file system on node "%s" - FS: "%s", VG: "%s"' %
            (node.hostname, volume_name, vg_name),
            self.rpc_callbacks['base'],
            [node.hostname],
            VXVM_AGENT,
            'create_filesystem',
            disk_group=vg_name,
            volume_name=volume_name,
            timeout=300
        )

    # >>>  Entry point in the VXVM driver for manipulating snapshots

    def gen_tasks_for_snapshot(self, context):
        '''
        Entry point for processing snapshots in VxVM driver,
        called from the volmgr plugin.
        '''

        action = context.snapshot_action()

        if action == 'create':
            return self._create_snapshot_tasks(context)
        elif action == 'remove':
            return self._remove_snapshot_tasks(context)
        elif action == 'restore':
            return self._restore_snapshot_tasks(context)
        return []

    def _create_snapshot_tasks(self, context):

        tasks = []

        data_manager = VxvmSnapshotDataMgr(driver=self, context=context)
        vgs = data_manager.get_all_volume_groups()

        if not vgs:
            return tasks
        for vg in vgs:
            for fs in vg.file_systems:
                task = self._create_snapshot_task(context, data_manager,
                                                  vg, fs)
                tasks.append(task)
        return tasks

    def _remove_snapshot_tasks(self, context):
        '''
        Generate remove snapshot tasks for VxVM Volume Groups
        '''
        tasks = []

        data_manager = VxvmSnapshotDataMgr(driver=self, context=context)

        has_snappable_vgs = self._has_snappable_file_systems(data_manager)

        if not has_snappable_vgs:
            return tasks

        check_active_node = self._gen_check_active_nodes_task(context,
                                                              data_manager,
                                                              'remove')
        if check_active_node:
            tasks.append(check_active_node)

        tasks.append(self._gen_check_restores_in_progress_task(context,
                                                          data_manager,
                                                             'remove'))

        desc_template = 'Remove VxVM {0} snapshot "{1}" for ' + \
                        'cluster "{2}", volume group "{3}"'
        cb_key = 'remove_snapshot'

        for vg in data_manager.get_all_volume_groups():
            log.trace.debug("Volume_group: {0}".format(vg.name))
            for fs in vg.file_systems:
                desc = desc_template.format(
                    self._get_snapshot_type(self.get_snapshot_tag(context)),
                    fs.snapshot_name,
                    vg.cluster_id,
                    vg.name)
                fs_filter = {'vg': vg.id, 'fs': fs.id}
                task = self._gen_task_worker(context, data_manager,
                                             desc, cb_key,
                                             'remove', fs_filter)
                tasks.append(task)
        return tasks

    def _get_cb_task_item(self, context):
        snaps = context.query('snapshot-base', active='true')
        if snaps:
            return snaps[0]
        return None

    def _gen_task_worker(self, context, data_manager,
                         desc, cb_key, action, fs_filter=None, tag=None):
        preamble = '_gen_task_worker: %s: tag: %s ' % (action, tag)
        log.trace.debug(preamble + "CB key: %s: %s" % (cb_key, desc))

        if action == 'restore' and tag == None:
            tag = restore_snapshot_tags.PEER_NODE_VXVM_VOLUME_TAG
        elif action == 'remove' and tag == None:
            tag = remove_snapshot_tags.PEER_NODE_VXVM_VOLUME_TAG

        log.trace.debug(preamble + "Task-Sort tag: " + tag)
        task_item = self._get_cb_task_item(context)

        if not fs_filter:
            return CallbackTask(task_item,
                                desc,
                                self.rpc_callbacks[cb_key],
                                data_set=data_manager.get_data_set(),
                                tag_name=tag)
        else:
            log.trace.debug(preamble + ("Filter: %s" % fs_filter))
            return CallbackTask(task_item,
                                desc,
                                self.rpc_callbacks[cb_key],
                                data_set=data_manager.get_data_set(True),
                                fs_filter=fs_filter,
                                tag_name=tag)

    def _gen_check_active_nodes_task(self, context, data_manager, action):

        cluster_ids = data_manager.get_cluster_ids()

        if context.is_snapshot_action_forced() or not cluster_ids:
            return None

        cls_list = VolMgrUtils.format_list([cid for cid in cluster_ids],
                                           quotes_char='double')

        cb_key = 'check_active_nodes'
        tag = None

        if action == 'restore':
            desc = 'Check that all nodes are reachable and an active node ' + \
                'exists for each VxVM volume group on cluster(s) %s' % cls_list
            tag = restore_snapshot_tags.VALIDATION_TAG
        elif action == 'remove':
            desc = 'Check that an active node exists for each VxVM volume ' + \
                                            'group on cluster(s) %s' % cls_list
            tag = remove_snapshot_tags.VALIDATION_TAG
        return self._gen_task_worker(context, data_manager,
                                     desc, cb_key, action, tag=tag)

    def _gen_check_presence_task(self, context, data_manager, action):

        if context.is_snapshot_action_forced():
            return None
        tag = None

        if action == 'restore':
            tag = restore_snapshot_tags.VALIDATION_TAG
        elif action == 'remove':
            tag = remove_snapshot_tags.VALIDATION_TAG
        cls_list = VolMgrUtils.format_list(
                [cluster for cluster in data_manager.get_cluster_ids()],
                                                    quotes_char='double'
            )
        desc = 'Check VxVM snapshots are present on cluster(s) %s' % cls_list
        cb_key = 'check_presence'
        return self._gen_task_worker(context, data_manager,
                                     desc, cb_key, action, tag=tag)

    def _gen_check_validity_task(self, context, data_manager, action):
        cls_list = VolMgrUtils.format_list(
                [cluster for cluster in data_manager.get_cluster_ids()],
                                                    quotes_char='double'
            )
        desc = 'Check VxVM snapshots are valid on cluster(s) %s' % cls_list
        cb_key = 'check_validity'
        tag = None

        if action == 'restore':
            tag = restore_snapshot_tags.VALIDATION_TAG
        elif action == 'remove':
            tag = remove_snapshot_tags.VALIDATION_TAG
        return self._gen_task_worker(context, data_manager,
                                     desc, cb_key, action, tag=tag)

    def _gen_check_restores_in_progress_task(self, context,
                                             data_manager, action):
        desc = 'Check VxVM snapshots are currently not being restored'
        cb_key = 'check_restores_in_progress'
        tag = remove_snapshot_tags.VALIDATION_TAG
        return self._gen_task_worker(context, data_manager, desc,
                                     cb_key, action, tag=tag)

    def _restore_snapshot_tasks(self, context):
        '''
        Iterate through VCS clusters and examine each VxVM storage-profile
        '''

        tasks = []
        check_presence_task = None

        data_manager = VxvmSnapshotDataMgr(driver=self, context=context)
        action_forced = data_manager.get_action_forced()

        has_snappable_vgs = self._has_snappable_file_systems(data_manager)

        if not has_snappable_vgs:
            return tasks

        check_active_task = self._gen_check_active_nodes_task(context,
                                                         data_manager,
                                                            'restore')
        if check_active_task:
            tasks.append(check_active_task)

        if not action_forced:
            check_presence_task = self._gen_check_presence_task(context,
                                                            data_manager,
                                                            'restore')
        if check_presence_task:
            tasks.append(check_presence_task)

        tasks.append(self._gen_check_validity_task(context,
                                                   data_manager,
                                                   'restore'))
        desc_template = 'Restore VxVM {0} snapshot "{1}" ' + \
                        'for cluster "{2}", volume group "{3}"'
        cb_key = 'restore_snapshot'

        for vg in data_manager.get_all_volume_groups():
            log.trace.debug("Volume_group: %s" % vg.name)
            for fs in vg.file_systems:
                desc = desc_template.format(
                    self._get_snapshot_type(self.get_snapshot_tag(context)),
                    fs.snapshot_name,
                    vg.cluster_id,
                    vg.name)
                fs_filter = {'vg': vg.id, 'fs': fs.id}
                task = self._gen_task_worker(context, data_manager,
                                             desc, cb_key,
                                             'restore', fs_filter)
                if task:
                    tasks.append(task)

        return tasks

    def _create_snapshot_task(self, context, data_manager, vg, fs):
        '''
        Create the CallbackTask to create a vxfs snapshot
        '''
        tag_name = create_snapshot_tags.PEER_NODE_VXVM_VOLUME_TAG
        log.trace.info("Snapshot task created for VG {0},  FS {1}".format(
                        vg.name, fs.id))

        # The node where we are running the task is not known yet.
        # It will be resolved at run time, back in the callback.

        fs_filter = {'vg': vg.id, 'fs': fs.id}
        data_set = data_manager.get_data_set(make_deep_copy=True)

        cb_key = 'create_snapshot'
        desc = 'Create VxVM {0} snapshot "{1}" for cluster "{2}", '\
               'volume group "{3}"'
        desc = desc.format(
            self._get_snapshot_type(self.get_snapshot_tag(context)),
            fs.snapshot_name,
            vg.cluster_id,
            vg.name)
        task_item = self._get_cb_task_item(context)
        return CallbackTask(task_item,
                            desc,
                            self.rpc_callbacks[cb_key],
                            data_set=data_set,
                            fs_filter=fs_filter,
                            tag_name=tag_name)

    @staticmethod
    def _snapshot_by_volmgr(fs):
        '''
        Check whether fs is marked for manual snap-shotting via other plugins.
        Returns True if volmgr should generate the snapshot tasks
        '''
        return fs.snap_external == 'false' and fs.current_snap_size != '0'

    @staticmethod
    def _has_snappable_file_systems(data_manager):
        '''
        Check if any any file-system in data manager is snappable
        '''
        return any(vg.has_snappable_file_systems() for vg
                   in data_manager.get_all_volume_groups())

    def _should_snapshot_fs(self, fs, vg, name=UPGRADE_SNAPSHOT_NAME):
        '''
        Check snapshot criteria for creating the snapshot
        Returns true if we should create the snapshot for the fs
        '''
        return not fs.is_initial() and \
            self._snapshot_by_volmgr(fs) and \
            float(fs.current_snap_size) > 0 and \
            self._vg_snap_operation_allowed_in_san_aspect(None, vg, name)

    def _can_delete_or_restore_snapshot(
            self, vg, fs, name=UPGRADE_SNAPSHOT_NAME):
        '''
        Check snapshot criteria for deleting or restoring a snapshot
        Returns true if we may delete or restore the snapshot
        '''
        return (fs.item_id is not None) \
            and VxvmDriver._snapshot_by_volmgr(fs) \
            and (vg.volume_group_name is not None) \
            and not fs.is_initial() \
            and self._vg_snap_operation_allowed_in_san_aspect(None, vg, name)

    def _validate_vg_size_against_disks(self, cluster, node, vg):
        """
        Validate the File System for a given Volume Group
        will fit on the nominated System Disks.
        """
        preamble = '_validate_vg_size_against_disk: Cluster: %s VG:%s ' % \
                   (cluster.item_id, vg.volume_group_name)

        # get all disks in the volume group that are not for removal
        all_disks = [VolMgrUtils.get_node_disk_for_pd(node, pd) for
                   pd in vg.physical_devices if not pd.is_for_removal()]

        disks = [disk for disk in all_disks if disk is not None]

        if disks:

            disks_str = VolMgrUtils.format_list(
                [disk.item_id for disk in disks]
            )

            log.trace.debug(
                (preamble + "Will validate %s against %s") %
                (vg.volume_group_name, disks_str)
            )

            # Case 1: the volume group has physical disks associated with it
            #         calculate the vg and disk sizes and detect errors
            fs_total_size = VxvmDriver._compute_file_systems_size(vg)
            ss_total_size = VxvmDriver._compute_snapshots_size(vg)
            vg_total_size = VxvmDriver._compute_volume_group_size(vg)
            disks_total_size = VxvmDriver._compute_total_disk_size(disks)

            if vg_total_size > disks_total_size:
                message = (
                    "The total size ({disk_size} MB) of the disk(s) "
                    "connected to cluster '{cluster_name}' is not large "
                    "enough for volume group total requirement "
                    "({vg_total} MB), containing file systems {fs_total} MB "
                    "and snapshots {ss_total} MB"
                ).format(
                    cluster_name=cluster.item_id,
                    disk_size=disks_total_size,
                    fs_total=fs_total_size,
                    ss_total=ss_total_size,
                    vg_total=vg_total_size
                )
                log.trace.debug(preamble + message)
                return ValidationError(
                    item_path=vg.get_vpath(),
                    error_message=message
                )
        return None

    def validate_for_removal_vg(self, profile, api_context):
        errors = []
        snapshots = api_context.query('snapshot-base')
        if snapshots:
            for group in [vg for vg in profile.volume_groups
                          if vg.is_for_removal()]:
                snap_string = 'a snapshot exists'
                if len(snapshots) > 1:
                    snap_string = 'snapshots exist'
                msg = ('Cannot remove {item_type} "{item_type_name}" '
                       'because {snapshot_exists}.'.format(
                            item_type=group.item_type_id,
                            item_type_name=group.volume_group_name,
                            snapshot_exists=snap_string)
                       )
                errors.append(
                    ValidationError(
                        item_path=vg.get_vpath(), error_message=msg
                    )
                )
        return errors

    def validate_disk_sizes(self, cluster):
        """
        Validate that the Volume Groups can fit on the
        nominated system disks
        """
        node = self._get_cluster_node(cluster)
        errors = []

        for profile in cluster.query("storage-profile", volume_driver='vxvm'):
            for vg in profile.volume_groups:
                error = self._validate_vg_size_against_disks(cluster, node, vg)
                if error:
                    errors.append(error)

            break   # Added for CR LITPCDS-10895

        return errors

    def validate_pd_to_sp_mapping(self, api_context):
        """
        LITPCDS-10977 - Validate that each physical-device is referenced by
        only one storage-profile, i.e. that there is a one-to-one mapping
        between physical-devices and storage-devices.
        """
        preamble = 'validate_pd_to_sp_mapping'

        storage_profiles = \
            api_context.query('storage-profile', volume_driver='vxvm')
        physical_devices = \
            api_context.query('physical-device')

        errors = []

        log.trace.debug(
            '%s - ensure 1-to-1 mapping between PDs and SPs' % preamble
        )

        for pd in physical_devices:
            occurs_in = []
            for sp in storage_profiles:
                for vg in sp.volume_groups:
                    vg_pd_names = [
                        v.device_name for v in VolMgrUtils.vg_pds(vg)
                        ]
                    if pd.device_name in vg_pd_names:
                        occurs_in.append(sp.item_id)

            # There may be zero or one occurrences of the physical device in
            # the volume groups of the storage profiles, otherwise this is
            # a validation error
            if len(occurs_in) > 1:
                log.trace.debug(
                    '%s - PD : %s mapped to more than one SPs (%s).'
                    % (preamble, pd.device_name, occurs_in)
                )
                msg = 'The physical device "%s" is included in multiple ' \
                      'storage profiles "%s"' % \
                      (pd.device_name, VolMgrUtils.format_list(occurs_in))
                error = ValidationError(
                    item_path=pd.get_vpath(), error_message=msg
                )
                errors.append(error)

        return errors

    def _wait_for_restore_task(self, node, vg_name, volume_name):
        """
        Create the CallbackTask to wait for the restore of a vxfs filesystem
        """
        log.trace.info("Restore wait task created for VG {0},  FS {1}".format(
            vg_name, volume_name))

        return CallbackTask(
            node,
            'Wait for VxVM volume "%s" to complete restore '
            'on node "%s", disk group "%s"' %
            (volume_name, node.hostname, vg_name),
            self.rpc_callbacks['vx_restore'],
            [node.hostname],
            max_wait=18000,
            disk_group=vg_name,
            volume_name=volume_name,
        )

    def check_restores_in_progress(self, data_manager):

        for vg in data_manager.get_all_volume_groups():
            for fs in vg.file_systems:
                if fs.restore_in_progress:
                    msg = "Restore of snapshot, {0} in progress".format(
                        fs.present)
                    raise CallbackExecutionException(msg)

    @staticmethod
    def log_unreachable_cluster_message(log_level, unreachable_cluster):

        nodes = VolMgrUtils.format_list(
            unreachable_cluster.get_hostnames(), quotes_char='double')

        error_text = 'Cluster "%s" node(s) %s did not respond' % (
            unreachable_cluster.id, nodes)

        log_level("No reachable node in cluster %s" % unreachable_cluster.id)
        log_level(error_text)

        # log feedback and errors for nodes in the form of warnings
        for node in unreachable_cluster.nodes:
            log_level(", ".join(node.errors_list))

        return error_text

    def check_disk_groups_active_nodes(self, data_manager):

        for cluster in data_manager.get_clusters():

            # Case 1. Happy Path - at least one node is up in the cluster
            if cluster.is_reachable():

                for node in cluster.nodes:
                    if not node.is_reachable():
                        errors = ", ".join(node.errors_list)
                        log.trace.debug(errors)

                not_imported_vgs = \
                    data_manager.get_not_imported_volume_groups(cluster)

                # check that all VGs have been imported
                if not_imported_vgs:

                    vg_list = [
                        'Cluster:"%s" Volume Group:"%s"' %
                        (vg.cluster_id, vg.name) for vg in not_imported_vgs]

                    error = (
                        ("After reimporting, the following Disk Group(s) "
                         "are not available: %s Please check your system") %
                        VolMgrUtils.format_list(vg_list)
                    )

                    raise CallbackExecutionException(error)

            # Case 2. cluster is unreachable, raise and exception
            else:
                error_to_raise = self.log_unreachable_cluster_message(
                    log.trace.debug, cluster)
                # only now raise an exception
                raise CallbackExecutionException(error_to_raise)

    def check_restore_completed(self, context, hostname, vg_name, volume_name):
        try:
            out, errors = RpcCommandOutputProcessor().\
                execute_rpc_and_process_result(
                    context,
                    [hostname],
                    SNAPSHOT_AGENT,
                    'check_restore_completed',
                    {'disk_group': vg_name,
                    'volume_name': volume_name},
                    timeout=None
                )
        except RpcExecutionException as e:
            raise PluginError(e)
        if errors:
            log.trace.error(', '.join(reduce_errs(errors)))
            raise PluginError(', '.join(reduce_errs(errors)))
        return out[hostname].strip() == 'off'

    def get_dg_hostname(self, context, cluster, vg_names,
                        ignore_unreachable=False):
        ''' ensures that dg_name is imported and returns the node object in
            which it is imported and its vxvm snapshots '''
        self.unreachable_nodes = []

        def applied_reachable_nodes(cluster):
            return [n for n in cluster.query('node')
                    if n.is_applied() and
                       n.hostname not in self.unreachable_nodes]

        applied_nodes = applied_reachable_nodes(cluster)
        if not applied_nodes:
            raise PluginError("No reachable nodes on cluster {0}, cannot run "
                              "vxvm operations".format(cluster.item_id))
        vg_metadata = self._vg_metadata(context, applied_nodes,
                                        vg_names, ignore_unreachable)
        not_imported_dgs = [vg for vg in vg_names if vg not in vg_metadata]
        if not not_imported_dgs:
            return vg_metadata

        applied_nodes = applied_reachable_nodes(cluster)
        if not applied_nodes:
            raise DgNotImportedError("No reachable nodes in cluster {0}, "
                            "cannot import disk group".format(cluster.item_id))

        # any reachable node in the cluster will do, so just pick the first one
        self._import_disk_groups(context, not_imported_dgs, applied_nodes[0])

        # dg is finally imported. Retry and get its snapshots
        vg_metadata = self._vg_metadata(context, applied_nodes[:1],
                                        vg_names, ignore_unreachable
                                                   )
        not_imported_dgs = [vg for vg in vg_names if vg not in vg_metadata]
        if not not_imported_dgs:
            return vg_metadata

        err = ", ".join(not_imported_dgs)
        raise DgNotImportedError("Disk group {0} not available on node {1} "
                    "even after reimporting. Please check your system".\
                    format(err, applied_nodes[0].hostname))

    def _import_disk_groups(self, api_context, dg_names, node):
        # imports disk groups defined in dg_names in node
        for dg_name in dg_names:
            log.trace.info("Disk group {0} is not imported. Will try "\
                           "to import it now.".format(dg_name))
            try:
                self.import_disk_group(api_context, node, dg_name)
            except CallbackExecutionException as e:
                raise DgNotImportedError(e)
            log.trace.info("Disk group {0} is imported in {1}".format(
                                                dg_name, node.hostname)
                                                                      )

    def _vg_metadata(self, context, nodes, dg_names, ignore_unreachable):
        # check if the requested disk groups are available in nodes and return
        # for each dg the node objs in which it is available and its snapshots
        try:
            out, errors = RpcCommandOutputProcessor().\
                execute_rpc_and_process_result(
                    context,
                    [n.hostname for n in nodes],
                    VXVM_AGENT,
                    'get_dg_hostname',
                    timeout=None
                )
        except RpcExecutionException as e:
            raise PluginError(e)

        # TODO: use context when create_plan cannot create snapshots
        if ignore_unreachable:
            out, errors = self.filter_unreachable_nodes(out, errors)
        if errors:
            log.trace.error(', '.join(reduce_errs(errors)))
            raise PluginError(', '.join(reduce_errs(errors)))
        # out format -> {hostname1: {dg_name: [snapshots]}, hostname2: {...}}
        # hostnames in 'nodes' that also appear in the 'out' dictionary
        hostnames = dict((n.hostname, n) for n in nodes if n.hostname in out)
        # filter nodes with no VGs, they will have no disk imported and will
        # make json.loads throw an exception
        out = dict((k, v) for (k, v) in out.iteritems() if v)
        # nodes_ss_per_dg is a dictionary like this:
        # {dg_name: {node_item: [snapshot_list}}
        nodes_ss_per_dg = {}
        dummy_data = {'snaps': [], 'cv': [], 'co': []}
        for hostname, dg_json in out.iteritems():
            node_item = hostnames.get(hostname)
            dg_snapshots = json.loads(dg_json)
            # get only the VGs requested in dg_names
            for dg in set(dg_snapshots.iterkeys()).intersection(set(dg_names)):
                if nodes_ss_per_dg.get(dg):
                    raise Exception(
                        "DG {0} is imported in more than one node".format(dg)
                    )
                nodes_ss_per_dg[dg] = \
                        (node_item, dg_snapshots.get(dg, dummy_data))
        return nodes_ss_per_dg

    def gen_tasks_for_fencing_disks(self, cluster):

        tasks = []

        nodes = [node.hostname for node in cluster.nodes]
        vx_disk_group_name = self.get_vx_fencing_disk_group_name(
            cluster.fencing_disks, cluster.cluster_id
        )

        fencing_disks_initial = [
            fencing_disk for fencing_disk in
            cluster.fencing_disks if fencing_disk.is_initial()
        ]

        # Initial cluster install only and fencing disks in initial
        if cluster.is_initial() and fencing_disks_initial:
            # LITPCDS-10991: requires a specific ordering for tasks related to
            # fencing disks. This task is hung off the fencing disks collection
            # for this reason.
            task = CallbackTask(
                cluster.fencing_disks,
                'Setup VxVM coordinator disk group "{0}"'.format(
                    vx_disk_group_name),
                self.rpc_callbacks['vx_disk_group_setup'],
                vx_disk_group_name,
                nodes[:1],  # need to execute commands only on 1 node
                cluster.item_id
            )

            # Append additional model_items to be state transitioned by the
            # Task feedback, make sure to only add disks in initial
            for fencing_disk in fencing_disks_initial:
                task.model_items.add(fencing_disk)
            tasks.append(task)

        for fencing_disk in cluster.fencing_disks:
            if VxvmDriver._only_disk_uuid_changed(fencing_disk):
                task = self._gen_noop_task_for_disk_update(fencing_disk,
                                              'cluster "%s"' % cluster.item_id)
                tasks.append(task)

        return tasks

    def get_vx_fencing_disk_group_name(self, fencing_disks, cluster_id):
        '''
        Note: This same method must go into both vcs and vol manager plugins!
        If it needs to change, please analyse the method of the same name in
        the vcs plugin.
        Method used to generate a unique "disk group name". It includes the
        cluster id.
        Veritas Disk Groups and Volumes names are limited to 31 characters
        '''
        if not len(fencing_disks):
            return
        disk_group_name = 'vxfencoorddg_' + str(cluster_id)

        return VxvmDriver._condense_name(disk_group_name, size=30)

    def _destroy_disk_group(self, context, disk_group_name, nodes):
        log.event.info("Destroy disk group {0}".format(disk_group_name))
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    nodes,
                    VXVM_AGENT,
                    'destroy_disk_group',
                    {
                        'disk_group': disk_group_name
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def _remove_coordinator_from_disk_group(self, context, disk_group_name,
                                            nodes):
        log.event.info("Remove coordinator for disk group {0}".format(
            disk_group_name))
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    nodes,
                    VXVM_AGENT,
                    'vgdg_set_coordinator_off',
                    {
                        'disk_group_name': disk_group_name
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def _run_vxdisk_list(self, disk_name, nodes):
        try:
            results = run_rpc_command(
                nodes,
                VXVM_AGENT,
                'vxdisk_list',
                {
                    'disk_name': disk_name
                },
                timeout=299,
            )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        log.trace.debug("vxdisk_list rpc return: " + str(results))
        if len(results[nodes[0]]["errors"]) != 0:
            raise CallbackExecutionException('{0}'.format(
                results[nodes[0]]["errors"]))

        return results[nodes[0]]['data']['out']

    def _get_disk_group_name_from_return(self, vxdisk_list_return):
        for line in vxdisk_list_return:
            if line.startswith('group:'):
                return line.split('group:name=')[1].split('id')[0]

    def _check_disk_for_dg(self, context, disk_name, nodes):
        '''
        This method is run as the disk cannot be added to a disk group if it is
        already a member of a disk group. If the disk is a member of another
        group, then the coordinator flag is removed and the disk group is
        destroyed.
        '''
        log.event.info("Check disk {0} for disk group".format(disk_name))
        vxdisk_list_return_string = self._run_vxdisk_list(disk_name,
                                                          nodes)
        log.trace.debug('Return string vxdisk_list for {0}: {1}'.format(
                            disk_name, vxdisk_list_return_string))
        vxdisk_list_return = [line.replace(' ', '') for line
                              in vxdisk_list_return_string.split("\n")]

        disk_group_name = self._get_disk_group_name_from_return(
            vxdisk_list_return)

        if disk_group_name:
            self.import_disk_group_t_flag(context, disk_group_name, nodes)
            self._remove_coordinator_from_disk_group(context, disk_group_name,
                                                     nodes)
            self._destroy_disk_group(context, disk_group_name, nodes)

    def _clear_scsi_keys(self, context, disk_names, nodes):
        for disk_name in disk_names:
            try:
                _, errors = RpcCommandProcessorBase().\
                    execute_rpc_and_process_result(
                        context,
                        nodes,
                        VXVM_AGENT,
                        'clear_keys',
                        {
                            'disk_name': disk_name,
                        },
                        timeout=299,
                        retries=1
                    )
            except RpcExecutionException as e:
                raise CallbackExecutionException(e)
            log.trace.info("clear_scsi_keys: " + str(reduce_errs(errors)))
            if errors:
                raise CallbackExecutionException(', '.join(reduce_errs(
                                                                      errors)))

    def _setup_vx_disk(self, context, disk_names, nodes, disk_group_name):
        for disk_name in disk_names:
            self._check_disk_for_dg(context, disk_name, nodes)
            try:
                _, errors = RpcCommandProcessorBase().\
                    execute_rpc_and_process_result(
                        context,
                        nodes,
                        VXVM_AGENT,
                        'setup_disk',
                        {
                            'disk_name': disk_name,
                            'disk_group': disk_group_name
                        },
                        timeout=299,
                        retries=1
                    )
            except RpcExecutionException as e:
                raise CallbackExecutionException(e)
            log.trace.info("setup_vx_disk: " + str(reduce_errs(errors)))
            if errors:
                raise CallbackExecutionException(
                    ', '.join(reduce_errs(errors))
                )

    def _initialise_vx_disk_group(self, context, disk_group_name,
                                  facter_disk_names, nodes):
        # Initialise the disk group with the first disk
        disk_name = facter_disk_names[0]

        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    nodes,
                    VXVM_AGENT,
                    'create_disk_group',
                    {
                        'disk_group': disk_group_name,
                        'disk_names': disk_name
                    },
                    timeout=299,
                    retries=1
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

        for disk_name in facter_disk_names[1:]:
            try:
                _, errors = RpcCommandProcessorBase().\
                    execute_rpc_and_process_result(
                        context,
                        nodes,
                        VXVM_AGENT,
                        'add_disk_to_group',
                        {
                            'disk_group': disk_group_name,
                            'disk_name': disk_name
                        },
                        timeout=299,
                        retries=1
                    )
            except RpcExecutionException as e:
                raise CallbackExecutionException(e)
            if errors:
                raise CallbackExecutionException(', '.join(reduce_errs(
                                                           errors)))

    def check_dcoregionsz(self, cb_api, vg, active_node):
        try:
            output, errors = RpcCommandOutputProcessor().\
                execute_rpc_and_process_result(
                    cb_api,
                    [active_node],
                    SNAPSHOT_AGENT,
                    'check_dcoregionsz',
                    {
                    'disk_group': vg.name,
                    },
                    timeout=200
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        log.trace.info("gathered dcoregionsz value({0})"
                       .format(output))
        if errors:
            raise CallbackExecutionException(errors)
        if output and active_node in output.keys():
            value = output[active_node]
            if value:
                return value
        raise CallbackExecutionException(
                "No region size value found for volume group"
                " {0} node {1}".format(vg.name, active_node))

    def create_snapshot(self, cb_api, data_manager):

        error_msg = ''
        fs_filter = data_manager.get_filters()
        vg_vpath = fs_filter['vg_vpaths'][0]
        fs_id = fs_filter['fs_ids'][0]

        vg = data_manager.get_volume_group(vg_vpath)
        active_node = vg.get_active_node()
        if not active_node:

            error_msg = 'No active node for disk group "%s"' % vg.name

        else:

            fs = vg.get_file_system(fs_id)
            dcoregionsz = self.check_dcoregionsz(cb_api, vg, active_node)
            self._create_fs_snapshot(cb_api, vg, fs, dcoregionsz)

        if error_msg:
            raise CallbackExecutionException(error_msg)

    def _create_fs_snapshot(self, cb_api, vg, fs, dcoregionsz):
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    cb_api,
                    [vg.get_active_node()],
                    SNAPSHOT_AGENT,
                    'create_snapshot',
                    {
                     'disk_group': vg.name,
                     'volume_name': fs.id,
                     'snapshot_name': fs.snapshot_name,
                     'volume_size': fs.snapshot_size,
                     'region_size': dcoregionsz,
                    },
                    timeout=200
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)

        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def restore_snapshot(self, cb_api, data_manager):

        error_msg = ''
        fs_filter = data_manager.get_filters()
        action_forced = data_manager.get_action_forced()

        vg_vpath = fs_filter['vg_vpaths'][0]
        fs_id = fs_filter['fs_ids'][0]

        vg = data_manager.get_volume_group(vg_vpath)

        if not vg.get_active_node() and action_forced:
            return

        if not vg.get_active_node():

            error_msg = 'No active node for disk group "%s"' % vg.name

        else:
            fs = vg.get_file_system(fs_id)
            if fs.present:
                self._restore_fs_snapshot(cb_api, vg, fs)
            else:
                error_msg = ('No snapshot present for file system "%s" '
                             'in volume group "%s"') % (fs.id, vg.name)
        if error_msg:
            if not action_forced:
                raise CallbackExecutionException(error_msg)
            else:
                log.trace.info(error_msg)

    def _restore_fs_snapshot(self, cb_api, vg, fs):

        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    cb_api,
                    [vg.get_active_node()],
                    SNAPSHOT_AGENT,
                    'restore_snapshot',
                    {
                      'disk_group': vg.name,
                      'volume_name': fs.id,
                      'snapshot_name': fs.present,
                    },
                    timeout=200
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)

        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def remove_snapshot(self, cb_api, data_manager):

        action_forced = data_manager.get_action_forced()
        fs_filter = data_manager.get_filters()
        error_msg = ''

        vg_vpath = fs_filter['vg_vpaths'][0]
        fs_id = fs_filter['fs_ids'][0]

        vg = data_manager.get_volume_group(vg_vpath)

        if not vg.get_active_node():
            error_msg = 'No active node for disk group "%s"' % vg.name
            if action_forced:
                log.trace.info(error_msg)
            else:
                raise CallbackExecutionException(error_msg)
        else:
            fs = vg.get_file_system(fs_id)
            if fs.present:
                self._remove_fs_snapshot(cb_api, vg, fs)
            else:
                error_msg = 'No snapshot present for file system "{0}" '\
                             'in volume group "{1}"'
                error_msg = error_msg.format(fs_id, vg.name)
                log.trace.info(error_msg)

    def _remove_fs_snapshot(self, cb_api, vg, fs):
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    cb_api,
                    [vg.get_active_node()],
                    SNAPSHOT_AGENT,
                    'delete_snapshot',
                    {
                      'disk_group': vg.name,
                      'volume_name': fs.id,
                      'snapshot_name': fs.present,
                    },
                    timeout=200,
                    retries=2
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)

        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def check_snapshots_exist(self, data_manager):

        report = VxvmSnapshotReport()
        for vg in data_manager.get_all_volume_groups():
            if not vg.get_active_node():
                continue

            for fs in vg.file_systems:
                if not fs.present:
                    report.add_snapshot(vg, fs)

        if report.get_snapshots():
            msg = 'Snapshot(s) missing: %s' % report.get_snapshots_txt()
            raise CallbackExecutionException(msg)

    def check_snapshots_valid(self, data_manager):

        report = VxvmSnapshotReport()

        for vg in data_manager.get_all_volume_groups():
            if not vg.get_active_node():
                continue

            for fs in vg.file_systems:
                if fs.present and not fs.valid:
                    report.add_snapshot(vg, fs)

        if report.get_snapshots():
            prefix = 'Check VxVM snapshots are valid'
            msg = '%s %s' % (prefix, report.get_snapshots_txt())
            raise CallbackExecutionException(msg)

    def _check_snapshot_is_valid(self, context, node, disk_group_name,
                                snap_name):
        errors = {}

        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    [node],
                    SNAPSHOT_AGENT,
                    'check_snapshot',
                    {
                        'disk_group': disk_group_name,
                        'snapshot_name': snap_name
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)

        if errors:
            raise PluginError(
                'Snapshot "{0}" on node "{1}" has become invalid'.format(
                    snap_name, node
                )
            )

        return not errors

    def _add_coordinator_to_disk_group(self, context, disk_group_name, nodes):
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    nodes,
                    VXVM_AGENT,
                    'vgdg_set_coordinator_on',
                    {
                        'disk_group_name': disk_group_name
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def deport_disk_group(self, context, disk_group_name, nodes):
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    nodes,
                    VXVM_AGENT,
                    'deport_disk_group',
                    {
                        'disk_group': disk_group_name
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def import_disk_group(self, context, node, disk_group_name):
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    [node.hostname],
                    VXVM_AGENT,
                    'import_disk_group',
                    {
                        'disk_group_name': disk_group_name,
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    def import_disk_group_t_flag(self, context, disk_group_name, nodes):
        try:
            _, errors = RpcCommandProcessorBase().\
                execute_rpc_and_process_result(
                    context,
                    nodes,
                    VXVM_AGENT,
                    'import_disk_group_t_flag',
                    {
                        'disk_group_name': disk_group_name
                    },
                    timeout=None
                )
        except RpcExecutionException as e:
            raise CallbackExecutionException(e)
        if errors:
            raise CallbackExecutionException(', '.join(reduce_errs(errors)))

    # pylint: disable=W0613
    def vx_disk_group_setup(self, callback_api, disk_group_name, nodes,
                            cluster_id):
        cluster_obj = [cluster for cluster in callback_api.query("vcs-cluster")
                       if cluster.item_id == cluster_id][0]

        fencing_disk_uuids = [fencing_disk.uuid for fencing_disk in
                              cluster_obj.fencing_disks]

        msg = 'Obtaining facter names for the disk uuids {0}'
        log.trace.debug(msg.format(fencing_disk_uuids))

        facter_disk_names = []
        for uuid in fencing_disk_uuids:
            facter_disk_names.append(VxvmDriver.get_disk_fact_name(uuid))

        msg = 'Obtained facter names: "{0}" for the disk uuids: "{1}"'
        log.trace.debug(msg.format(facter_disk_names, fencing_disk_uuids))

        log.trace.debug('Clearing all SCSI-3 keys from disk')
        self._clear_scsi_keys(callback_api, facter_disk_names, nodes)

        log.trace.debug("Starting setup for the VX disks")
        self._setup_vx_disk(
            callback_api, facter_disk_names, nodes, disk_group_name
        )
        msg = 'Starting setup VX Disk Group "{0}" with disks'
        log.trace.debug(msg.format(disk_group_name))
        self._initialise_vx_disk_group(
            callback_api, disk_group_name, facter_disk_names, nodes
        )

        msg = 'Adding Coordinator Flag to "{0}"'
        log.trace.debug(msg.format(disk_group_name))
        self._add_coordinator_to_disk_group(
            callback_api, disk_group_name, nodes
        )

        log.trace.debug("Successful VX Disk Group Setup")
        # At this point 'vxdisk -o all dgs list' shows on both nodes

        msg = 'Deporting the VX Disk Group "{0}"'
        log.trace.debug(msg.format(disk_group_name))
        self.deport_disk_group(callback_api, disk_group_name, nodes)

        msg = ('Importing the VX Disk Group "{0}" with -t flag to disable '
               'auto import at next reboot')
        log.trace.debug(msg.format(disk_group_name))
        self.import_disk_group_t_flag(callback_api, disk_group_name, nodes)

        msg = 'Deporting the VX Disk Group "{0}" again'
        log.trace.debug(msg.format(disk_group_name))
        self.deport_disk_group(callback_api, disk_group_name, nodes)
