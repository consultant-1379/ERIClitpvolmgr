##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

from litp.core.validators import ValidationError
from litp.core.execution_manager import ConfigTask
from litp.core.task import OrderedTaskList
from litp.core.litp_logging import LitpLogger
from litp.core.execution_manager import CallbackTask, PluginError
from litp.plan_types.create_snapshot import create_snapshot_tags
from litp.plan_types.remove_snapshot import remove_snapshot_tags
from litp.plan_types.restore_snapshot import restore_snapshot_tags
from litp.core.rpc_commands import RpcExecutionException,\
                                   RpcCommandOutputProcessor,\
                                   reduce_errs
from volmgr_extension.volmgr_extension import LVM_FS_DEFAULT_MOUNT_OPTIONS

from volmgr_plugin.volmgr_utils import (
    VolMgrUtils,
    LITP_EXTRA_CHARS,
    TAG_RESERVED_LENGTH,
    MAX_LVM_VOL_LENGTH,
    MS_ROOT_VG_GROUP_NAME,
    DEFAULT_RPC_TIMEOUT,
    ROOT_FS_MOUNT_POINT,
    LVM_FS_TYPES_NON_SWAP
)

from litp.core.future_property_value import FuturePropertyValue
from .driver import DriverBase
import math
import re
import os

log = LitpLogger()

LV_AGENT = "lv"
LVS_ACTION = "lvs"
VG2FACT_ACTION = "vg2fact"
LSBLK_ACTION = "lsblk"
PVRESIZE_ACTION = "pvresize"

# Signal for logic to use predefined disk device
# to enable KGB cloud systems where disk uuid changes on instantiation
KGB = "kgb"


class LvmDriver(DriverBase):
    """
    LITP LVM Driver
    """

    SLASH_BOOT_SIZE = 500

    VG_OVERHEAD = 100

    LOGICAL_EXTENT_SIZE_MB = 4
    XFS_MINIMUM_SIZE_MB = 16

    @staticmethod
    def generate_LV_name(vg, fs):
        return "_".join((vg.item_id, fs.item_id))

    @staticmethod
    def gen_full_fs_dev_name(node, vg, fs):
        """
        Generate file system identifier
        This *MUST* synchronize with the
        puppet/modules/lvm/manifests/volume.pp file-system identifier
        """
        # Because of LITPCDS-4508, the resource name given to Lvm::Volume
        # resources must include the VG's name. That resource name is the name
        # of the logical volume within its Volume Group, as it would appear in
        # the output of lvdisplay(1)

        return os.path.join("/dev",
                            vg.volume_group_name,
                            LvmDriver.gen_fs_device_name(node, vg, fs))

    @staticmethod
    def _suitable_state(pds, vg, fs, disks):
        """
        Check if any 1 of items is Initial or Updated
        """

        if any(pd.is_initial() or pd.is_updated() for pd in pds):
            return True

        if vg.is_initial() or vg.is_updated():
            return True

        if fs.is_initial():
            return True

        if fs.is_updated():
            # LITPCDS-6964: here we want to ignore updates
            # to properties fs.snap_size and fs.snap_name
            if VolMgrUtils._item_property_has_changed(fs, 'type') or \
               VolMgrUtils._item_property_has_changed(fs, 'size') or \
               VolMgrUtils._mount_affecting_properties_have_changed(fs):
                return True

        if not fs.is_for_removal() and \
           not vg.is_for_removal() and \
           LvmDriver._properties_updated_in_items(disks, ['size', 'uuid']):
            return True

        return False

    @staticmethod
    def _properties_updated_in_items(items, properties):
        return any(VolMgrUtils._item_property_has_changed(item, property_name)
                   for item in items
                   for property_name in properties)

    @staticmethod
    def _get_items_updated_properties(items, properties):
        return [getattr(item, property_name)
                for item in items
                for property_name in properties
                if VolMgrUtils._item_property_has_changed(item, property_name)]

    @staticmethod
    def _disk_sizes_updated(disks):
        for disk in disks:
            if not disk.is_updated():
                continue
            if VolMgrUtils.get_disk_size_delta(disk) != 0:
                return True
        return False

    @staticmethod
    def _volume_only_changed_on_properties(pds, vg, fs, disks, expected_props):

        if any(pd.is_initial() or pd.is_updated() for pd in pds):
            return False

        if vg.is_initial() or vg.is_updated():
            return False

        if fs.is_initial():
            return False

        for prop_name, applied_value in fs.applied_properties.items():

            if getattr(fs, prop_name) != applied_value:
                if prop_name not in expected_props:
                    return False

        if not fs.is_for_removal() and \
           not vg.is_for_removal() and \
           (LvmDriver._properties_updated_in_items(disks, ['uuid']) or
            LvmDriver._disk_sizes_updated(disks)):
            return False

        return True

    def _set_partition_flag(self, node, vg):
        """
        Set the partition flag for all new disks in a VG
        """

        # Mark disks as partitioned by Anaconda's initial setup
        for pd in vg.physical_devices:
            disk = VolMgrUtils.get_node_disk_for_pd(node, pd)
            if disk.is_initial() and not pd.is_for_removal():
                if VolMgrUtils.is_the_rootvg(node, vg) and \
                   pd.is_initial() and vg.is_initial():

                    if node.is_ms() and disk.bootable == 'false':
                        disk.disk_part = "false"
                    else:
                        disk.disk_part = "true"
                else:
                    disk.disk_part = "false"

    @staticmethod
    def ms_install_fs(node, vg, fs):
        if node.is_ms() and \
           vg.volume_group_name == MS_ROOT_VG_GROUP_NAME:
            for ms_fs in VolMgrUtils.get_ms_root_fss():
                if fs.mount_point == ms_fs.mount_point:
                    return ms_fs

    def _gen_tasks_for_file_system(self, node, pds, vg, fs, disks, context):
        """
        Generate all tasks for a file system in
        a given volume group on a given node.
        """

        preamble = '._gen_tasks_for_file_system: %s VG:%s, FS:%s : ' % \
                   (node.item_id, vg.item_id, fs.item_id)

        log.trace.debug((preamble +
                         "Generating tasks for file system '%s'") %
                         fs.item_id)

        # TODO If/when swap handling becomes supported, we'll need to handle it
        # separately

        if fs.type in LVM_FS_TYPES_NON_SWAP:
            tasks = []

            # Should the mount_point and/or fsck_pass and/or mount_options
            # be the only change,
            # the task for the Lvm::Volume is not needed.
            # In other cases (changes on PDs, VG or other properties affecting
            # the volume), the Lvm::Volume task should be regenerated.
            expected_props = ['mount_point', 'fsck_pass', 'mount_options']
            if not LvmDriver._volume_only_changed_on_properties(pds, vg,
                                                               fs, disks,
                                                               expected_props):
                tasks.append(self._gen_task_for_volume(node, pds, vg, fs,
                                                       context))
            if fs.is_initial():
                tasks.extend(self._gen_initial_mount_point_tasks(node, vg, fs))
            elif VolMgrUtils._mount_affecting_properties_have_changed(fs):
                tasks.extend(self._gen_updated_mount_point_tasks(node, vg, fs))

            if tasks:
                return [OrderedTaskList(node.storage_profile, tasks)]

        return []

    def _gen_task_mount_clean(self, node, vg, fs, mount_task):

        cluster = node.get_cluster()

        if node.is_ms() or \
           vg.volume_group_name in ('neo4j_vg', MS_ROOT_VG_GROUP_NAME) or \
           not LvmDriver._is_os_reinstall(node) or \
           not (cluster.item_type_id == 'vcs-cluster' and \
                cluster.cluster_type == 'sfha' and
                node.os.version == 'rhel7'):
            return None

        desc = 'Clean content of {0} on "{1}"'.format(fs.mount_point,
                                                      node.hostname)
        task = CallbackTask(fs,
                            desc,
                            self.rpc_callbacks['base'],
                            [node.hostname],
                            'vrts',
                            'clean_packages_dir',
                            destination_dir='{0}/*'.format(fs.mount_point))

        task.requires.add(mount_task)
        return task

    def _gen_initial_mount_point_tasks(self, node, vg, fs):

        tasks = []
        if hasattr(fs, 'mount_point') and \
           fs.mount_point and fs.mount_point != ROOT_FS_MOUNT_POINT:
            t1 = LvmDriver._gen_task_create_dir(node, vg, fs)
            t2 = LvmDriver._gen_task_mount_volume(node, vg, fs)
            tasks.extend([t1, t2])
            t3 = self._gen_task_mount_clean(node, vg, fs, t2)
            if t3:
                tasks.append(t3)

        return tasks

    @staticmethod
    def _gen_unmount_task(node, vg, fs):
        desc = 'Unmount \'{0}\' file system on node "{1}" - ' \
               'FS: "{2}", VG: "{3}", mount point: "{4}"'
        desc = desc.format(fs.type, node.hostname, fs.item_id,
                           vg.item_id, fs.applied_properties['mount_point'])

        fs_device = LvmDriver.gen_full_fs_dev_name(node, vg, fs)
        task = ConfigTask(node,
                        fs,
                        desc,
                        'mount',
                        fs.applied_properties['mount_point'],
                        fstype=fs.type,
                        device=fs_device,
                        ensure='absent')
        return task

    @staticmethod
    def _gen_replaced_tasks(fs):
        """
        Due to limitations on how Puppet 3.3.2 deals with File resouce,
        we have replaced it with a customized Exec resource using mkdir
        via volmgr::create_mount_path. For more details, please,
        refer to TORF-216609.
        """

        return set([(call_type, fs.applied_properties['mount_point'])
                    for call_type in ('file', 'volmgr::create_mount_path')])

    def _gen_updated_mount_point_tasks(self, node, vg, fs):
        tasks = []
        if VolMgrUtils._mount_point_has_changed(fs):
            if not hasattr(fs, 'mount_point') or not fs.mount_point:
                # mount_point has been removed

                unmount_task = LvmDriver._gen_unmount_task(node, vg, fs)
                unmount_task.persist = False
                unmount_task.replaces = LvmDriver._gen_replaced_tasks(fs)

                tasks = [unmount_task]

            elif hasattr(fs, 'mount_point') and fs.mount_point and \
                not fs.applied_properties.get('mount_point'):
                # mount_point has been added

                tasks = self._gen_initial_mount_point_tasks(node, vg, fs)

            else:
                # mount_point has been replaced

                unmount_task = LvmDriver._gen_unmount_task(node, vg, fs)
                unmount_task.persist = False

                dir_task = LvmDriver._gen_task_create_dir(node, vg, fs)
                dir_task.replaces = LvmDriver._gen_replaced_tasks(fs)

                mount_task = LvmDriver._gen_task_mount_volume(node, vg, fs)
                tasks = [unmount_task, dir_task, mount_task]

        elif VolMgrUtils._fsck_pass_has_changed(fs) or \
            VolMgrUtils._mount_options_has_changed(fs):
            # fsck_pass, mount_options is affected

            mount_task = LvmDriver._gen_task_mount_volume(node, vg, fs)
            tasks = [mount_task] if mount_task else []

        return tasks

    def _gen_disk_fact_name_by_disk_name(self, disk):
        # use name of a disk from the model
        # to construct the fact name
        return '$::disk_' + disk.name.lower().strip()

    def _physical_device_name(self, node, disk, vg, context):

        disk_fact_name = None

        if hasattr(disk, "uuid") and disk.uuid and \
                   KGB == VolMgrUtils.get_canonical_uuid(disk):
            fact_name = self._gen_disk_fact_name_by_disk_name(disk)
            disk_part = self._get_canonical_boot_disk_part(node, disk)
            if disk_part:
                disk_fact_name = "{0}{1}".format(fact_name, disk_part)
            else:
                disk_fact_name = "{0}".format(fact_name)
            log.trace.debug("Using disk fact name: %s" %
                            (disk_fact_name))
        elif disk.item_type_id == 'disk' or \
             (not disk.is_initial() and (hasattr(disk, "uuid") and disk.uuid)):
            disk_fact_name = self._gen_disk_fact_name(node, disk, vg, context)
        else:
            disk_fact_name = FuturePropertyValue(disk, "disk_fact_name")
            log.trace.debug("Using FuturePropertyValue: %s" % disk_fact_name)
        return disk_fact_name

    def _get_canonical_boot_disk_part(self, node, disk):
        disk_part = ''
        if not node.is_ms() and disk.bootable == 'true':
            disk_part = '3'
        elif node.is_ms() or disk.bootable == 'true':
            disk_part = '2'
        else:
            if hasattr(disk, "disk_part") and disk.disk_part == 'true':
                disk_part = '1'

        return disk_part

    def _gen_disk_fact_name(self, node, disk, vg, context):

        if node.is_ms() and vg.volume_group_name == MS_ROOT_VG_GROUP_NAME:
            vg_data = self._get_vg_data_by_rpc(context, node)
            disk_fact_name = '$::' + vg_data['fact']
        else:
            disk_fact_name = '$::disk_'
            disk_uuid = VolMgrUtils.get_canonical_uuid(disk)
            if disk_uuid:
                disk_fact_name += disk_uuid
            else:
                disk_fact_name += '<unknown>'
            if VolMgrUtils.is_the_rootvg(node, vg):
                disk_part = self._get_canonical_boot_disk_part(node, disk)
                if disk_part:
                    disk_fact_name += "_part{0}".format(disk_part)

            disk_fact_name += '_dev'

        return disk_fact_name

    @staticmethod
    def _gen_volume_task_description(node, new_pds, vg, fs, disks):

        if LvmDriver._properties_updated_in_items(disks, ['uuid']):
            uuids = LvmDriver._get_items_updated_properties(disks, ['uuid'])
            uuid_str = VolMgrUtils.format_list(uuids, quotes_char="double")

        if LvmDriver._properties_updated_in_items(disks, ['size']):
            sizes = LvmDriver._get_items_updated_properties(disks, ['size'])
            size_str = VolMgrUtils.format_list(sizes, quotes_char="double")

        # For cases where new pds have been added
        if not vg.is_initial() and new_pds:
            # Currently remove disk from vg is not supported
            # so if pds are different, we have added a new disk

            dev_names = [pd.device_name for pd in new_pds]

            pds_str = VolMgrUtils.format_list(dev_names, quotes_char="double")

            desc_start = ('Configure LVM VG: "{0}" for FS: "{1}" by extending '
                         'with new disk(s): {2} '.format(vg.item_id,
                                                fs.item_id, pds_str))

            desc_end = 'on node "{0}"'.format(node.hostname)
            if fs.is_initial() or not \
                      LvmDriver._properties_updated_in_items(disks,
                                                             ['size', 'uuid']):
                desc = desc_start + desc_end
            else:
                desc_start = desc_start + 'and by updating disk(s) '
                # FS is updated and underlying disk size is updated
                if LvmDriver._properties_updated_in_items(disks, ['size']):
                    # FS, disk_size and uuid are updated
                    if LvmDriver._properties_updated_in_items(disks, ['uuid']):
                        desc_middle = 'with uuid(s): {0} and '\
                            'disk size(s): {1} '.format(uuid_str, size_str)
                        desc = desc_start + desc_middle + desc_end
                    else:
                        # FS, disk_size are updated but not uuid
                        desc_middle = 'with disk size(s): {0} '\
                                               .format(size_str)
                        desc = desc_start + desc_middle + desc_end
                else:
                    # FS is updated but disk size is not
                    if LvmDriver._properties_updated_in_items(disks, ['uuid']):
                        # FS and uuid are updated but not size
                        desc_middle = 'with uuid(s): {0} '.format(uuid_str)
                        desc = desc_start + desc_middle + desc_end
        else:
            # For cases where there are no new pds
            desc_start = 'Configure LVM volume on node "{0}" - ' \
                        'FS: "{1}", VG: "{2}"'.format(node.hostname,
                                                         fs.item_id,
                                                         vg.item_id)
            if fs.is_initial():
                desc = desc_start
            else:
                if LvmDriver._properties_updated_in_items(disks, ['uuid']):
                    # FS and uuids have been updated
                    if LvmDriver._properties_updated_in_items(disks, ['size']):
                        # FS, uuid(s) and size(s) have been updated
                        desc_end = ' with uuid(s): {0} and disk size(s): '\
                                     '{1}'.format(uuid_str, size_str)
                        desc = desc_start + desc_end
                    else:
                        # FS and uuid(s) have been updated but not size(s)
                        desc_end = ' with uuid(s): {0}'.format(uuid_str)
                        desc = desc_start + desc_end
                else:
                    if LvmDriver._properties_updated_in_items(disks, ['size']):
                        desc_end = ' with disk size(s): {0}'.format(size_str)
                        desc = desc_start + desc_end
                    else:
                        desc = desc_start
        log.trace.debug("Task desc is %s" % desc)
        return desc

    def _gen_task_for_volume(self, node, pds, vg, fs, context):
        """
        Generate a task for a volume in a given volume group
        on a given physical device on a given node.
        """

        preamble = '._gen_task_for_volume: %s VG:%s, FS:%s : ' % \
                   (node.item_id, vg.item_id, fs.item_id)

        log.trace.debug(preamble + "Generating volume task")

        disks = []
        new_pds = []
        for pd in pds:
            if pd.is_initial():
                new_pds.append(pd)
            disk = VolMgrUtils.get_node_disk_for_pd(node, pd)
            if disk is not None:
                disks.append(disk)

        devices = []

        for disk in disks:
            device = self._physical_device_name(node, disk, vg, context)
            if device:
                devices.append(device)

        task_desc = LvmDriver._gen_volume_task_description(node, new_pds,
                                                           vg, fs, disks)

        # The call_id given to this task *MUST BE* the name of the Logical
        # Volume within its Volume Group
        device = LvmDriver.gen_fs_device_name(node, vg, fs)

        log.trace.debug(preamble + ("Will use Volume device %s" % device))
        vg_task = ConfigTask(node,
                             fs,
                             task_desc,
                             'lvm::volume',
                             device,
                             ensure='present',
                             pv=sorted(devices),
                             vg=vg.volume_group_name,
                             fstype=fs.type,
                             size=fs.size)

        # Associate with file-system and all physical-devices
        vg_task.model_items.add(vg)
        for pd in new_pds:
            disk = VolMgrUtils.get_node_disk_for_pd(node, pd)
            vg_task.model_items.add(pd)
            vg_task.model_items.add(disk)

        return vg_task

    @staticmethod
    def gen_fs_device_name(node, vg, fs):
        install_ms_fs = LvmDriver.ms_install_fs(node, vg, fs)
        if fs.type in LVM_FS_TYPES_NON_SWAP and install_ms_fs:
            return 'lv_' + install_ms_fs.name
        else:
            return LvmDriver.generate_LV_name(vg, fs)

    @staticmethod
    def _gen_task_create_dir(node, vg, fs):

        if fs.mount_point == ROOT_FS_MOUNT_POINT:
            return None

        desc = 'Create LVM mount directory on node "%s" - ' \
               'FS: "%s", VG: "%s", mount point: "%s"' % \
               (node.hostname, fs.item_id, vg.item_id, fs.mount_point)

        task = ConfigTask(node,
                          fs,
                          desc,
                          'volmgr::create_mount_path',
                          fs.mount_point,
                          mount_point=fs.mount_point)

        return task

    @staticmethod
    def _gen_task_mount_volume(node, vg, fs):

        if not fs.mount_point or fs.mount_point == ROOT_FS_MOUNT_POINT:
            return None

        desc = 'Mount \'{0}\' file system on node "{1}" - ' \
               'FS: "{2}", VG: "{3}", mount point: "{4}"'
        desc = desc.format(fs.type, node.hostname, fs.item_id,
                          vg.item_id, fs.mount_point)

        fs_device = LvmDriver.gen_full_fs_dev_name(node, vg, fs)
        task = ConfigTask(node,
                            fs,
                            desc,
                            'mount',
                            fs.mount_point,
                            **{'fstype': fs.type,
                               'device': fs_device,
                               'ensure': 'mounted',
                               'require': [{
                                    'type': 'Lvm::Volume',
                                    'value': LvmDriver.gen_fs_device_name(node,
                                                                          vg,
                                                                          fs)
                                           }],
                                'options': fs.mount_options \
                                if hasattr(fs, 'mount_options') else \
                                LVM_FS_DEFAULT_MOUNT_OPTIONS,
                               'atboot': "true",
                               'pass': fs.fsck_pass if fs.fsck_pass else '2'})
        return task

    def _vg_needs_clearpart(self, vg, root_vg_name):
        return not vg.is_initial() or \
               (vg.is_initial and vg.volume_group_name != root_vg_name)

    def _disk_is_part(self, disk):
        return hasattr(disk, "disk_part") and disk.disk_part == 'true'

    def _gen_task_for_pv_clearout(self, node, vg, the_pd, root_vg_name):
        preamble = '._gen_task_for_pv_clearout: %s VG:%s : ' % \
                   (node.item_id, vg.item_id)

        task = None
        # If this is a new drive on an 'update' runplan, purge any detritus
        if self._vg_needs_clearpart(vg, root_vg_name):
            # filter out disks that are prepared by kickstart (disk_part)
            disk = VolMgrUtils.get_node_disk_for_pd(node, the_pd)
            if self._disk_is_part(disk):
                return None
            if disk.is_initial() and the_pd.is_initial():
                log.trace.debug(preamble + "Clearing {0} ({1})".format(
                                disk.name, disk.uuid))

                desc = 'Purge existing partitions and volume groups ' \
                       'from disk "{0}" on node "{1}"'.format(
                    disk.name, node.hostname)

                if disk.item_type_id == 'disk':
                    task = CallbackTask(disk,
                                        desc,
                                        self.rpc_callbacks['base'],
                                        [node.hostname],
                                        'killpart',
                                        'clear',
                                        uuid=disk.uuid)
                else:
                    task = CallbackTask(disk,
                                        desc,
                                        self.rpc_callbacks['future_uuid'],
                                        [node.hostname],
                                        'killpart',
                                        'clear',
                                        uuidfromview=disk.get_vpath())

        return task

    @staticmethod
    def _is_os_reinstall(node):
        return hasattr(node, "upgrade") and \
               hasattr(node.upgrade, "os_reinstall") and \
               node.upgrade.os_reinstall == 'true'

    @staticmethod
    def _get_updated_disk_for_pd_on_node(node, pd):
        """
        Returns updated disk from lvm storage profile
        """
        for disk in VolMgrUtils.system_disks(node):
            if (pd.device_name == disk.name) and disk.is_updated():
                return disk
        return None

    def _gen_resize_disk_task(self, node, vg, disk, pd, context):
        disk_fact_name = str(
            self._physical_device_name(node, disk, vg, context)[3:])

        preamble = '._gen_resize_disk_task: {0} disk: {1} : '.format(
                   node.item_id, disk.item_id)

        log.trace.debug((preamble +
                         "Generating tasks for PV '{0}'").format(
                             disk_fact_name))

        desc = 'Resize LVM PV for disk "{0}" on node "{1}"'.format(
                disk.name, node.hostname)
        task = CallbackTask(disk,
                            desc,
                            self.rpc_callbacks['base'],
                            [node.hostname],
                            LV_AGENT,
                            PVRESIZE_ACTION,
                            disk_fact_name=disk_fact_name)
        task.model_items.add(pd)
        return task

    def _gen_resize_disk_tasks_for_vg(self, node, vg, context):
        tasks = []
        disk_sets = [(pd, self._get_updated_disk_for_pd_on_node(node, pd))
                 for pd in VolMgrUtils.vg_pds(vg) if pd.is_applied()]
        for (pd, disk) in disk_sets:
            if not disk:
                continue
            if VolMgrUtils.has_disk_size_increased(disk):
                task = self._gen_resize_disk_task(node, vg, disk, pd, context)
                tasks.append(task)
        return tasks

    def gen_tasks_for_volume_group(self, node, vg, context):
        """
        Generate all tasks for a given volume group
        for a given managed node.
        """
        preamble = '.gen_tasks_for_volume_group: %s VG:%s : ' % \
                   (node.item_id, vg.item_id)

        log.trace.debug((preamble +
                         "Generating tasks for volume group '%s'") %
                        vg.item_id)
        tasks = []
        purge_tasks = []

        root_vg_name = VolMgrUtils.get_root_vg_name_from_modeled_vgs(node, vg)
        # Mark disks as partitioned by Anaconda's initial setup
        self._set_partition_flag(node, vg)

        # Generate purge tasks if necessary

        if not LvmDriver._is_os_reinstall(node):
            for pd in vg.physical_devices:
                purge_task = self._gen_task_for_pv_clearout(node, vg, pd,
                                                            root_vg_name)
                if purge_task:
                    purge_tasks.append(purge_task)

        # Generate Physical Volume Resize tasks if necessary
        tasks.extend(self._gen_resize_disk_tasks_for_vg(node, vg, context))

        # Generate LVM config tasks if necessary
        pds = [pd for pd in vg.physical_devices]
        disks = VolMgrUtils.get_disks_for_pds(pds, node)

        for fs in vg.file_systems:
            if LvmDriver._suitable_state(pds, vg, fs, disks):
                tasks += self._gen_tasks_for_file_system(node, pds,
                                                     vg, fs, disks, context)
        if purge_tasks:
            # Ensure vg/fs related tasks are dependent on disk purge tasks
            # this will force purge tasks to happen one phase ahead
            for ptask in purge_tasks:
                for ftask in tasks:
                    ftask.task_list[0].requires.add(ptask)

            purge_tasks.extend(tasks)
            tasks = purge_tasks

        return tasks

    def _is_grub_task_required(self, volume_groups, cluster):
        if cluster.item_type_id == 'vcs-cluster':
            grub_lv_enabled_property_changed = \
            VolMgrUtils._item_property_has_changed(cluster, 'grub_lv_enable') \
               or (VolMgrUtils._grub_lv_enable_added(cluster) \
                   and cluster.grub_lv_enable == "true")

            return self._any_fs_updated_for_grub(volume_groups, cluster) \
                   or grub_lv_enabled_property_changed

        return False

    def _any_fs_updated_for_grub(self, volume_groups, cluster):
        return any(fs.is_initial() or fs.is_for_removal()
               for vg in volume_groups
               for fs in vg.file_systems) \
               and cluster.grub_lv_enable == "true"

    def gen_grub_tasks(self, node, volume_groups):
        tasks = []
        cluster = node.get_cluster()
        if self._is_grub_task_required(volume_groups, cluster):

            lv_string = ' '.join(['rd.lvm.lv={0}/{1}'.
                                 format(vg.volume_group_name,
                                        LvmDriver.generate_LV_name(vg, fs))
                                  for vg in volume_groups
                                  for fs in vg.file_systems
                                  if (('true' == cluster.grub_lv_enable and \
                                      not fs.is_for_removal()) or \
                                      ('false' == cluster.grub_lv_enable and \
                                      ('swap' == fs.type or \
                                      '/' == fs.mount_point)))])

            update_grub_task = CallbackTask(
                           node.storage_profile,
                           'Update LVM volume names in grub files on node ' \
                           '"{0}" and re-build grub.'.format(node.hostname),
                           self.rpc_callbacks['base'],
                           [node.hostname],
                           'grub',
                           'update_grub',
                           timeout=DEFAULT_RPC_TIMEOUT,
                           lv_names=lv_string)

            update_grub_task.requires.update([fs for vg in volume_groups
                                              for fs in vg.file_systems
                                              if fs.is_initial()])
            tasks.append(update_grub_task)

        if not tasks and (VolMgrUtils._grub_lv_enable_added(cluster) \
                          and cluster.grub_lv_enable == "false"):
            noop_grub_task = CallbackTask(cluster,
                            'Configure cluster "%s" to applied state' % \
                             cluster.item_id,
                            self.rpc_callbacks['noop_grub_task'],
                            cluster=cluster.item_id,
                            )
            tasks.append(noop_grub_task)

        return tasks

    @staticmethod
    def _effective_fs_size(fs_type, fs_size, fs_snap_size):
        size = VolMgrUtils.get_size_megabytes(fs_size)
        # Snapshots only taken for ext4 and xfs file-systems
        if fs_type in LVM_FS_TYPES_NON_SWAP:
            return int(math.ceil((size * (100. + int(fs_snap_size))) / 100))
        else:
            return size

    def _validate_vg_size_against_disks(self, node, vg, disks):
        """
        Validate the file system for a given volume group
        will fit on the nominated system disk.
        """

        preamble = '_validate_vg_size_against_disks: %s VG:%s ' % \
                   (node.item_id, vg.item_id)

        vg_cumulative_size = sum([LvmDriver._effective_fs_size(fs.type,
                                                               fs.size,
                                                               fs.snap_size)
                                  for fs in vg.file_systems
                                  if not fs.is_for_removal() and
                                     not vg.is_for_removal()])

        if node.is_ms() and VolMgrUtils.is_the_rootvg(node, vg):
            ms_modeled_fs_mounts = LvmDriver._get_modeled_ms_mounts(node)
            ms_modeled_fs_del_mounts = \
                LvmDriver._get_modeled_ms_changed_mounts(node)

            vg_cumulative_size += \
                sum([LvmDriver._effective_fs_size(ks.fs_type, ks.size, '100')
                     for ks in VolMgrUtils.get_ms_root_fss()
                        if not ks.mount_point in ms_modeled_fs_mounts and not
                            ks.mount_point in ms_modeled_fs_del_mounts])

        disks_cumulative_size = 0
        overhead = LvmDriver.VG_OVERHEAD
        suppress_validation_message = False

        for disk in disks:
            disks_cumulative_size += VolMgrUtils.get_size_megabytes(disk.size)

            if disk.bootable == 'true':
                overhead += LvmDriver.SLASH_BOOT_SIZE

            # If any of the disks is decreased in size do not display errors
            if VolMgrUtils._is_disk_size_decreased(disk):
                suppress_validation_message = True

        if (vg_cumulative_size + overhead) > disks_cumulative_size and \
                                       not suppress_validation_message:
            message = ("The System Disks ({disk_size} MB) "
            "on node '{node_name}' are not large "
            "enough for volume group requirement ({fs_total} MB). "
            "Volume group requirement = ((file systems including "
            "snapshots) {fs_size} MB) + (LVM metadata "
            "{overhead} MB.)").format(
                node_name=node.hostname,
                disk_size=disks_cumulative_size,
                fs_size=vg_cumulative_size,
                overhead=overhead,
                fs_total=sum([vg_cumulative_size, overhead]))

            log.trace.debug(preamble + message)
            return ValidationError(item_path=vg.get_vpath(),
                                   error_message=message)
        return None

    def _get_node_disks_for_vg(self, node, vg):
        vg_disks = []
        for pd in vg.physical_devices:
            disk = VolMgrUtils.get_node_disk_for_pd(node, pd)
            if disk is not None:
                vg_disks.append(disk)

        return vg_disks

    def _validate_disk_sizes_for_standard_plan(self, node):
        if LvmDriver._should_validate_storage_profile_standard_plan(node):
            return self.validate_disk_sizes(node)
        return []

    def _validate_disk_sizes_for_snapshot_plan(self, node):
        if self._should_validate_storage_profile_snapshot_plan(node):
            return self.validate_disk_sizes(node)
        return []

    @staticmethod
    def _should_validate_storage_profile_standard_plan(node):

        for vg in node.storage_profile.volume_groups:
            pds = [pd for pd in vg.physical_devices]
            disks = VolMgrUtils.get_disks_for_pds(pds, node)
            if any([LvmDriver._suitable_state(pds, vg, fs, disks)
                    for fs in vg.file_systems]):
                return True

        return False

    def _should_validate_storage_profile_snapshot_plan(self, node):

        for vg in node.storage_profile.volume_groups:
            if any((VolMgrUtils._item_property_has_changed(fs, 'snap_size') or
                VolMgrUtils._item_property_has_changed(fs, 'backup_snap_size'))
                   for fs in vg.file_systems):
                return True

        return False

    def validate_disk_sizes(self, node):
        """
        Validate that the volume groups can fit on the
        nominated system disks
        """

        errors = []
        for vg in node.storage_profile.volume_groups:

            vg_disks = self._get_node_disks_for_vg(node, vg)
            if vg_disks:
                error = self._validate_vg_size_against_disks(node, vg,
                                                    vg_disks)
                if error:
                    errors.append(error)

        return errors

    def _is_extent_multiple(self, size):
        """
        Return boolean True if file system size is a multiple of logical extent
        """

        size_mb = VolMgrUtils.get_size_megabytes(size)
        if (int(size_mb) % LvmDriver.LOGICAL_EXTENT_SIZE_MB) == 0:
            return True

        return False

    def _validate_fs_size(self, node):
        """
        Validate that file system size is multiple of logical extent
        """

        preamble = '_validate_fs_size: %s : ' % node.item_id

        errors = []
        for vg in node.storage_profile.volume_groups:
            for fs in vg.file_systems:
                if not self._is_extent_multiple(fs.size):
                    msg = ("File System size '%s' on '%s' is not an " +
                           "exact multiple of the LVM Logical Extent size " +
                           "('%dMB')") % (fs.size,
                                          node.hostname,
                                          LvmDriver.LOGICAL_EXTENT_SIZE_MB)
                    log.trace.debug(preamble + msg)
                    error = ValidationError(item_path=fs.get_vpath(),
                                            error_message=msg)
                    errors.append(error)

        return errors

    @staticmethod
    def _is_xfs_size_valid(size):
        """
        Return boolean True if file system size is greater than or equal to
        a minimum value
        """

        size_mb = VolMgrUtils.get_size_megabytes(size)
        if int(size_mb) >= LvmDriver.XFS_MINIMUM_SIZE_MB:
            return True

        return False

    def _validate_xfs_minimum_fs_size(self, node):
        """
        Validate that the minimum xfs file system sizes are greater than
        or equal to a minimum size value
        """

        preamble = '_is_xfs_size_valid: %s : ' % node.item_id
        errors = []
        for vg in node.storage_profile.volume_groups:
            for fs in vg.file_systems:
                if fs.type != 'xfs':
                    continue

                if not self._is_xfs_size_valid(fs.size):
                    msg = ("File System of size '%s' on '%s' must be greater"
                           " than ('%dMB') for type 'xfs'"
                           ) % (fs.size, node.hostname,
                                LvmDriver.XFS_MINIMUM_SIZE_MB)
                    log.trace.debug(preamble + msg)
                    error = ValidationError(item_path=fs.get_vpath(),
                                            error_message=msg)
                    errors.append(error)
        return errors

    @staticmethod
    def _validate_names_error(preamble, item, errors, msg):
        log.trace.debug(preamble + msg)
        errors.append(
            ValidationError(
                item_path=item.get_vpath(),
                error_message=msg
                )
            )

    def _validate_names_rootvg(self, node, vg):
        """
        Check vg-root VG/FS names
        """
        preamble = '_validate_names_rootvg: %s : ' % node.item_id
        errors = []
        check_pattern = r'^[0-9A-Za-z_\.]*$'
        for fs in vg.file_systems:
            lv_name = LvmDriver.generate_LV_name(vg, fs)
            vg_name = vg.volume_group_name

            lv_err = ('LVM LV name "{0}", derived from volume-group "{1}" '
                      'and file-system '
                      '"{2}",').format(lv_name, vg.item_id, fs.item_id)

            vg_err = 'LVM volume_group_name "{0}"'.format(vg_name)

            if lv_name[0] == '_':
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} cannot start with an underscore.'.format(lv_err)
                )
            if vg_name[0] == '_':
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} cannot start with an underscore.'.format(vg_err)
                )

            if len(lv_name) > 50:
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} exceeds 50 characters.'.format(lv_err)
                )
            if len(vg_name) > 50:
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} exceeds 50 characters.'.format(vg_err)
                )

            if not re.search(check_pattern, lv_name):
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} has disallowed characters.'.format(lv_err)
                )
            if not re.search(check_pattern, vg_name):
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} has disallowed characters.'.format(vg_err)
                )

            for banned_seq in [
                '_mlog', '_mimage', '_rimage', '_tdata', '_tmeta'
                ]:
                if lv_name.find(banned_seq) != -1:
                    self._validate_names_error(
                        preamble, vg, errors,
                        '{0} contains prohibited "{1}".'.format(
                            lv_err, banned_seq
                            )
                        )

        return errors

    def _validate_names_nonrootvg(self, node, vg):
        """
        Check non-root VG/FS names
        """
        preamble = '_validate_names_nonrootvg: %s : ' % node.item_id
        check_pattern = r'^[0-9A-Za-z+_\.-]*$'
        errors = []
        for fs in vg.file_systems:
            lv_name = LvmDriver.generate_LV_name(vg, fs)
            vg_name = vg.volume_group_name
            fs_name = '{0}/{1}'.format(vg_name, lv_name)

            lv_err = ('LVM LV name "{0}", derived from volume-group "{1}" ' \
                      'and file-system "{2}",').format(lv_name, vg.item_id,
                                                       fs.item_id)

            fs_err = 'LVM FS device name "{0}", which is derived from VG ' \
                     'group name "{1}", item_id "{2}" & FS item_id ' \
                     '"{3}"'.format(fs_name, vg_name, vg.item_id, fs.item_id)

            # Check components for bad chars
            if not re.search(check_pattern, vg_name):
                self._validate_names_error(
                    preamble, vg, errors,
                    'LVM volume_group_name "{0}"'
                    ' contains disallowed characters.'.format(vg_name)
                    )
            if not re.search(check_pattern, vg.item_id):
                self._validate_names_error(
                    preamble, vg, errors,
                    'LVM VG item_id "{0}"'
                    ' contains disallowed characters.'.format(vg.item_id)
                    )
            if not re.search(check_pattern, fs.item_id):
                self._validate_names_error(
                    preamble, fs, errors,
                    'LVM FS item_id "{0}"'
                    ' contains disallowed characters.'.format(fs.item_id)
                    )

            for banned_seq in [
                '_mlog', '_mimage', '_rimage', '_tdata', '_tmeta'
                ]:
                if lv_name.find(banned_seq) != -1:
                    self._validate_names_error(
                        preamble, vg, errors,
                        '{0} contains prohibited "{1}".'.format(
                            lv_err, banned_seq
                            )
                        )

            if len(fs_name) + fs_name.count('-') > (MAX_LVM_VOL_LENGTH -
                                LITP_EXTRA_CHARS -
                                TAG_RESERVED_LENGTH):
                self._validate_names_error(
                    preamble, vg, errors,
                    '{0} exceeds {1} characters.'.format(fs_err,
                        (MAX_LVM_VOL_LENGTH -
                                LITP_EXTRA_CHARS -
                                TAG_RESERVED_LENGTH))
                    )

        return errors

    def _validate_names(self, node):
        """
        Check whether combined VG/FS name exceeds known limits.

        Anaconda has a limit of 50 for each of VG and LV name length.
        Litp imposes the following restrictions on root-vg entities
        (whether they're created pre or post installation):
            50 >= len("<vg.volume_group_name>")
            50 >= len("<vg.item_id>_<fs.item_id>")
        For non root-vg those Anaconda restrictions don't apply;
        instead an LVM restriction applies which is that the combined
        VG-LV name must not exceed 126. For Litp that means:
            126 >= len("<vg.volume_group_name>/<vg.item_id>_<fs.item_id>")
        As a caveat, snapshot volume names are similarly restricted by:
            126 >= len("<vg.volume_group_name>/L_<vg.item_id>_<fs.item_id>_")
        """

        errors = []
        root_vg = VolMgrUtils.get_node_root_vg_name(node)
        for vg in node.storage_profile.volume_groups:
            if root_vg and (vg.volume_group_name == root_vg):
                errors.extend(self._validate_names_rootvg(node, vg))
            else:
                errors.extend(self._validate_names_nonrootvg(node, vg))
        return errors

    @staticmethod
    def _validate_lvm_uuids(source_profile, nodes):

        preamble = '._validate_lvm_uuids: '
        errors = []

        if source_profile.volume_driver != 'lvm':
            return errors

        uuid_data = []
        for node in nodes:
            if not node.storage_profile or \
               node.storage_profile.get_source() != source_profile:
                continue

            unique_pds = []

            for vg in node.storage_profile.volume_groups:
                if vg.is_for_removal():
                    continue

                for pd in vg.physical_devices:
                    if pd.is_for_removal():
                        continue

                    # Here ignore duplicate PD device-names
                    if pd.device_name not in \
                       [unique_pd.device_name for unique_pd in unique_pds]:
                        unique_pds.append(pd)

            for pd in unique_pds:
                disk = VolMgrUtils.get_node_disk_for_pd(node, pd)
                canonical_uuid = VolMgrUtils.get_canonical_uuid(disk)
                if canonical_uuid:
                    # Here ignore duplicate Disk UUIDs within a node
                    if (node, canonical_uuid) not in \
                      [(node1, uuid) for (node1, _, uuid) in uuid_data]:
                        uuid_data.append((node, pd, canonical_uuid))

        if uuid_data:
            unique_uuids = []
            for (_, pd, uuid1) in uuid_data:
                if uuid1 in unique_uuids or KGB == uuid1:
                    continue

                unique_uuids.append(uuid1)

                copies = [dup_pd for (_, dup_pd, uuid2) in uuid_data
                           if uuid2 == uuid1]

                if len(copies) > 1:
                    for dup_pd in copies:
                        msg = ('Disk UUID "%s" must be globally unique ' +
                               'when used by a LVM storage-profile') % uuid1
                        log.trace.debug(preamble + msg)
                        error = ValidationError(error_message=msg,
                                                item_path=dup_pd.get_vpath())
                        errors.append(error)

        return errors

    def validate_profile_globally(self, source_profile, nodes):
        """
        Public driver method to validate a LVM storage-profile globally
        """

        errors = []
        errors += self._validate_lvm_uuids(source_profile, nodes)
        return errors

    def validate_node(self, context, node):
        """
        Public driver method to validate LVM items per node
        for a standard plan
        """
        errors = []
        errors += self._validate_fs_size(node)
        errors += self._validate_xfs_minimum_fs_size(node)
        errors += self._validate_disk_sizes_for_standard_plan(node)
        errors += self._validate_names(node)
        errors += self._validate_ms_root_vg_disks(context, node)
        return errors

    def validate_node_snapshot(self, node):
        """
        Public driver method to validate LVM items per node
        for a snapshot plan
        """
        errors = []
        errors += self._validate_disk_sizes_for_snapshot_plan(node)
        return errors

    def _validate_ms_root_vg_disks(self, context, node):
        """
        Validate that the volume-group called 'vg_root'
        matches what was created by Kickstart
        """

        preamble = '._validate_ms_root_vg_disks: %s: ' % node.hostname

        errors = []

        if not node.is_ms():
            return errors

        vg_data = self._get_vg_data_by_rpc(context, node)
        if not vg_data or ('uuid' not in vg_data):
            return errors

        log.trace.debug((preamble + "Discovered VG data: %s") % vg_data)

        for vg in node.storage_profile.volume_groups:
            vg_pds = [pd for pd in vg.physical_devices]

            if MS_ROOT_VG_GROUP_NAME == vg.volume_group_name:
                log.trace.debug(preamble + "%s is the %s" %
                                (vg.get_vpath(), MS_ROOT_VG_GROUP_NAME))

                if len(vg_pds) != 1:
                    msg = ('The MS "%s" volume-group may only span '
                           'a single physical-device') % vg.volume_group_name
                    log.trace.debug(preamble + msg)
                    error = ValidationError(item_path=vg.get_vpath(),
                                            error_message=msg)
                    errors.append(error)
                else:
                    disk = VolMgrUtils.get_node_disk_for_pd(node, vg_pds[0])
                    if disk is not None:
                        if disk.bootable != 'true':
                            msg = ("The system disk modeled for '%s' "
                                   "volume-group on MS '%s' must have "
                                   "'bootable' set to 'true'") % \
                                   (MS_ROOT_VG_GROUP_NAME, node.get_vpath())
                            log.trace.debug(preamble + msg)
                            error = ValidationError(item_path=disk.get_vpath(),
                                                    error_message=msg)
                            errors.append(error)
                        elif disk.uuid:

                            root_disk_uuid = \
                                VolMgrUtils.get_canonical_uuid(disk)

                            if not root_disk_uuid:
                                root_disk_uuid = '<unknown>'

                            log.trace.debug((preamble +
                                             "The '%s' modeled bootable " +
                                             "disk uuid is '%s'") % \
                                            (MS_ROOT_VG_GROUP_NAME,
                                             root_disk_uuid))

                            if vg_data['uuid'] != root_disk_uuid:
                                msg = ("The system disk '%s' modeled for '%s' "
                                       "volume-group on MS '%s' is invalid; "
                                       "it is not the real Kickstarted "
                                       "device, which has a UUID of '%s'") \
                                       % (root_disk_uuid,
                                          MS_ROOT_VG_GROUP_NAME,
                                          node.get_vpath(),
                                          vg_data['uuid'])
                                log.trace.debug(preamble + msg)
                                error = ValidationError(error_message=msg,
                                                      item_path=vg.get_vpath())
                                errors.append(error)
            else:
                for pd in vg.physical_devices:
                    disk = VolMgrUtils.get_node_disk_for_pd(node, pd)
                    canonical_uuid = VolMgrUtils.get_canonical_uuid(disk)
                    if canonical_uuid and \
                       vg_data['uuid'] == canonical_uuid:
                        msg = ('Non "%s" volume-group "%s" may not use '
                               'the Kickstarted "%s" disk "%s"') % \
                               (MS_ROOT_VG_GROUP_NAME,
                                vg.volume_group_name,
                                MS_ROOT_VG_GROUP_NAME,
                                disk.uuid)
                        log.trace.debug(preamble + msg)
                        error = ValidationError(item_path=vg.get_vpath(),
                                                error_message=msg)
                        errors.append(error)
        return errors

    def _get_vg_data_by_rpc(self, context, ms):
        (stdout, errors) = \
           self._execute_rpc_and_get_output(context,
                                            [ms.hostname],
                                            LV_AGENT,
                                            VG2FACT_ACTION,
                                            {'vgname': MS_ROOT_VG_GROUP_NAME})
        if errors:
            log.trace.error(', '.join(reduce_errs(errors)))
            raise PluginError(', '.join(reduce_errs(errors)))

        return LvmDriver._parse_rpc_output(stdout, ms)

    @staticmethod
    def _parse_rpc_output(stdout, ms):

        preamble = '._parse_rpc_output: %s: ' % ms.hostname

        output = [o for o in stdout[ms.hostname].split('\n')]
        if output:
            # Just a single output line expected
            fact_line = output[0].strip()
        else:
            return None

        log.trace.debug((preamble + "RPC output: %s") % fact_line)

        uuid = None
        regexp = re.compile(r'^disk_(?P<uuid>.+)_part[23]_dev$')
        match = regexp.search(fact_line)
        if match:
            parts = match.groupdict()
            if parts:
                if 'uuid' in parts.keys():
                    uuid = parts['uuid']
                    log.trace.debug((preamble +
                                     "Regexp match, subparts found, UUID: %s")
                                     % uuid)

        return {'fact': fact_line, 'uuid': uuid}

    def gen_tasks_for_ms_non_modeled_ks_snapshots(self, context, msnodes):

        log.trace.debug("gen_tasks_for_ms_kickstart_snapshot")
        tasks = []

        try:
            action = context.snapshot_action()
        except Exception as e:
            # Raise a comprehensive error instead of a traceback
            # in /var/log/messages
            raise PluginError(e)

        for ms in msnodes:
            if action == 'create':
                tasks.extend(self._create_ms_non_modeled_ks_snapshot_tasks(
                                                               context, ms))
            elif action == 'remove':
                tasks.extend(self._delete_ms_non_modeled_ks_snapshot_tasks(
                                                              context, ms))
            elif action == 'restore':
                tasks.extend(self._restore_ms_non_modeled_ks_snapshot_tasks(
                                                               context, ms))

        return tasks

    def _get_ms_non_modeled_ks_fs(self, ms, context):
        """
        Note: This method scans the file systems on the ms using an rpc call.
        Only use this method in snapshot plans where the use of mco agents
        in creating the plan is currently permitted
        """

        file_systems = []

        scanned_file_systems = self._get_scanned_file_systems(context, ms)

        for fs in scanned_file_systems:
            if self._ms_non_modeled_ks_fs(context, ms, fs):
                file_systems.append(fs)
        return file_systems

    def _create_ms_non_modeled_ks_snapshot_tasks(self, context, node):
        tasks = []

        if node.is_for_removal():
            return tasks

        tag = self.get_snapshot_tag(context)
        for fs in self._get_ms_non_modeled_ks_fs(node,
                                           context):
            msg = "Adding task to create snapshot on {0}, fs {1}".\
                                    format(node.hostname, fs['lv_name'])
            log.trace.debug(msg)
            tasks.append(self._gen_lvm_snapshot_callback_task(
                    context,
                    node,
                    fs['size'],
                    VolMgrUtils.gen_snapshot_name(
                        fs['lv_name'],
                        tag),
                    fs['path'])
                )
        # save grub only in upgrade snapshots
        if not tag:
            tasks.append(self._gen_create_grub_backup_task(node))
        return tasks

    @staticmethod
    def _get_modeled_ms_changed_mounts(ms):
        return [] if not ms.storage_profile else \
               [fs.applied_properties.get('mount_point', None)
                for vg in ms.storage_profile.volume_groups
                for fs in vg.file_systems
                if not fs.is_for_removal() and
                   not vg.is_for_removal() and
                   fs.mount_point != fs.applied_properties.get(
                                            'mount_point', None)]

    @staticmethod
    def _get_modeled_ms_mounts(ms):
        return [] if not ms.storage_profile else \
               [fs.mount_point
                for vg in ms.storage_profile.volume_groups
                for fs in vg.file_systems
                if not fs.is_for_removal() and
                   not vg.is_for_removal()]

    @staticmethod
    def _get_expected_ms_non_modeled_fs_snapshots(tag, ms):
        """
        This method returns a list with the *expected* snapshots for
        file systems coming from Kickstart that are not also modeled.
        """

        ms_modeled_fs_mounts = LvmDriver._get_modeled_ms_mounts(ms)

        ms_install_data = VolMgrUtils.get_ms_fs_data()['root']

        expected_snapshots = []
        for fs in ms_install_data['file_systems']:
            snap_size = (fs.deployment_snap_size if tag == '' else
                         fs.named_snap_size)
            if (snap_size == '0' or
                fs.mount_point in ms_modeled_fs_mounts):
                continue
            else:
                prefix = 'lv_' + fs.name
                snap = VolMgrUtils.gen_snapshot_name(prefix, tag)
                expected_snapshots.append((ms_install_data['volume_group'],
                                           snap))

        return expected_snapshots

    def _delete_ms_non_modeled_ks_snapshot_tasks(self, context, node):
        """
        From LITPCDS-9564, MS can have additional
        file systems, all of them modeled in LITP.

        From now on, only the snapshots (named or deployment)
        for file systems coming from initial kickstart
        (root, home and var) are going to be managed for ms_snapshot
        actions.

        Note: modeled filesystems snapshots are managed like a
        regular node file systems.

        File systems which are not modeled or not coming from kickstart
        are not managed for snapshot actions.
        """

        tasks = []

        tag = self.get_snapshot_tag(context)
        expected_snapshots = self._get_expected_ms_non_modeled_fs_snapshots(
                                                                    tag, node)

        for (vg_name, snapshot_name) in expected_snapshots:
            log.trace.debug("Adding task to delete LV %s in VG %s" %
                            (snapshot_name, vg_name))

            task = self._remove_lvm_snapshot_callback_task(
                context, node,
                vg_name, snapshot_name
            )
            tasks.append(task)

        if not tag:
            task = self._gen_del_grub_backup_task(node, False)
            tasks.append(task)

        return tasks

    def _get_scanned_file_systems(self, context, node):
        """
        Note: This method calls get_lv_metadata which scans the file systems
        using an rpc call. Only use this method in snapshot plans
        """
        return self._get_from_lv_metadata(node, context,
                   ['path', 'size', 'lv_name', 'attrs', 'mount'])

    def _restore_ms_non_modeled_ks_snapshot_tasks(self, context, node):
        tasks = []
        #Should we keep legacy behaviour for the moment ??
        #force_restore = False
        force_restore = context.is_snapshot_action_forced()

        if node.is_for_removal():
            return tasks

        tag = self.get_snapshot_tag(context)
        expected_ms_snapshots = \
            self._get_expected_ms_non_modeled_fs_snapshots(tag, node)
        scanned_file_systems = self._get_scanned_file_systems(context, node)

        for (vg, snapshot_name) in expected_ms_snapshots:
            for fs_data in scanned_file_systems:
                log.trace.debug("lv_name is %s" % fs_data['lv_name'])
                if fs_data['lv_name'] == snapshot_name:
                    log.trace.debug("Check if %s is valid" % snapshot_name)
                    valid_snap = VolMgrUtils.check_snapshot_is_valid(
                                fs_data['attrs'])
                    if not valid_snap:
                        error_msg = 'Snapshot "{0}" on node "{1}" has ' \
                                    'become invalid'.format(snapshot_name,
                                                             node.hostname)
                        log.trace.error(error_msg)
                        raise PluginError(error_msg)
                    log.trace.debug(
                        "Adding task to restore lv {0} in vg {1}".\
                        format(snapshot_name, vg))
                    tasks.append(
                        self._restore_lvm_snapshot_callback_task(
                            node, vg, snapshot_name, force_restore
                        )
                    )
        self._log_missing_snapshots(node, expected_ms_snapshots,
                        scanned_file_systems)
        if len(tasks) != len(expected_ms_snapshots):
            log.trace.debug("LEN OF TASKS = %d, len expected %d" % \
                           (len(tasks), len(expected_ms_snapshots)))
            error_msg = 'There are missing snapshots for MS, cannot restore.'
            log.trace.error(error_msg)
            raise PluginError(error_msg)

        tasks.append(self._restore_grub_backup_task(node, force_restore))
        return tasks

    def _log_missing_snapshots(self, node, expected_snapshots,
                        scanned_file_systems):
        found_snapshots = []
        for fs in scanned_file_systems:
            if not self._check_not_snapshot(fs['attrs']):
                found_snapshots.append(fs['lv_name'])
        for (_, snapshot_name) in expected_snapshots:
            if snapshot_name not in found_snapshots:
                error_msg = 'Snapshot "{0}" on node "{1}" is missing'.format(
                                                             snapshot_name,
                                                             node.hostname)
                log.trace.error(error_msg)

    def _check_fs_snapshot_for_tag(self, context, lv_name):
        return not (lv_name == 'lv_var_log' and
                    self.get_snapshot_tag(context) == '')

    def _ms_non_modeled_ks_fs(self, context, ms, fs):

        ms_modeled_fs_mounts = LvmDriver._get_modeled_ms_mounts(ms)

        return (self._check_is_lv_not_swap(context, fs['path'], ms.hostname)
                and self._check_size(fs['size']) and
                self._check_not_snapshot(fs['attrs']) and
                LvmDriver._check_kickstarted_install_fs(fs['lv_name']) and
                fs['mount'] not in ms_modeled_fs_mounts and
                self._check_fs_snapshot_for_tag(context, fs['lv_name']) and
                not fs['lv_name'] == 'lv_software')

    @staticmethod
    def _check_kickstarted_install_fs(lv_name):
        ms_install_data = VolMgrUtils.get_ms_fs_data()
        ms_install_fs_names = ["lv_%s" % fs.name for fs in
                               ms_install_data['root']['file_systems']]
        return lv_name in ms_install_fs_names

    def _check_is_lv_not_swap(self, context, lv, hostname):
        lsblk = self._get_lsblock_data(context, lv, hostname)
        fs_data = {}
        # lsblk command return a string structured like that
        # 'NAME="vg_ms1-lv_swap" FSTYPE="swap" LABEL=""\
        # UUID="f7c8cbc9-f82e-4d50-9d8c-66240e01a46b" MOUNTPOINT="[SWAP]"'
        kv_pairs = lsblk.split(" ")
        for kv_pair in kv_pairs:
            [k, v] = kv_pair.split("=", 1)
            fs_data[k] = v.strip('"')
        return fs_data['FSTYPE'] in LVM_FS_TYPES_NON_SWAP

    @staticmethod
    def _check_size(size):
        # strip the unit and convert to float
        f_size = float(size[:-1])
        return f_size > 0

    @staticmethod
    def _check_not_snapshot(attrs):
        return attrs[0] != "s"

    def gen_tasks_for_modeled_snapshot(self, context, snapshot_model, nodes):

        tasks = []

        action = context.snapshot_action()

        if action == 'create':
            for node in nodes:
                tasks.extend(self._create_snapshot_tasks(context, node))

        elif action == 'remove':

            snap_name = context.snapshot_name()

            # Filter all modeled nodes (peer + MS) that have snappable FSs
            snap_nodes = [node for node in nodes if
                          self._snaps_possible_on_node(node, snap_name)]

            # Generate tasks for snapshot actions on snappable nodes
            for node in snap_nodes:
                tasks.extend(self._remove_snapshot_tasks(context, node))

        elif action == 'restore':
            tasks.extend(
                self._restore_snapshot_tasks(context, snapshot_model, nodes)
            )

        return tasks

    def check_remove_nodes_reachable_task(self, context, nodes):

        snap_name = context.snapshot_name()

        peer_nodes = [node for node in nodes if not node.is_ms() and
                      self._snaps_possible_on_node(node, snap_name)]

        if not context.is_snapshot_action_forced() and peer_nodes:

            hosts = [node.hostname for node in peer_nodes]
            model_item = context.query('snapshot-base', active='true')[0]
            lst = VolMgrUtils.format_list(hosts, quotes_char='double')

            description = 'Check peer nodes {0} are reachable'.format(lst)

            return CallbackTask(
                model_item,
                description,
                self.rpc_callbacks['check_remove_nodes_reachable'],
                hosts,
                tag_name=remove_snapshot_tags.VALIDATION_TAG
            )

        return None

    @staticmethod
    def _generate_snapshot_names_for_modeled_fs(node, vg, fs, tag):
        return {
            'snapshot_name': LvmDriver.generate_lvm_snapshot_name(node, vg,
                                                                  fs, tag)
        }

    @staticmethod
    def _generate_snapshot_name_for_kickstart_fs(fs_name, tag):

        lv = 'lv_' + fs_name

        return {
            'snapshot_name': VolMgrUtils.gen_snapshot_name(lv, tag)
        }

    def check_restore_in_progress_task(self, context, nodes):

        preamble = "_check_restores_in_progress_task: "

        log.trace.debug(
            preamble + "gather snapshot info to be checked in callback")

        # accumulate the file-systems to check
        snap_data = {}
        snap_name = context.snapshot_name()
        snap_tag = self.get_snapshot_tag(context)

        snap_nodes = [node for node in nodes if node.is_ms() or
                      self._snaps_possible_on_node(node, snap_name)]

        template = "Check is FS {id} snapshot merging on {host}"

        if snap_tag:
            template = "Check is FS {id} snapshot '{tag}' merging on {host}"

        # filter file-systems with removable snapshots
        for node in snap_nodes:

            hostname = node.hostname

            msg = "check for modeled FSs on {node}".format(node=hostname)
            log.trace.debug(preamble + msg)

            snap_data[hostname] = {}  # add the hostname

            # Case 1: check all modeled file-systems
            for vg in self._local_storage_backed_vgs(node, snap_name):

                vg_name = vg.volume_group_name
                snap_data[hostname][vg_name] = []

                for fs in vg.file_systems:

                    if self.snap_operation_allowed(fs):

                        msg = template.format(
                            tag=snap_tag, host=hostname, id=fs.item_id)

                        log.trace.debug(preamble + msg)

                        snap_data[hostname][vg_name].append(
                            self._generate_snapshot_names_for_modeled_fs(
                                node, vg, fs, snap_tag
                            )
                        )

            # Case 2: check all kickstarter file-systems
            if node.is_ms():

                msg = "{ms} is an MS check kickstart FSs".format(ms=hostname)
                log.trace.debug(preamble + msg)

                # the root_vg on an MS may or may not be modeled
                if MS_ROOT_VG_GROUP_NAME not in snap_data[hostname]:
                    snap_data[hostname][MS_ROOT_VG_GROUP_NAME] = []

                fs_names = []
                # filter out any FS that's not a lvm type and not swap
                for lvm_type in LVM_FS_TYPES_NON_SWAP:
                    fs_names += VolMgrUtils.get_ms_root_fs_names(
                        fs_type=lvm_type)

                for fs_name in fs_names:

                    msg = template.format(
                        tag=snap_tag, host=hostname, id=fs_name)

                    log.trace.debug(preamble + msg)

                    snap_data[hostname][MS_ROOT_VG_GROUP_NAME].append(
                        self._generate_snapshot_name_for_kickstart_fs(
                            fs_name, snap_tag
                        )
                    )

        model_item = context.query('snapshot-base', active='true')[0]
        description = "Check LVM snapshots are currently not being restored"

        return CallbackTask(
            model_item,
            description,
            self.rpc_callbacks['check_restores_in_progress'],
            snap_data,
            context.is_snapshot_action_forced(),
            tag_name=remove_snapshot_tags.VALIDATION_TAG
        )

    @staticmethod
    def generate_lvm_snapshot_name(node, vg, fs, tag):
        lv_name = LvmDriver.gen_fs_device_name(node, vg, fs)
        return VolMgrUtils.gen_snapshot_name(lv_name, tag)

    @staticmethod
    def _active_item(item):
        # litpcds-12785: this used to check that the item was not in
        # for_removal, but after that bug the behaviour has changed
        return not item.is_initial()

    def _create_snapshot_tasks(self, context, node):

        log.trace.debug("_create_snapshot_tasks")

        create_snapshot_tasks = []

        tag = self.get_snapshot_tag(context)
        msg = "Adding task to create name {tag} snapshot on {host}, FS {id}"

        # check if a tag has been retrieved from the context
        if not tag:
            msg = "Adding task to create snapshot on {host}, FS {id}"

        snap_name = context.snapshot_name()

        # filter active volume groups that are not using any remote storage
        active_vgs = [
            vg for vg in self._local_storage_backed_vgs(node, snap_name)
            if LvmDriver._active_item(vg)
        ]

        # filter active file-systems where snapshots can be created
        for vg in active_vgs:
            for fs in vg.file_systems:
                if LvmDriver._active_item(fs) and \
                   self.snap_operation_allowed(fs):
                    log.trace.debug(
                        msg.format(tag=tag, host=node.hostname, id=fs.item_id)
                    )
                    # generate tasks to create these snapshots
                    create_snapshot_tasks.append(
                        self._gen_lvm_snapshot_callback_task(
                            context,
                            node,
                            VolMgrUtils.compute_snapshot_size(fs),
                            LvmDriver.generate_lvm_snapshot_name(node, vg, fs,
                                                                 tag),
                            LvmDriver.gen_full_fs_dev_name(node, vg, fs)
                        )
                    )

        # LITPCDS-10321: GRUB save for MS is already done at
        # _create_ms_non_modeled_ks_snapshot_tasks
        if create_snapshot_tasks and not tag and not node.is_ms():
            create_snapshot_tasks.append(
                self._gen_create_grub_backup_task(node))

        return create_snapshot_tasks

    def _remove_snapshot_tasks(self, context, node):

        preamble = "_remove_snapshot_tasks: "
        msg = "action forced: %s" % context.is_snapshot_action_forced()

        log.trace.debug(preamble + msg)

        remove_snapshot_tasks = []

        tag = self.get_snapshot_tag(context)
        msg = "Adding task to delete name {tag} snapshot on {host}, FS {id}"

        # check if a tag has been retrieved from the context
        if not tag:
            msg = "Adding task to delete snapshot on {host}, FS {id}"

        snap_name = context.snapshot_name()

        # Filter file-systems with removable snapshots,
        # state doesn't matter for remove
        for vg in self._local_storage_backed_vgs(node, snap_name):
            for fs in vg.file_systems:
                if self.snap_operation_allowed(fs):
                    log.trace.debug(
                        preamble + msg.format(
                            tag=tag, host=node.hostname, id=fs.item_id))

                    # Generate tasks to remove these snapshots
                    snap_names = \
                        LvmDriver._generate_snapshot_names_for_modeled_fs(
                            node, vg, fs, tag)
                    fs_snapname = snap_names["snapshot_name"]

                    remove_snapshot_tasks.append(
                        self._remove_lvm_snapshot_callback_task(
                            context,
                            node,
                            vg_name=vg.volume_group_name,
                            snap_name=fs_snapname,
                        )
                    )

        # LITPCDS-10321: remove GRUB for MS is already done at
        # _delete_ms_non_modeled_ks_snapshot_tasks
        if remove_snapshot_tasks and not tag and not node.is_ms():
            task = self._gen_del_grub_backup_task(
                node, context.is_snapshot_action_forced())
            remove_snapshot_tasks.append(task)

        return remove_snapshot_tasks

    def _restore_snapshot_tasks(self, context, snapshot_model, all_nodes):

        preamble = '_restore_snapshot_tasks: '

        log.trace.debug(preamble)

        snap_name = context.snapshot_name()
        tasks = []

        force_restore = context.is_snapshot_action_forced()

        clusters = snapshot_model.query('cluster')

        for cluster in clusters:
            log.trace.debug(preamble +
                            ("cluster %s nodes are %s" %
                             (cluster.item_id, cluster.nodes)))
            ss_nodes = [n for n in all_nodes
                        if n in cluster.nodes and not n.is_ms()]

            log.trace.debug(preamble +
                            ("cluster %s: nodes on which "
                             "to restore snapshots: %s") %
                            (cluster.item_id, ss_nodes))
            tasks.extend(
                self._restore_lvs_on_nodes(
                    ss_nodes, True, force_restore, snap_name
                )
            )

        model_item = snapshot_model.query('snapshot-base')[0]
        missing_task = self._check_snapshots_exist_callback_task(
            model_item, all_nodes, snap_name, force_restore
        )

        if missing_task:
            tasks.append(missing_task)

        validity_task = self._check_valid_snapshot_callback_task(
            model_item, all_nodes, snap_name, force_restore
        )

        if validity_task:
            tasks.append(validity_task)

        # All management nodes
        ms_es = [n for n in all_nodes if n.is_ms()]

        log.trace.debug(preamble + ("MSs are %s" % ms_es))

        if ms_es:
            ms_tasks = self._restore_lvs_on_nodes(
                ms_es, False, force_restore, snap_name
            )

            if ms_tasks:
                tasks.extend(ms_tasks)
        else:
            log.trace.debug(preamble + "There are no MSs to process")

        return tasks

    def _restore_lvs_on_nodes(self, ss_nodes, include_grub,
                              force_restore, snap_name):
        log.trace.debug("_restore_lvs_on_nodes")

        # ss_nodes contains all the nodes that need snapshot tasks
        tasks = []

        for node in ss_nodes:
            restore_tasks = self._restore_lvs_per_node(node,
                                                       include_grub,
                                                       snap_name,
                                                       force_restore)
            if restore_tasks:
                tasks.extend(restore_tasks)

        return tasks

    def _restore_lvs_per_node(self, node, include_grub, snap_name, forced):
        tasks = []

        for vg in self._local_storage_backed_vgs(node, snap_name):
            for fs in vg.file_systems:
                if self.snap_operation_allowed(fs):
                    msg = ("Adding task to restore snapshot on node %s, " +
                           "file-system %s") % (node.hostname, fs.item_id)
                    log.trace.debug(msg)

                    fs_snapname = VolMgrUtils.gen_snapshot_name(
                                    LvmDriver.gen_fs_device_name(node, vg, fs))

                    tasks.append(
                        self._restore_lvm_snapshot_callback_task(
                            node,
                            vg_name=vg.volume_group_name,
                            snap_name=fs_snapname,
                            forced=forced
                        )
                    )
        # LITPCDS-10321: GRUB restore for MS is already done at
        # _restore_ms_non_modeled_ks_snapshot_tasks
        if include_grub and not node.is_ms() and \
                self._need_to_restore_any_lv_in_node(node, snap_name):
            tasks.append(self._restore_grub_backup_task(node, forced))
        return tasks

    def _need_to_restore_any_lv_in_node(self, node, snap_name):
        for vg in self._local_storage_backed_vgs(node, snap_name):
            for fs in vg.file_systems:
                if self.snap_operation_allowed(fs):
                    return True
        return False

    def _gen_lvm_snapshot_callback_task(self, context, node, size, name, path):
        tag = create_snapshot_tags.PEER_NODE_LVM_VOLUME_TAG

        if node.is_ms():
            tag = create_snapshot_tags.LMS_LVM_VOLUME_TAG

        return CallbackTask(
            node.system if node.system else node,
            'Create LVM {0} snapshot "{1}" on node "{2}"'.format(
                self._get_snapshot_type(self.get_snapshot_tag(context)),
                name,
                node.hostname
            ),
            self.rpc_callbacks['create_snapshot'],
            node.hostname,
            'snapshot',
            'create',
            size=size,
            path=path,
            name=name,
            timeout=DEFAULT_RPC_TIMEOUT,
            tag_name=tag
        )

    def _remove_lvm_snapshot_callback_task(
           self, context, node, vg_name, snap_name):
        tag = remove_snapshot_tags.PEER_NODE_LVM_VOLUME_TAG

        if node.is_ms():
            tag = remove_snapshot_tags.LMS_LVM_VOLUME_TAG

        if context.is_snapshot_action_forced() and not node.is_ms():
            callback_key = 'ping_and_remove_snapshot'
        else:
            callback_key = 'remove_snapshot'

        task = CallbackTask(
            node.system if node.system else node,
            'Remove LVM {0} snapshot "{1}" on node "{2}"'.format(
                self._get_snapshot_type(self.get_snapshot_tag(context)),
                snap_name,
                node.hostname
            ),
            self.rpc_callbacks[callback_key],
            node.hostname,
            vg=vg_name,
            name=snap_name,
            timeout=DEFAULT_RPC_TIMEOUT,
            tag_name=tag
        )

        return task

    def _restore_lvm_snapshot_callback_task(self, node, vg_name,
                                            snap_name, forced):
        tag = restore_snapshot_tags.PEER_NODE_LVM_VOLUME_TAG

        if node.is_ms():
            tag = restore_snapshot_tags.LMS_LVM_VOLUME_TAG

        return CallbackTask(
            node.system if node.system else node,
            'Restore LVM vol "%s" on node "%s"' %
            (snap_name, node.hostname),
            self.rpc_callbacks['restore'],
            [node.hostname],
            'snapshot',
            'restore',
            vg=vg_name,
            name=snap_name,
            timeout=DEFAULT_RPC_TIMEOUT,
            tag_name=tag,
            force=forced
        )

    def _snaps_possible_on_node(self, node, snap_name):
        snaps_possible_on_node = False

        for vg in self._local_storage_backed_vgs(node, snap_name):
            for fs in vg.file_systems:
                if self.snap_operation_allowed(fs):
                    return True

        return snaps_possible_on_node

    def _check_snapshots_exist_callback_task(self, mod_item, ss_nodes,
                                             snap_name, force=False):
        log.trace.debug("_check_snapshots_exist_callback_task %s" % snap_name)

        peer_nodes = [n for n in ss_nodes if
                      self._snaps_possible_on_node(n, snap_name)]
        if peer_nodes and not force:
            return self._gen_check_nodes_and_snaps_task(mod_item,
                                                        peer_nodes,
                                                        snap_name)

    def get_expected_node_snapshots(self, node, snap_name):
        """
        This method returns a list with the *expected* snapshots for
        file systems on a node
        """

        expected_snapshots = []
        if not node:
            return expected_snapshots

        for vg in self._local_storage_backed_vgs(node, snap_name):
            if LvmDriver._active_item(vg):
                for fs in vg.file_systems:
                    if self.snap_operation_allowed(fs):
                        lv_name = LvmDriver.gen_fs_device_name(node, vg, fs)
                        expected_snapshots.append(
                            VolMgrUtils.gen_snapshot_name(lv_name, snap_name))

        log.trace.debug("expected snapshots are %s" % expected_snapshots)
        return expected_snapshots

    def _check_valid_snapshot_callback_task(self,
                                     mod_item, nodes, snap_name, force=False):
        log.trace.debug("_check_valid_snapshot_callback_task %s" % snap_name)
        tag = restore_snapshot_tags.VALIDATION_TAG

        s_nodes = [n for n in nodes if \
                         self._snaps_possible_on_node(n, snap_name)]

        if s_nodes:

            expected_snapshots = {}
            for node in s_nodes:
                expected_snapshots[node.hostname] = dict()
                expected_snapshots[node.hostname]['item_type_id'] = \
                                                 node.item_type_id
                expected_snapshots[node.hostname]['snapshots'] = self.\
                    get_expected_node_snapshots(node, snap_name)

            msg = VolMgrUtils.format_list(
                [node.hostname for node in s_nodes], quotes_char="double"
            )

            msg1 = ("Adding task to check validity of snapshots "
                    "on nodes %s) " % msg)
            log.trace.debug(msg1)

            return CallbackTask(
                mod_item,
                'Check LVM snapshots on node(s) %s are valid' % msg,
                self.rpc_callbacks['check_valid_snaps'],
                [node.hostname for node in s_nodes],
                LV_AGENT,
                LVS_ACTION,
                timeout=DEFAULT_RPC_TIMEOUT,
                snap_name=snap_name,
                tag_name=tag,
                force=force,
                expected_snapshots=expected_snapshots
            )
        return None

    def _gen_check_nodes_and_snaps_task(self, cluster, nodes, snap_name):

        preamble = "_gen_check_nodes_and_snaps_task: "
        log.trace.debug(preamble + snap_name)

        tag = restore_snapshot_tags.VALIDATION_TAG
        nodes_include_ms = False
        ms_name = None

        if not nodes:
            return None

        expected_snapshots = {}
        for node in nodes:
            if node.is_ms():
                nodes_include_ms = True
                ms_name = node.hostname
            expected_snapshots[node.hostname] = dict()
            expected_snapshots[node.hostname]['item_type_id'] = \
                node.item_type_id
            expected_snapshots[node.hostname]['snapshots'] = self.\
                get_expected_node_snapshots(node, snap_name)

        hosts = [n.hostname for n in nodes if not n.is_ms()]
        host_data = [(n.hostname, n.is_ms()) for n in nodes]
        host_csl = VolMgrUtils.format_list(hosts, quotes_char="double")

        txt = ("Creating a task to check peer nodes are reachable " +
               "and snapshots exist on nodes %s ") % host_csl
        log.trace.debug(preamble + txt)

        if hosts and nodes_include_ms:
            desc = 'Check peer node(s) {0} are reachable with all LVM ' \
                   'snapshots present and LVM snapshots are present for ' \
                   'MS "{1}" modelled file systems'.format(host_csl, ms_name)
        elif hosts and not nodes_include_ms:
            desc = 'Check peer node(s) {0} are reachable with all LVM ' \
                   'snapshots present'.format(host_csl)
        elif not hosts and nodes_include_ms:
            desc = 'Check LVM snapshots are present for ' \
                   'MS "{0}" modelled file systems'.format(ms_name)

        callback_key = 'nodes_reachable_and_snaps_exist'
        return CallbackTask(cluster,
                            desc,
                            self.rpc_callbacks[callback_key],
                            host_data,
                            'lv',
                            'lvs',
                            timeout=DEFAULT_RPC_TIMEOUT,
                            snap_name=snap_name,
                            tag_name=tag,
                            expected_snapshots=expected_snapshots
                            )

    def _gen_create_grub_backup_task(self, node):
        return self._grub_task(node, 'save_grub')

    def _gen_del_grub_backup_task(self, node, forced):
        return self._grub_task(node, 'remove_grub', forced)

    def _restore_grub_backup_task(self, node, forced):
        return self._grub_task(node, 'restore_grub', forced)

    def _generate_grub_tag(self, node, action):
        tag = None
        tag_set = None

        if action == 'remove_grub':
            tag_set = remove_snapshot_tags

        elif action == 'restore_grub':
            tag_set = restore_snapshot_tags

        elif action == 'save_grub':
            tag_set = create_snapshot_tags

        if node.is_ms():
            tag = tag_set.LMS_LVM_VOLUME_TAG
        else:
            tag = tag_set.PEER_NODE_LVM_VOLUME_TAG
        return tag

    def _grub_task(self, node, action, forced=False):

        tag = self._generate_grub_tag(node, action)

        if action == 'remove_grub':

            if forced:
                callback_key = 'ping_and_remove_grub'
            else:
                callback_key = 'remove_grub'
            return CallbackTask(
                node.system if node.system else node,
                'Remove grub copy on node "{0}"'.format(node.hostname),
                self.rpc_callbacks[callback_key],
                node.hostname,
                timeout=DEFAULT_RPC_TIMEOUT,
                tag_name=tag
            )

        elif action == 'restore_grub':
            if forced:
                callback_key = 'ping_and_restore_grub'
            else:
                callback_key = 'restore_grub'
            return CallbackTask(
                node.system if node.system else node,
                'Restore grub copy on node "{0}"'.format(node.hostname),
                self.rpc_callbacks[callback_key],
                [node.hostname],
                'snapshot',
                action,
                timeout=DEFAULT_RPC_TIMEOUT,
                tag_name=tag
            )

        else:
            return CallbackTask(
                node.system if node.system else node,
                'Save grub on node "{0}"'.format(node.hostname),
                self.rpc_callbacks['base'],
                [node.hostname],
                'snapshot',
                action,
                timeout=DEFAULT_RPC_TIMEOUT,
                tag_name=tag
            )

    def snap_operation_allowed(self, fs):
        return fs.type in LVM_FS_TYPES_NON_SWAP and \
               float(fs.current_snap_size) > 0 and \
               fs.snap_external == 'false'

    def _get_from_lv_metadata(self, node, context, keys_to_retrieve):

        lv_metadata = self._get_lv_metadata(node, context)

        A = set(keys_to_retrieve)
        B = set(VolMgrUtils.lv_scan_line_keys)

        # this doesn't quite look right to me, there is now checking to see
        # that the keys that are being requested are appropriate, you could
        # pass any nonsense in and everything will work fine
        subset = A.intersection(B)

        if subset:
            return [
                dict([(key, lv[key]) for key in subset])
                for lv in lv_metadata
            ]

        return []

    def _get_lv_metadata(self, node, context):
        """
        Using an mco agent get a list of all lvm volumes on the node
        """

        action_output, _ = self._execute_rpc_and_get_output(
            context, [node.hostname], LV_AGENT, LVS_ACTION
        )

        # we actually expect only one node
        # exclude any empty strings that are returned
        return [
            VolMgrUtils.parse_lv_scan_line(line)
            for line in action_output[node.hostname].split('\n') if line
        ]

    def _get_lsblock_data(self, context, lv, hostname):

        out, errors = self._execute_rpc_and_get_output(
            context, [hostname], LV_AGENT, LSBLK_ACTION, {"path": lv}
        )

        if errors:
            log.trace.error(', '.join(errors))
            raise PluginError(', '.join(errors))

        return out[hostname]

    def _execute_rpc_and_get_output(self, context, nodes, agent, action,
                                   action_kwargs=None, timeout=None):
        try:
            return RpcCommandOutputProcessor().execute_rpc_and_process_result(
                      context, nodes, agent, action, action_kwargs, timeout
                                                                              )
        except RpcExecutionException as e:
            raise PluginError(e)
