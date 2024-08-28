##############################################################################
# COPYRIGHT Ericsson AB 2013
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

import re
from litp.core.extension import ViewError
from litp.core.litp_logging import LitpLogger
from litp.core.constants import UPGRADE_SNAPSHOT_NAME


MAX_VXVM_VOL_LENGTH = 31
MAX_LVM_VOL_LENGTH = 126
MAX_LVM_SNAP_VOL_LENGTH = 122
LITP_EXTRA_CHARS = 3
TAG_RESERVED_LENGTH = 5
MS_ROOT_VG_GROUP_NAME = "vg_root"
DEFAULT_RPC_TIMEOUT = 400
EXPIRED_STR = "execution expired"
LVM_FS_TYPES_NON_SWAP = ('ext4', 'xfs')
LVM_FS_TYPES = LVM_FS_TYPES_NON_SWAP + ('swap',)

ROOT_FS_MOUNT_POINT = '/'

log = LitpLogger()


class VolMgrUtils(object):
    """
    Simple utilities for various file system requirements.
    """

    lv_scan_line_keys = ['path', 'size', 'attrs',
                         'mount', 'vg_name', 'lv_name']

    class MsFs(object):

        def __init__(self, fs_type, size, deployment_snap_size,
                           named_snap_size, name, mount_point):
            object.__init__(self)
            self.fs_type = fs_type
            self.size = size
            self.deployment_snap_size = deployment_snap_size
            self.named_snap_size = named_snap_size
            self.name = name
            self.mount_point = mount_point

        def __repr__(self):
            return "Name:%s, Size:%s, MP:%s, Type:%s." % \
                   (self.name, self.size, self.mount_point, self.fs_type)

    class MsPd(object):

        def __init__(self, name, size, grow, fs_type=''):
            object.__init__(self)
            self.name = name
            self.size = size
            self.fs_type = fs_type
            self.grow = grow

        def __repr__(self):
            return "Name:%s, Size:%s, Grow:%s, Type:%s." % \
                   (self.name, self.size, self.grow, self.fs_type)

    @staticmethod
    def get_intersection_items_by_itemtype(snapshot_model, context, itemtype):
        return [item1 for item1 in snapshot_model.query(itemtype)
                for item2 in context.query(itemtype)
                if str(item1.vpath) == str(item2.vpath) and not
                item2.is_initial()]

    @staticmethod
    def is_the_rootvg(node, vg):
        """
        Check if a VG is the vg-root for the OS of a node
        """
        return VolMgrUtils.get_root_vg_name_from_modeled_vgs(node,
                                                             vg) is not None

    @staticmethod
    def _get_mn_root_vg_name(node):
        try:
            return node.storage_profile.view_root_vg
        except ViewError:
            pass

    @staticmethod
    def get_root_vg_name_from_modeled_vgs(node, vg):
        """
        Return volume_group_name of root volume-group modeled for node.

        Returns None if root volume-group was not modeled, for example for
        MS when supplementary profile does not present.
        """
        if node.is_ms():
            if MS_ROOT_VG_GROUP_NAME == vg.volume_group_name:
                preamble = ('.get_root_vg_name_from_modeled_vgs:'
                            ' %s %s VG:%s : ') % \
                            (node.item_type_id, node.item_id, vg.item_id)
                log.trace.debug("%sMS '%s' volume-group.",
                                preamble, MS_ROOT_VG_GROUP_NAME)
                return MS_ROOT_VG_GROUP_NAME
        else:
            root_vg_name = VolMgrUtils._get_mn_root_vg_name(node)
            if root_vg_name and (root_vg_name == vg.volume_group_name):
                return root_vg_name

    @staticmethod
    def get_node_root_vg_name(node):
        """
        Return volume_group_name of root volume-group modeled for node.

        Searches for volume-group in node's storage profile.

        Returns None if root volume-group was not modeled.

        This method should be used when no volume group is known in advance,
        to avoid repeated access to view_root_vg in case of managed node.
        """
        if node.is_ms():
            for vg in node.storage_profile.volume_groups:
                if vg.volume_group_name == MS_ROOT_VG_GROUP_NAME:
                    return vg.volume_group_name
        else:
            return VolMgrUtils._get_mn_root_vg_name(node)

    @staticmethod
    def system_disks(node):
        """
        Ignore the abstract base type - only select
        instances of extensions of the base type.
        """
        if hasattr(node, 'system') and node.system:
            return [drive for drive in node.system.disks
                    if drive.item_type_id != 'disk-base']
        return []

    @staticmethod
    def get_size_megabytes(size_units):
        """
        Utility method to convert a combined size-and-unit string
        into a Numeric Megabytes value
        :param size_units: Combined size-and-unit string
        :type size_units: String
        :return: Numeric size in Megabytes
        :rtype: Integer
        """

        pattern = r'^\s*(?P<size>[1-9][0-9]*)\s*(?P<unit>[MGT])\s*$'
        regexp = re.compile(pattern)

        match = regexp.search(size_units)

        if match:
            parts = match.groupdict()
            if parts:
                if 'size' in parts.keys():
                    size = int(parts['size'])

                    if 'unit' in parts.keys():
                        unit = parts['unit']
                        if unit == 'M':
                            size *= 1
                        elif unit == 'G':
                            size *= 1024
                        elif unit == 'T':
                            size *= 1024 * 1024
                        return size
        else:
            return 0

    @staticmethod
    def get_canonical_uuid(disk):
        """
        For a disks object return its UUID in a canonical or normal form.
        This should be done before any comparisons of UUIDs.
        """
        if not disk or not disk.uuid or disk.item_type_id == 'disk-base':
            return None
        else:
            return disk.uuid.strip().lower().replace('-', '_')

    @staticmethod
    def get_node_disk_for_pd(node, pd):
        """
        Iterate the node system disks and locate the exact disk
        referenced by the physical device.
        """
        for disk in VolMgrUtils.system_disks(node):
            if disk.name and (disk.name == pd.device_name):
                return disk

    @staticmethod
    def get_disks_for_pds(pds, node):
        disks = []
        for disk in VolMgrUtils.system_disks(node):
            for pd in pds:
                if disk.name and (disk.name == pd.device_name):
                    disks.append(disk)
        return disks

    @staticmethod
    def get_pd_for_disk(vg, disk):
        for pd in vg.physical_devices:
            if not pd.is_for_removal()and (pd.device_name == disk.name):
                return pd

    @staticmethod
    def _is_disk_size_decreased(disk):

        if "size" in disk.applied_properties:

            applied_size = disk.applied_properties["size"]
            old_size = VolMgrUtils.get_size_megabytes(applied_size)
            new_size = VolMgrUtils.get_size_megabytes(disk.size)

            return new_size < old_size

        return False

    @staticmethod
    def gen_snapshot_name(volume_name, snapshot_name=None):
        if snapshot_name and snapshot_name != UPGRADE_SNAPSHOT_NAME:
            name = "L_{volume_name}_{snapshot_name}".format(
                volume_name=volume_name,
                snapshot_name=snapshot_name)
        else:
            name = "L_{volume_name}_".format(volume_name=volume_name)
        return name

    @staticmethod
    def _gen_cache_object_name(volume_name, snapshot_name=None):
        name = VolMgrUtils.gen_snapshot_name(volume_name, snapshot_name)
        name = name.replace('L_', 'LO', 1)
        return name

    @staticmethod
    def _gen_cache_volume_name(volume_name, snapshot_name=None):
        name = VolMgrUtils.gen_snapshot_name(volume_name, snapshot_name)
        name = name.replace('L_', 'LV', 1)
        return name

    @staticmethod
    def compute_snapshot_size(fs, driver_type='lvm'):

        fs_size = str(VolMgrUtils.get_size_megabytes(fs.size))

        return_value = float(fs_size) * float(fs.current_snap_size) / 100.0
        if 'vxvm' == driver_type:
            return_value = int(return_value)

        return str(return_value) + 'M'

    @staticmethod
    def get_ms_root_fss():
        return [
          VolMgrUtils.MsFs('xfs',  '70G', '100', '100', 'root', '/'),
          VolMgrUtils.MsFs('xfs',  '12G', '100', '100', 'home', '/home'),
          VolMgrUtils.MsFs('swap',  '2G',   '0',   '0', 'swap', 'swap'),
          VolMgrUtils.MsFs('xfs',  '10G', '100', '100', 'var',  '/var'),
          VolMgrUtils.MsFs('xfs',  '35G', '100', '100', 'var_tmp', '/var/tmp'),
          VolMgrUtils.MsFs('xfs',  '15G', '100', '100', 'var_opt_rh',
                                                          '/var/opt/rh'),
          VolMgrUtils.MsFs('xfs',   '7G', '100', '100', 'var_lib_puppetdb',
                                                          '/var/lib/puppetdb'),
          VolMgrUtils.MsFs('xfs',  '20G',   '0', '100', 'var_log', '/var/log'),
          VolMgrUtils.MsFs('xfs', '140G', '100', '100', 'var_www', '/var/www'),
          VolMgrUtils.MsFs('xfs', '150G',   '0',   '0', 'software',
                                                          '/software')
               ]

    @staticmethod
    def get_ms_root_fs_names(fs_type=''):

        if fs_type:
            return [fs.name for fs in VolMgrUtils.get_ms_root_fss()
                    if fs.fs_type == fs_type]

        return [fs.name for fs in VolMgrUtils.get_ms_root_fss()]

    @staticmethod
    def get_ks_fs_max_length():

        max_length = 0

        ks_fs_names = [ks_fs.name
                       for ks_fs in VolMgrUtils.get_ms_root_fss()
                       if ks_fs.fs_type in LVM_FS_TYPES_NON_SWAP]
        for fs_item_id in ks_fs_names:

            fs_name = '{0}/lv_{1}'.format(MS_ROOT_VG_GROUP_NAME, fs_item_id)

            if len(fs_name) > max_length:
                max_length = len(fs_name) + fs_name.count('-')

        return max_length

    @staticmethod
    def get_ms_root_pds():
        return [VolMgrUtils.MsPd('pv.008002', '1M',   True)]

    @staticmethod
    def get_ms_boot_pds():
        return [VolMgrUtils.MsPd('/boot', '1000M', False, 'xfs')]

    @staticmethod
    def get_ms_fs_data():
        return {
            'boot': {
                'volume_group': None,
                'file_systems': [],
                'physical_devices': VolMgrUtils.get_ms_boot_pds(),
            },
            'root': {
                'volume_group': MS_ROOT_VG_GROUP_NAME,
                'file_systems': VolMgrUtils.get_ms_root_fss(),
                'physical_devices': VolMgrUtils.get_ms_root_pds(),
            },
        }

    @staticmethod
    def get_kickstart_fss():
        ks_fss = []
        ms_ks_data = VolMgrUtils.get_ms_fs_data()
        ks_groups = ms_ks_data.keys()
        for ks_group in ks_groups:
            for ks_fs in ms_ks_data[ks_group]['file_systems']:
                ks_fss.append(ks_fs)
        return ks_fss

    @staticmethod
    def is_a_ks_filesystem(vg, fs):
        if MS_ROOT_VG_GROUP_NAME == vg.volume_group_name:
            ks_fss = VolMgrUtils.get_kickstart_fss()
            for ks_fs in ks_fss:
                if ks_fs.mount_point == fs.mount_point:
                    return True
        return False

    @staticmethod
    def get_ks_filesystem_size(fs):
        ks_fss = VolMgrUtils.get_kickstart_fss()
        for ks_fs in ks_fss:
            if ks_fs.mount_point == fs.mount_point:
                return ks_fs.size
        return None

    @staticmethod
    def _get_modeled_ks_filesystems(profile):
        return [fs for vg in profile.volume_groups
                for fs in vg.file_systems
                if VolMgrUtils.is_a_ks_filesystem(vg, fs)]

    @staticmethod
    def _mount_point_has_changed(fs):

        if not fs.is_updated():
            return False

        mount_point = fs.mount_point if hasattr(fs, 'mount_point') else None

        return mount_point != fs.applied_properties.get('mount_point')

    @staticmethod
    def _fsck_pass_has_changed(fs):

        if not fs.is_updated():
            return False

        fsck_pass = fs.fsck_pass if hasattr(fs, 'fsck_pass') else None

        return fsck_pass != fs.applied_properties.get('fsck_pass')

    @staticmethod
    def _mount_options_has_changed(fs):
        """
        Utility method to detect if mount_options prop has changed
        :param fs: file system
        :type fs: file-system Item type
        :return: True or False
        :rtype: boolean
        """
        if not fs.is_updated():
            return False

        mount_options = fs.mount_options if hasattr(
            fs, 'mount_options') else None

        return mount_options != fs.applied_properties.get(
            'mount_options')

    @staticmethod
    def _mount_affecting_properties_have_changed(fs):
        return VolMgrUtils._mount_point_has_changed(fs) or \
               VolMgrUtils._fsck_pass_has_changed(fs) or \
               VolMgrUtils._mount_options_has_changed(fs)

    @staticmethod
    def _grub_lv_enable_added(cluster):
        """
        This method checks whether grub_lv_enable is introduced
        in the cluster.

        :param cluster
        :return True or False
        """
        grub_lv_enable = hasattr(cluster, 'grub_lv_enable')
        return cluster.is_updated() and \
               'grub_lv_enable' not in cluster.applied_properties \
               and grub_lv_enable

    @staticmethod
    def _item_property_has_changed(item, property_name):
        return item.is_updated() and \
               property_name in item.applied_properties and \
               getattr(item, property_name) != \
               item.applied_properties.get(property_name, None)

    @staticmethod
    def vg_pds(vg):
        return [pd for pd in vg.physical_devices if not pd.is_for_removal()]

    @staticmethod
    def vg_system(vg):
        the_system = None
        if vg.get_ms() is not None:
            the_system = vg.get_ms().system
        elif vg.get_node() is not None:
            the_system = vg.get_node().system
        elif vg.get_cluster() is not None:
            # All nodes should share the same VxVM disks
            if len(vg.get_cluster().nodes) > 0:
                the_system = [x for x in vg.get_cluster().nodes][0].system
        return the_system

    @staticmethod
    def check_snapshot_is_valid(attrs):
        if attrs[4] != "I":
            return True
        else:
            return False

    @staticmethod
    def parse_lvscan_line(line):
        [lv, size, attrs, mount] = [s for s in line.split(" ") if len(s) > 0]
        vg_name, lv_name = lv.split("/")[-2:]
        return lv, size.upper(), vg_name, lv_name, attrs, mount

    @staticmethod
    def parse_lv_scan_line(line):
        path, size, attrs, mount = line.split()
        vg_name, lv_name = path.split('/')[-2:]
        lv_scan_line_values = [path, size.upper(), attrs, mount,
                               vg_name, lv_name]
        return dict(zip(VolMgrUtils.lv_scan_line_keys, lv_scan_line_values))

    @staticmethod
    def format_list(lst, quotes_char='none'):
        if lst:
            char = {'single': '\'', 'double': '\"'}
            qmark = char.get(quotes_char, '')
            t0 = qmark + "%s" + qmark
            t1 = t0 + ", "
            t2 = t0 + " and "
            template = t1 * len(lst[:-2]) + t2 * len(lst[-2:-1]) + t0
            return template % tuple(lst)
        return ""

    @staticmethod
    def get_disk_size_delta(disk_item):
        current_size = VolMgrUtils.get_size_megabytes(disk_item.size)
        applied_size = VolMgrUtils.get_size_megabytes(
                disk_item.applied_properties.get("size", 0))
        return current_size - applied_size

    @staticmethod
    def has_disk_size_increased(disk_item):
        return VolMgrUtils.get_disk_size_delta(disk_item) > 0

    @staticmethod
    def has_disk_size_decreased(disk_item):
        return VolMgrUtils.get_disk_size_delta(disk_item) < 0
