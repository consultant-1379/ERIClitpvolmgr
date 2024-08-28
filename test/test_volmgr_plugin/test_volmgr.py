##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

import unittest
import time

from nose.tools import nottest
from litp.core.plugin_manager import PluginManager
from litp.core.model_manager import ModelManager
from litp.core.plugin_context_api import PluginApiContext, NoSnapshotItemError
from litp.core.validators import ValidationError
from litp.extensions.core_extension import CoreExtension
from litp.core.execution_manager import CallbackExecutionException
from volmgr_extension.volmgr_extension import VolMgrExtension

import litp.core.node_utils as coreutils

from volmgr_plugin.volmgr_plugin import (
    VolMgrPlugin,
    IgnoredCodesAgentProcessor,
    Timeout
)
from volmgr_plugin.drivers.lvm import LvmDriver

from volmgr_plugin.volmgr_utils import (
    VolMgrUtils,
    MAX_VXVM_VOL_LENGTH,
    MAX_LVM_SNAP_VOL_LENGTH,
    LITP_EXTRA_CHARS
)
from volmgr_plugin.drivers.vxvm import DgNotImportedError

from litp.core.execution_manager import CallbackTask, PluginError
from litp.core.rpc_commands import RpcExecutionException
from litp.core.rpc_commands import RpcCommandOutputNoStderrProcessor, RpcCommandOutputProcessor
from litp.core.model_type import ItemType
from litp.core.model_type import Property
from litp.core.callback_api import CallbackApi
from litp.core.execution_manager import ExecutionManager
from litp.core.model_type import Collection, PropertyType

from mock import MagicMock, patch, Mock

from mock_items import (
    MockStorageProfile,
    MockVG,
    MockNode,
    MockCluster,
    MockFs,
)

from mock_vol_items import (
    VolMgrMock,
    VolMgrMockContext,
    VolMgrMockCluster,
    VolMgrMockVCSCluster,
    VolMgrMockDeployment,
    VolMgrMockMS,
    VolMgrMockNode,
    VolMgrMockSystem,
    VolMgrMockStorageProfile,
    VolMgrMockDisk,
    VolMgrMockOtherDisk,
    VolMgrMockPD,
    VolMgrMockVG,
    VolMgrMockFS,
    MockConfigTask,
    MockCallbackTask
)


class VolMock(Mock):
    def __init__(self, **kwargs):
        super(VolMock, self).__init__(**kwargs)
        self.get_vpath=lambda: "/%s/%s" % (self.item_type_id, self.item_id)

    def get_ms(self):
        return None

    def get_cluster(self):
        return None


class TestVolMgrPlugin(unittest.TestCase):

    @staticmethod
    def _set_state_xxx(items, state):
        for item in items:
            if 'applied' == state:
                item.is_applied = lambda: True
                item.is_for_removal = lambda: False
                item.is_updated = lambda: False
                item.is_initial = lambda: False
            elif 'removed' == state:
                item.is_applied = lambda: False
                item.is_for_removal = lambda: True
                item.is_updated = lambda: False
                item.is_initial = lambda: False
            elif 'updated' == state:
                item.is_applied = lambda: False
                item.is_for_removal = lambda: False
                item.is_updated = lambda: True
                item.is_initial = lambda: False
            elif 'initial' == state:
                item.is_applied = lambda: False
                item.is_for_removal = lambda: False
                item.is_updated = lambda: False
                item.is_initial = lambda: True

    @staticmethod
    def _set_state_applied(items):
        TestVolMgrPlugin._set_state_xxx(items, 'applied')

    @staticmethod
    def _set_state_updated(items):
        TestVolMgrPlugin._set_state_xxx(items, 'updated')

    @staticmethod
    def _set_state_initial(items):
        TestVolMgrPlugin._set_state_xxx(items, 'initial')

    @staticmethod
    def _set_state_removed(items):
        TestVolMgrPlugin._set_state_xxx(items, 'removed')

    def setUp(self):
        """
        Construct a model manager, sufficient for test cases
        that you wish to implement in this suite.
        """
        self.model_manager = ModelManager()
        self.plugin_manager = PluginManager(self.model_manager)
        self.context = PluginApiContext(self.model_manager)

        core_ext = CoreExtension()
        volmgr_ext = VolMgrExtension()

        for ext in [core_ext, volmgr_ext]:
            self.plugin_manager.add_property_types(ext.define_property_types())
            self.plugin_manager.add_item_types(ext.define_item_types())

        # Add default minimal model (which creates '/' root item)
        self.plugin_manager.add_default_model()

        # Instantiate your plugin and register with PluginManager
        self.plugin = VolMgrPlugin()
        self.plugin_manager.add_plugin('TestPlugin',
                                       'some.test.plugin',
                                       '1.0.0',
                                       self.plugin)
        self._register_lun_type()

    def _register_lun_type(self):
        self.model_manager.register_item_type(ItemType(
                "lun-disk",
                extend_item="disk-base",
                item_description="A lun disk item.",
                bootable=Property(
                    "basic_boolean",
                    default="false",
                    prop_description="Set to true if this disk is the system" \
                    " bootup device. This must be set for exactly one disk."
                    ),
                name=Property(
                    "basic_string",
                    prop_description="Device name of this disk.",
                    required=True,
                ),
                size=Property(
                    "disk_size",
                    required=True,
                    prop_description="Size of this disk.",
                ),
                uuid=Property(
                    "disk_uuid",
                    prop_description="UUID of this disk.",
                    required=False,
                    site_specific=True
                ),
                disk_part=Property("basic_boolean",
                    prop_description='Disk has partitions.',
                    updatable_plugin=True,
                    updatable_rest=False,
                    required=False,
                    default="false",
                ),
            ),
            )

    # TODO (igor): Too complex! 11 McCabe index.
    def _create_storage_profile_items(self, profile, system, data):
        if profile:
            profile_url = profile.get_vpath()
            for vg in data['VGs']:
                vg_url = profile_url + '/volume_groups/' + vg['id']

                rsp = self.model_manager.create_item('volume-group',
                                                     vg_url,
                                                     volume_group_name=vg['name'])

                self.assertFalse(isinstance(rsp, list), rsp)
                if 'FSs' in vg:
                    for fs in vg['FSs']:
                        fs_url = vg_url + '/file_systems/' + fs['id']

                        snap_size = '0'
                        if 'lvm' == profile.volume_driver and \
                                fs['type'] in ['ext4', 'xfs']:
                            if 'snap_size' in fs:
                                snap_size = fs['snap_size']
                            else:
                                snap_size = '10'
                        if 'mount_options' in fs:
                            rsp = self.model_manager.create_item(
                                'file-system',
                                fs_url,
                                type=fs['type'],
                                mount_point=fs['mp'],
                                size=fs['size'],
                                snap_size=snap_size,
                                snap_external=fs.get('snap_external', 'false'),
                                mount_options=fs['mount_options']
                            )
                        else:
                            rsp = self.model_manager.create_item(
                                'file-system',
                                fs_url,
                                type=fs['type'],
                                mount_point=fs['mp'],
                                size=fs['size'],
                                snap_size=snap_size,
                                snap_external=fs.get('snap_external', 'false')
                            )

                        self.assertFalse(isinstance(rsp, list), rsp)

                if 'PDs' in vg:
                    for pd in vg['PDs']:
                        pd_url = vg_url + '/physical_devices/' + pd['id']
                        rsp = self.model_manager.create_item('physical-device',
                                                             pd_url,
                                                             device_name=pd['device'])
                        self.assertFalse(isinstance(rsp, list), rsp)

        if system:
            sys_url = system.get_vpath()
            for disk in data['disks']:
                disk_url = sys_url + '/disks/' + disk['id']
                rsp = self.model_manager.create_item(disk.get('item_type_id', 'disk'),
                                                     disk_url,
                                                     bootable=disk['bootable'],
                                                     uuid=disk['uuid'],
                                                     name=disk['name'],
                                                     size=disk['size'],
                                                     disk_part='false')
                self.assertFalse(isinstance(rsp, list), rsp)

    def setup_model(self, link_node_to_system=True, volume_driver_type='lvm'):

        rsp = self.model_manager.create_item('deployment', '/deployments/d1')
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.create_item('cluster',
                                             '/deployments/d1/clusters/c1')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.cluster1 = rsp

        n1_url = '/deployments/d1/clusters/c1/nodes/n1'

        rsp = self.model_manager.create_item('node', n1_url,
                                             hostname='node1')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.node1 = rsp

        s1_url = '/infrastructure/systems/s1'
        sys_name = 'MN1SYS'
        rsp = self.model_manager.create_item('system',
                                             s1_url,
                                             system_name=sys_name)
        self.assertFalse(isinstance(rsp, list), rsp)
        self.system1 = rsp

        sp1_name = 'storage_profile_1'
        rsp = self.model_manager.create_item('storage-profile',
                                             '/infrastructure/storage/storage_profiles/sp1',
                                             volume_driver=volume_driver_type)
        self.assertFalse(isinstance(rsp, list), rsp)
        self.sp1 = rsp

        rsp = self.model_manager.create_inherited(
            '/infrastructure/storage/storage_profiles/sp1',
            n1_url + '/storage_profile')
        self.assertFalse(isinstance(rsp, list), rsp)

        if link_node_to_system:
            rsp = self.model_manager.create_inherited(s1_url,
                                                      n1_url + '/system')
            self.assertFalse(isinstance(rsp, list), rsp)
        rsp = self.model_manager.create_item('snapshot-base',
                                             '/snapshots/snapshot')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.context.snapshot_name = MagicMock(return_value='snapshot')

    def _create_dataset_primary_disk(self):

        disk1_name = 'primary'

        storage_data = \
            {'VGs': [{
                'id': 'vg1',
                'name': 'root_vg',
                'FSs': [{'id': 'fs1', 'type': 'xfs', 'mp': '/',     'size': '10G', 'snap_size': '20'},
                        {'id': 'fs2', 'type': 'swap', 'mp': 'swap',  'size': '2G', 'snap_size': '50'},
                        {'id': 'fs3', 'type': 'ext4', 'mp': '/home', 'size': '14G', 'snap_size': '50'},
                        {'id': 'fs4', 'type': 'xfs', 'mp': '/var', 'size': '4G', 'snap_size': '50', 'mount_options':'defaults'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]}],
            'disks': [{'id': 'disk1', 'bootable': 'true', 'uuid': 'ABCD_1234',
                       'name': disk1_name, 'size': '53G', 'disk_part': 'false'}]}

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

    def _create_vxvm_dataset_duplicate_pd(self):

        disk1_name = 'primary'

        storage_data = \
            {'VGs': [{
                'id': 'vg1',
                'name': 'root_vg',
                'FSs': [{'id': 'fs1', 'type': 'vxfs', 'mp': '/',     'size': '10G', 'snap_size': '100'},
                        {'id': 'fs3', 'type': 'vxfs', 'mp': '/home', 'size': '14G', 'snap_size': '100'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]},
                {
                'id': 'vg2',
                'name': 'root2_vg',
                'FSs': [{'id': 'fs1', 'type': 'vxfs', 'mp': '/',     'size': '10G', 'snap_size': '100'},
                        {'id': 'fs3', 'type': 'vxfs', 'mp': '/home', 'size': '14G', 'snap_size': '100'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]}

                ],
            'disks': [{'id': 'disk1', 'bootable': 'false', 'uuid': 'ABCD_1234',
                       'name': disk1_name, 'size': '53G'}]}

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

    def _create_vxvm_dataset_multiple_disks(self):

        disk1_name = 'primary'
        disk2_name = 'secondary'

        storage_data = \
            {'VGs': [{
                'id': 'vg1',
                'name': 'root_vg',
                'FSs': [{'id': 'fs1', 'type': 'vxfs', 'mp': '/',     'size': '10G', 'snap_size': '100'},
                        {'id': 'fs3', 'type': 'vxfs', 'mp': '/home', 'size': '14G', 'snap_size': '100'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]},
                {
                'id': 'vg2',
                'name': 'root2_vg',
                'FSs': [{'id': 'fs1', 'type': 'vxfs', 'mp': '/',     'size': '10G', 'snap_size': '100'},
                        {'id': 'fs3', 'type': 'vxfs', 'mp': '/home', 'size': '14G', 'snap_size': '100'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}, {'id': 'pd2', 'device': disk2_name}]}

                ],
            'disks': [{'id': 'disk1', 'bootable': 'false', 'uuid': 'ABCD_1234',
                       'name': disk1_name, 'size': '53G'},
                     {'id': 'disk2', 'bootable': 'false', 'uuid': 'EFGH_5678',
                       'name': disk2_name, 'size': '50G'}]}

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

    def _create_vxvm_dataset_unique_pd(self):

        disk1_name = 'primary'
        disk2_name = 'secondary'

        storage_data = \
            {'VGs': [{
                'id': 'vg1',
                'name': 'root_vg',
                'FSs': [{'id': 'fs1', 'type': 'vxfs', 'mp': '/',     'size': '10G', 'snap_size': '100'},
                        {'id': 'fs3', 'type': 'vxfs', 'mp': '/home', 'size': '14G', 'snap_size': '100'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]}],
              'VGs': [{
                'id': 'vg2',
                'name': 'root2_vg',
                'FSs': [{'id': 'fs1', 'type': 'vxfs', 'mp': '/',     'size': '10G', 'snap_size': '100'},
                        {'id': 'fs3', 'type': 'vxfs', 'mp': '/home', 'size': '14G', 'snap_size': '100'}],
                'PDs': [{'id': 'pd1', 'device': disk2_name}]}],
            'disks': [{'id': 'disk1', 'bootable': 'false', 'uuid': 'ABCD_1234',
                       'name': disk1_name, 'size': '53G'}]}

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

    def _create_dataset_secondary_disk(self):

        disk2_name = 'secondary'

        storage_data = \
            {'VGs': [{
                'id': 'vg2',
                'name': 'app_vg',
                'FSs': [{'id': 'fs1',  'type': 'xfs', 'mp': '/opt', 'size': '10G', 'snap_size': '20'},
                        {'id': 'fs2',  'type': 'xfs', 'mp': '/var', 'size': '20G', 'snap_size': '40'}],
                'PDs': [{'id': 'pd1', 'device': disk2_name}]}],
         'disks': [{'id': 'disk2', 'bootable': 'false', 'uuid': 'ABCD_1235',
                    'name': disk2_name, 'size': '40G'}]}

        self._create_storage_profile_items(self.sp1, self.system1, storage_data)

    def _setup_mocks(self, restore=None, snap_size='100', snap_external='false'):
        context = MagicMock()
        context.is_snapshot_action_forced = lambda: False
        context.snapshot_model = lambda: context

        cluster = VolMgrMockCluster(item_id="test_c1", cluster_type="sfha")

        snapshot = MagicMock()
        snapshot.get_vpath = lambda: '/x/y/z'

        fs1 = VolMgrMockFS(item_id='fs1', size='10G',
                           snap_size=snap_size, mount_point='/fs1',
                           snap_external=snap_external)
        fs2 = VolMgrMockFS(item_id='fs2', size='1G',
                           snap_size=snap_size, mount_point='/fs2',
                           snap_external=snap_external)
        fs3 = VolMgrMockFS(item_id='fs3', size='1G',
                           snap_size=snap_size, mount_point='/fs3',
                           snap_external=snap_external)
        fs4 = VolMgrMockFS(item_id="fs4", size="1G",
                           snap_size=snap_size, mount_point='/fs4',
                           snap_external=snap_external)

        def _mock_context_query(args, **kwargs):
            if args == 'file-system':
                return [fs1]
            elif args == 'vcs-cluster':
                return [cluster]
            elif args == 'snapshot-base':
                return [snapshot]
            else:
                return []

        context.query = _mock_context_query #MagicMock(side_effect = [[snapshot], [cluster, ]])

        context.snapshot_name = MagicMock(return_value='snapshot')

        storage_profile = VolMgrMockStorageProfile(item_id="st1",
                                                   volume_driver="vxvm")

        vg1 = VolMgrMockVG(item_id='test_dg1', volume_group_name='test_dg1')
        vg1.file_systems = [fs1, fs2]

        vg2 = VolMgrMockVG(item_id='test_dg2', volume_group_name='test_dg2')
        vg2.file_systems = [fs3, fs4]

        storage_profile.volume_groups = [vg1, vg2]

        cluster.storage_profile.append(storage_profile)
        node1 = MockNode("node1", "node1")

        items = [fs1, fs2, fs3, fs4, vg1, vg2, storage_profile, node1]

        TestVolMgrPlugin._set_state_applied(items)
#       TestVolMgrPlugin._set_state_initial([cluster])
        TestVolMgrPlugin._set_state_applied([cluster])

        for fs in (fs1, fs2, fs3, fs4):
            fs.get_node = MagicMock(return_value=node1)

        for vg, fs_list in ((vg1, (fs1, fs2)), (vg2, (fs3, fs4))):
            for fs in fs_list:
                fs.parent = MagicMock()
                fs.parent.parent = vg

        def mock_cluster_query(arg, *args, **kwargs):
            if arg == 'node':
                return [node1]
            elif arg == 'file-system':
                return [fs1]
            elif arg == 'storage-profile':
                return [storage_profile]
            elif arg == 'vcs-cluster':
                return [cluster]
            else:
                return []

        cluster.query = mock_cluster_query

        def _mock_node_query(*args, **kwargs):
            if arg == 'file-system':
                return [fs1, fs2, fs3, fs4]
            else:
                return []

        node1.query = _mock_node_query

        return context, cluster, storage_profile, node1

    def test_set_applied_not_called_on_query_item(self):
        # LITPCDS-12729
        model = ModelManager()
        model.register_property_type(
            PropertyType('any', regex=r"^.*$"))
        model.register_item_type(ItemType("root",
            snapshots=Collection("snapshot-base", max_count=1000),
        ))
        model.register_item_type(ItemType(
            "snapshot-base",
            reboot_issued_at=Property('any',
                required=False,
                updatable_rest=False,
                updatable_plugin=True),
            rebooted_clusters=Property('any',
                required=False,
                updatable_rest=False,
                updatable_plugin=True),
        ))
        model.create_root_item("root")
        snap = model.create_item("snapshot-base", "/snapshots/snapshot",
                reboot_issued_at='10.12', rebooted_clusters='c1')

        manager = ExecutionManager(model, Mock(), Mock())
        cb_api = CallbackApi(manager)

        # LITPCDS-12729: 'set_applied()' not called on QueryItem which would fail
        # as it is not exposed through the API and doesn't exist in that class
        self.assertEqual(snap.get_state(), 'Initial')
        VolMgrPlugin._set_reboot_time(cb_api)
        self.assertEqual(snap.get_state(), 'Applied')

    def test_format_list(self):
        l1 = []
        l2 = ['x']
        l3 = ['x', 'y']
        l4 = ['x', 'y', 'z']
        l5 = ['x', 'y', 'z', 'w']
        self.assertEquals('', VolMgrUtils.format_list(l1))
        self.assertEquals('x', VolMgrUtils.format_list(l2))
        self.assertEquals('x and y', VolMgrUtils.format_list(l3))
        self.assertEquals('x, y and z', VolMgrUtils.format_list(l4))
        self.assertEquals('x, y, z and w', VolMgrUtils.format_list(l5))

    def test_megabyte_conversion(self):
        self.assertEquals(1024, VolMgrUtils.get_size_megabytes('1G'))
        self.assertEquals(1, VolMgrUtils.get_size_megabytes('1M'))
        self.assertEquals(1024 * 1024, VolMgrUtils.get_size_megabytes('1024G'))
        self.assertEquals(1024 * 1024, VolMgrUtils.get_size_megabytes('1T'))
        self.assertEquals(0, VolMgrUtils.get_size_megabytes('1F'))
        self.assertEquals(0, VolMgrUtils.get_size_megabytes('1'))
        self.assertEquals(0, VolMgrUtils.get_size_megabytes('G'))

    def test_validate_model_primary_disk(self):
        self.setup_model()
        self._create_dataset_primary_disk()
        errors = self.plugin.validate_model(self.context)
        self.assertEqual([], errors)

    def test_validate_model_disk_too_small_for_2_fsystems(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           mount_point='/')
        fs2 = VolMgrMockFS(item_id='fs2',
                           size='10G',
                           mount_point='/opt')
        fs3 = VolMgrMockFS(item_id='fs3',
                           size='20G',
                           mount_point='/var')
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='20G',
                              uuid='ABCD_1234')
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="app_vg")

        vg.file_systems.append(fs1)
        vg.file_systems.append(fs2)
        vg.file_systems.append(fs3)
        vg.physical_devices.append(pd1)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg)

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk)

        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp
        cluster = VolMgrMockCluster(item_id="c1")
        cluster.nodes.append(node)

        def _mock_cluster_query(args, **kwargs):
            if args == 'storage-profile':
                if "volume_driver" in kwargs \
                  and kwargs["volume_driver"] is "vxvm":
                    return []
                else:
                    return [sp]
            else:
                return []
        cluster.query = _mock_cluster_query
        all_items = [fs1, fs2, fs3, pd1, disk, vg, sp, cluster, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected =  "The System Disks (20480 MB) on node 'mn1' are not " \
                    "large enough for volume group requirement (82520 MB). " \
                    "Volume group requirement = ((file systems including " \
                    "snapshots) 81920 MB) + (LVM metadata 600 MB.)"
        self.assertEqual(expected, errors[0].error_message)

    def test_validate_model_no_space_for_sundries(self):

        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           mount_point='/')
        fs2 = VolMgrMockFS(item_id='fs2',
                           size='20G',
                           mount_point='/home')

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="app_vg")

        vg.physical_devices.append(pd1)
        vg.file_systems.append(fs1)
        vg.file_systems.append(fs2)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg)

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk)

        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [fs1, fs2, pd1, disk, vg, sp, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        self.assertEqual(1, len(errors))

        expected = "The System Disks (33792 MB) on node 'mn1' are " \
                   "not large enough for volume group requirement (62040 MB). " \
                   "Volume group requirement = ((file systems including "\
                   "snapshots) 61440 MB) + (LVM metadata 600 MB.)"
        self.assertEqual(expected, errors[0].error_message)

    def test_validate_model_no_bootable_disk(self):

        fs1 = VolMgrMockFS('fs1', size='30G', mount_point='/')
        pd1 = VolMgrMockPD('pd1', device_name='sda')
        vg1 = VolMgrMockVG('vg1', volume_group_name='app_vg')

        sp1 = VolMgrMockStorageProfile('sp1', volume_driver='lvm')

        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)
        sp1.volume_groups.append(vg1)

        system1 = VolMgrMockSystem('s1')

        node1 = VolMgrMockNode("n1", hostname="mn1")
        node1.system = system1
        node1.storage_profile = sp1

        all_items = [fs1, pd1, vg1, sp1, node1, system1]

        VolMgrMock._set_state_initial(all_items)

        context = VolMgrMockContext()

        context.query = VolMgrMock.mock_query({
            'node': [node1],
            'storage-profile': [sp1],
            'file-system': [fs1],
            'volume-group': [vg1],
            'system': [system1]
        })

        errors = self.plugin.validate_model(context)

        self.assertEqual(2, len(errors), errors)

        expected = "Exactly one System Disk should have 'bootable' " \
                   "Property set to 'true'"
        self.assertEqual(expected, errors[0].error_message)

        expected = "Failed to find System disk 'sda' for Node 'mn1'"
        self.assertEqual(expected, errors[1].error_message)

    def test_validate_model_duplicate_vg_name(self):
        vg1 = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_root")
        vg2 = VolMgrMockVG(item_id="vg2",
                          volume_group_name="vg_root")
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg1)
        sp.volume_groups.append(vg2)

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [disk1, vg1, vg2, sp, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        self.assertEqual(2, len(errors))

        expected = 'Property "volume_group_name" is not unique for '\
                   'this storage-profile.'
        self.assertEqual(expected, errors[0].error_message)
        self.assertEqual(expected,  errors[1].error_message)

    def test_validate_model_duplicate_mount_point(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           mount_point='/')
        fs2 = VolMgrMockFS(item_id='fs2',
                           size='20G',
                           mount_point='/home')
        fs3 = VolMgrMockFS(item_id='fs3',
                           size='20G',
                           mount_point='/home')

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')

        vg1 = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_root")
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        vg1.file_systems.extend([fs1, fs2, fs3])
        sp.volume_groups.append(vg1)

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [fs1, fs2, fs3, pd1, disk1, vg1, sp, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        self.assertEqual(2, len(errors))

        expected = 'File System mount_point is not unique for this ' \
                   'Storage profile'
        self.assertTrue(all(err.error_message == expected
                            for err in errors))

    def test_validate_model_one_pd_per_disk(self):

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')

        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='disk1')

        vg1 = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_root")
        vg2 = VolMgrMockVG(item_id="vg2",
                          volume_group_name="app_vg")

        vg1.physical_devices.extend([pd1, pd2])
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg1)
        sp.volume_groups.append(vg2)

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [pd1, pd2, disk1, vg1, vg2, sp, disk1, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected = "Disk 'disk1' referenced by multiple Physical Devices"
        self.assertEqual(expected, errors[0].error_message)

    def test__validate_node_vg_uniform_disk_types(self):
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')

        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='disk2')

        vg1 = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_root")

        vg1.physical_devices.extend([pd1, pd2])
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg1)
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockOtherDisk(item_id='d2',
                              bootable='false',
                              name='disk2',
                              size='33G',
                              uuid='EFGH_5678')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)
        sys.disks.append(disk2)
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [disk1, disk2, pd1, pd2, vg1, sp, node]
        VolMgrMock._set_state_initial(all_items)

        context = VolMgrMockContext()
        context.query = VolMgrMock.mock_query({
            'node': [node], 'storage-profile': [sp]
            })

        errors = self.plugin.validate_model(context)

        msg = 'Disk is of type "{0}". All disks associated with volume group "vg_root" on node "mn1" must be of identical item type'
        exp1 = ValidationError(item_path=disk1.get_vpath(),
                                   error_message=msg.format(disk1.item_type_id))
        exp2 = ValidationError(item_path=disk2.get_vpath(),
                                   error_message=msg.format(disk2.item_type_id))
        expected = [exp1, exp2]
        self.assertEqual(expected, errors)

    def test__validate_cluster_vg_uniform_disk_types(self):

        # Test for cluster storage profile
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')

        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='disk2')

        vg1 = VolMgrMockVG(item_id="vg1",
                          volume_group_name="clus_vg")

        vg1.physical_devices.append(pd1)
        vg1.physical_devices.append(pd2)

        sp2 = VolMgrMockStorageProfile(item_id="sp2",
                                       volume_driver="vxvm")

        sp2.volume_groups.append(vg1)
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockOtherDisk(item_id='d2',
                              bootable='false',
                              name='disk2',
                              size='33G',
                              uuid='EFGH_5678')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)
        sys.disks.append(disk2)
        node.system = sys
        clus = VolMgrMockCluster(item_id="c1",
                                 cluster_type='sfha')

        clus.storage_profile.append(sp2)
        clus.nodes = [node]
        clus.query = VolMgrMock.mock_query({
                'storage-profile': [sp2]
                })

        all_items = [clus, disk1, disk2, pd1, pd2, vg1, sp2, node]
        VolMgrMock._set_state_initial(all_items)

        context = VolMgrMockContext()
        context.query = VolMgrMock.mock_query({
                'storage-profile': [sp2],
                'vcs-cluster' : [clus]
                })
        errors = self.plugin.validate_model(context)

        msg = 'Disk is of type "{0}". All disks associated with volume group "clus_vg" on node "mn1" must be of identical item type'
        exp1 = ValidationError(item_path=disk1.get_vpath(),
                                   error_message=msg.format(disk1.item_type_id))
        exp2 = ValidationError(item_path=disk2.get_vpath(),
                                   error_message=msg.format(disk2.item_type_id))
        self.assertEqual(exp1, errors[0])
        expected = [exp1, exp2]
        self.assertEqual(expected, errors)


    def test_validate_model_fs_size_multiple_of_lvm_extent(self):

        fs1 = VolMgrMockFS(item_id='fs1',
                           size='11M',
                           mount_point='/')
        fs1.type='ext4'
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')

        vg1 = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_root")

        vg1.physical_devices.append(pd1)
        vg1.file_systems.append(fs1)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg1)

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [fs1, pd1, disk1, vg1, sp, disk1, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        # Error: FS size is not multiple of Extent
        expected = "File System size '{0}' on '{1}' is not an exact " \
                        "multiple of the LVM Logical Extent size ('4MB')"
        expected = expected.format(fs1.size, node.hostname)
        errors = self.plugin.validate_model(context)
        self.assertEqual(1, len(errors))
        self.assertEqual(expected, errors[0].error_message)

    def test_validate_model_root_vg_disk_not_bootable(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='4G',
                           mount_point='/')
        fs1.type='ext4'
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        vg1 = VolMgrMockVG(item_id="vg1",
                           volume_group_name="vg_root")

        vg1.physical_devices.append(pd1)
        vg1.file_systems.append(fs1)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        # Strange method called in validate_root_vg_boot_disk
        sp.view_root_vg = vg1.volume_group_name
        sp.volume_groups.append(vg1)

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='true',
                              name='disk2',
                              size='33G',
                              uuid='ABCD_1235')

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1, disk2])
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        all_items = [fs1, pd1, disk1, disk2, vg1, sp, disk1, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif 'storage-profile' == args:
                return [sp]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected = "For node '{0}' the vg-root must be created on disks " \
                   "that include one bootable disk"
        expected = expected.format(node.hostname)
        self.assertEqual(1, len(errors))
        self.assertEqual(expected, errors[0].error_message)

    def test_validate_model_not_root_mount_point(self):
        # Unable to identify how to mock sp.view_root_vg appropriately for this
        # test.
        self.setup_model()
        disk_name = 'primary'
        storage_data = \
        {'VGs': [{'id': 'vg1',
                  'name': 'root_vg',
                  # Error: no slash file-system for root-vg
                  'FSs': [{'id': 'fs1', 'type': 'ext4',
                           'mp': '/something', 'size': '4G'}],
                  'PDs': [{'id': 'pd1', 'device': disk_name}]
                 }
                ],
         'disks': [{'id': 'disk1', 'bootable': 'true', 'uuid': 'ABCD_1234',
                    'name': disk_name, 'size': '6G'}
                  ]
        }
        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)
        errors = self.plugin.validate_model(self.context)
        self.assertEqual(1, len(errors))

    def test_validate_model_duplicate_disk_name(self):
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1235')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1, disk2])
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        all_items = [disk1, disk2, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected = "System Disk name '{0}' is not unique"
        expected = expected.format(disk1.name)
        self.assertEqual(2, len(errors))
        self.assertEqual(expected, errors[0].error_message)

    def test_validate_model_duplicate_disk_uuid(self):

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='true',
                              name='disk2',
                              size='33G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1, disk2])
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        all_items = [disk1, disk2, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            else:
                return []
        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected = "System Disk uuid '{0}' is not unique"
        expected = expected.format(disk1.uuid)
        self.assertEqual(2, len(errors))
        self.assertTrue(all([err.error_message == expected
                         for err in errors]))

    def test_validate_model_cloud_disk_uuid(self):

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='disk2')
        vg1 = VolMgrMockVG(item_id="vg1",
                           volume_group_name="vg_root")
        vg2 = VolMgrMockVG(item_id="vg2",
                           volume_group_name="app_vg")
        vg1.physical_devices.append(pd1)
        vg2.physical_devices.append(pd2)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='true',
                              name='disk2',
                              size='33G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1, disk2])
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        all_items = [pd1, pd2, vg1, vg2, sp, disk1, disk2, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            else:
                return []
        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected = "System Disk uuid '{0}' is not unique"
        expected = expected.format(disk1.uuid)
        self.assertTrue(all([err.error_message == expected
                             for err in errors]))

    def _create_vxvm_profile(self, driver, fs_type, fs_names=None):
        sp1_name = 'storage_profile_vxvm'
        vxvm_profile = self.model_manager.create_item('storage-profile',
                                             '/infrastructure/storage/storage_profiles/sp2',
                                             volume_driver=driver)
        disk2_name = "primary"

        if not fs_names:
            fs_names = ['fs1', 'fs2']
        storage_data = \
            {'VGs': [{
                'id': 'vg2',
                'name': 'vg2',
                'FSs': [{'id': fs_names[0],  'type': fs_type, 'size': '10G', 'snap_size': '20', 'mp':'/opt'},
                        {'id': fs_names[1],  'type': fs_type, 'size': '10G', 'snap_size': '20', 'mp':'/opt2'}],
                'PDs': [{'id': 'pd1', 'device': disk2_name}]}],
         'disks': [{'id': 'disk2', 'bootable': 'false', 'uuid': 'ABCD_1235',
                    'name': disk2_name, 'size': '40G'}]}

        if fs_type == 'ext4':
            storage_data['VGs'][0]['FSs'][0]['mp'] = '/opt'
            storage_data['VGs'][0]['FSs'][1]['mp'] = '/opt'
        self._create_storage_profile_items(vxvm_profile,
                                           None,
                                           storage_data)

    def test_validate_model_snapshot_name_tag(self):

        pd1 = VolMgrMockPD(item_id='pd1', device_name='disk1')
        vg1 = VolMgrMockVG(item_id="vg1", volume_group_name="vg_root")

        vg1.physical_devices.append(pd1)
        sp = VolMgrMockStorageProfile(item_id="sp1", volume_driver="lvm")

        sp.volume_groups.append(vg1)

        disk1 = VolMgrMockDisk(
            item_id='d1', bootable='false',
            name='disk1', size='33G', uuid='ABCD_1234')

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk1)

        node = VolMgrMockNode(item_id="n1", hostname="mn1")
        node.system = sys

        nodes = [node]

        all_items = [pd1, vg1, sp, disk1, node]

        VolMgrMock._set_state_initial(all_items)

        inf1 = MagicMock()
        inf1.storage.storage_profiles = [sp]
        infrastructure = [inf1]

        # test error msg with lvm profile
        context = VolMgrMockContext()
        context.query = VolMgrMock.mock_query({
            'node': nodes, 'infrastructure': infrastructure
        })
        context.snapshot_action = MagicMock(return_value='create')
        context.snapshot_name = MagicMock(return_value='x'*200)

        errors = self.plugin.validate_model_snapshot(context)

        # no LVM FSs are modeled, this leave 122 - 3 = 119 available chars
        # but we also consider KS FS files (home, root, var, etc.)
        # 119 - (longest_ks_fs_name) = 104

        max_chars = (MAX_LVM_SNAP_VOL_LENGTH - LITP_EXTRA_CHARS -
                     VolMgrUtils.get_ks_fs_max_length())

        expected = (
            'Snapshot name tag cannot exceed %s characters which is the '
            'maximum available length for an ext4 or xfs file system.'
        ) % (max_chars)

        self.assertEqual(expected, errors[0].error_message)

        context.snapshot_name = MagicMock(
            return_value='x'*(max_chars + 1))
        errors = self.plugin.validate_model_snapshot(context)

        self.assertEqual(expected, errors[0].error_message)

        context.snapshot_name = MagicMock(return_value='x' * max_chars)
        errors = self.plugin.validate_model_snapshot(context)

        self.assertEqual(0, len(errors))

        #test error msg with vxvm profle, and 1 fs
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           snap_size='50',
                           mount_point='/jump')
        fs1.type="vxfs"
        sp.volume_driver="vxvm"
        vg1.file_systems.append(fs1)

        max_chars = MAX_VXVM_VOL_LENGTH -LITP_EXTRA_CHARS - len(fs1.item_id)

        all_items = [fs1, pd1, vg1, sp, disk1, node]
        VolMgrMock._set_state_initial(all_items)

        context.snapshot_action = MagicMock(return_value='create')
        context.snapshot_name = MagicMock(return_value='x' * (max_chars + 1))

        errors = self.plugin.validate_model_snapshot(context)

        expected = (
            'Snapshot name tag cannot exceed %s characters which is the '
            'maximum available length for a VxFS file system.'
        ) % (max_chars)

        self.assertEqual(expected, errors[0].error_message)

        # test with 2 profiles, with 1 fs each
        sp2 = VolMgrMockStorageProfile(item_id="sp2", volume_driver="lvm")
        vg2 = VolMgrMockVG(item_id="vg2", volume_group_name="vg_root")
        sp2.volume_groups.append(vg2)

        fs2 = VolMgrMockFS(
            item_id='fs2', size='10G', snap_size='50', mount_point='/jump')

        vg2.file_systems.append(fs2)

        node.storage_profile = sp2

        all_items = [fs1,fs2, pd1, vg1, vg2, sp, sp2, disk1, node]
        VolMgrMock._set_state_initial(all_items)

        context.snapshot_action = MagicMock(return_value='create')
        context.snapshot_name = MagicMock(return_value='x' * (max_chars + 1))

        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(expected, errors[0].error_message)

        # Validation will pass because action is remove
        context.snapshot_action = MagicMock(return_value='remove')
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEquals([], errors)

        # Validation will pass because action will be set to unknown
        context.snapshot_action = MagicMock(return_value='')
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEquals([], errors)

    def get_snap_size(self, task):
        task = str(task)
        try:
            return [i for i in task.split("'") if "G" in i][0]
        except IndexError:
            pass

    def _generate_test_model_for_lvm_create_snapshot(self):

        fs1 = VolMgrMockFS(
            item_id='fs1', size='10G', mount_point='/', snap_size='20')
        fs2 = VolMgrMockFS(
            item_id='fs2', size='14G', mount_point='/home', snap_size='50')
        fs3 = VolMgrMockFS(
            item_id='fs3', size='2G', mount_point='swap', snap_size='50')
        fs4 = VolMgrMockFS(
            item_id='fs4', size='10G', mount_point='/opt', snap_size='20')
        fs5 = VolMgrMockFS(
            item_id='fs5', size='20G', mount_point='/var',snap_size='40')

        file_systems = [fs1, fs2, fs3, fs4, fs5]

        pd1 = VolMgrMockPD(item_id='pd1', device_name='disk1')
        pd2 = VolMgrMockPD(item_id='pd2', device_name='disk2')

        physical_devices = [pd1, pd2]

        vg1 = VolMgrMockVG(item_id="vg1", volume_group_name="vg_root")
        vg1.physical_devices.append(pd1)
        vg1.file_systems.extend([fs1, fs2, fs3])

        vg2 = VolMgrMockVG(item_id="vg2", volume_group_name="app_vg")
        vg2.physical_devices.append(pd2)
        vg2.file_systems.extend([fs4, fs5])

        volume_groups = [vg1, vg2]

        sp = VolMgrMockStorageProfile(item_id="sp1", volume_driver="lvm")
        sp.volume_groups.extend(volume_groups)

        storage_profiles = [sp]

        disk1 = VolMgrMockDisk(
            item_id='d1', bootable='true',
            name='disk1', size='53G', uuid='ABCD_1234')

        disk2 = VolMgrMockDisk(
            item_id='d2', bootable='false',
            name='disk2', size='40G', uuid='ABCD_1235')

        disks = [disk1, disk2]

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend(disks)

        sys.query = VolMgrMock.mock_query({
            'disk': disks, 'disk-base': disks
        })

        systems = [sys]

        mn1 = VolMgrMockNode(item_id="n1", hostname="mn1")
        mn1.system = sys
        mn1.storage_profile = sp

        nodes = [mn1]

        model_items = (
            nodes + systems +
            storage_profiles + volume_groups + file_systems +
            physical_devices + disks
        )

        VolMgrMock._set_state_applied(model_items)

        inf1 = MagicMock()
        inf1.storage.storage_profiles = [sp]
        infrastructure = [inf1]

        context = VolMgrMockContext()
        context.snapshot_action = MagicMock(return_value='create')
        context.query = VolMgrMock.mock_query({
            'node' : nodes,
            'infrastructure' : infrastructure,
        })

        # give a default snapshot name
        context.snapshot_name = MagicMock(return_value='snapshot')

        context.snapshot_model = VolMgrMock.mock_query({
            'node' : nodes,
            'storage-profile': storage_profiles,
        })

        return context

    def _generate_test_model_for_vxvm_create_snapshot(self):

        fs1 = VolMgrMockFS(
            item_id='fs1', size='10G', mount_point='/foo', snap_size='100')
        fs2 = VolMgrMockFS(
            item_id='fs2', size='10G', mount_point='/bar', snap_size='50')
        fs3 = VolMgrMockFS(
            item_id='fs3', size='10G', mount_point='/funk', snap_size='0')

        file_systems = [fs1, fs2, fs3]

        for fs in file_systems:
            fs.type = 'vxfs'

        pd1 = VolMgrMockPD(item_id='pd1', device_name='disk1')
        pd2 = VolMgrMockPD(item_id='pd2', device_name='disk2')

        physical_devices = [pd1, pd2]

        vg1 = VolMgrMockVG(
            item_id="vg_shared",
            volume_group_name="vxvm_vg"
        )

        vg1.physical_devices.append(pd1)
        vg1.file_systems.extend(file_systems)

        volume_groups = [vg1]

        sp1 = VolMgrMockStorageProfile(item_id="sp1", volume_driver="vxvm")
        sp1.volume_groups.extend(volume_groups)

        storage_profiles = [sp1]

        disk1 = VolMgrMockDisk(
            item_id='d1', bootable='true',
            name='disk1', size='53G', uuid='ABCD_1234')

        disk2 = VolMgrMockDisk(
            item_id='d2', bootable='false',
            name='disk2', size='40G', uuid='ABCD_1235')

        disks = [disk1, disk2]

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend(disks)

        sys.query = VolMgrMock.mock_query({
            'disk': disks, 'disk-base': disks
        })

        systems = [sys]

        mn1 = VolMgrMockNode(item_id="n1", hostname="mn1")
        mn1.system = sys

        mn2 = VolMgrMockNode(item_id="n2", hostname="mn2")
        mn2.system = sys

        nodes = [mn1, mn2]

        c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        c1.storage_profile = [sp1]
        c1.nodes = nodes
        c1.query = VolMgrMock.mock_query({
            'storage-profile': storage_profiles,
            'node': nodes
        })

        clusters = [c1]

        model_items = (
            clusters + nodes + systems +
            storage_profiles + volume_groups + file_systems +
            physical_devices + disks
        )

        VolMgrMock._set_state_applied(model_items)

        inf1 = MagicMock()
        inf1.storage.storage_profiles = [sp1]
        infrastructure = [inf1]

        context = VolMgrMockContext()
        context.snapshot_action = MagicMock(return_value='create')
        context.query = VolMgrMock.mock_query({
            'node' : nodes,
            'infrastructure' : infrastructure,
            'storage-profile': storage_profiles,
            'vcs-cluster': clusters,
        })

        # give a default snapshot name
        context.snapshot_name = MagicMock(return_value='snapshot')

        context.snapshot_model = VolMgrMock.mock_query({
            'node' : nodes,
            'storage-profile': storage_profiles,
            'vcs-cluster': clusters,
        })

        return context

    @patch('volmgr_plugin.volmgr_plugin.CallbackTask', new=MockCallbackTask)
    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test_create_snapshot_task(self):

        context = self._generate_test_model_for_lvm_create_snapshot()

        # Not interested in MS tasks for this test
        self.plugin.lvm_driver._create_ms_non_modeled_ks_snapshot_tasks = \
            MagicMock(return_value=[])

        self.plugin.lvm_driver._snap_operation_allowed = \
            MagicMock(return_value=True)

        # Case 1: default, unnamed snapshot happy path
        tasks = self.plugin._get_snapshot_tasks(context, 'create')

        # 5 tasks generated for FSs, and 1 for save grub = 6 tasks
        self.assertEqual(6, len(tasks))

        # Case 2: named snapshot happy path
        context.snapshot_name = MagicMock(return_value='test_snap_name')
        tasks = self.plugin._get_snapshot_tasks(context, 'create')

        # 5 tasks for FSs only
        self.assertEqual(5, len(tasks))

        # has 3 file-systems, but one has snap_size=0
        context = self._generate_test_model_for_vxvm_create_snapshot()

        # Case 1: default, unnamed snapshot happy path
        tasks = self.plugin._get_snapshot_tasks(context, 'create')
        self.assertEqual(2, len(tasks))

        # Case 2: named snapshot happy path
        context.snapshot_name = MagicMock(return_value='test_snap_name')
        tasks = self.plugin._get_snapshot_tasks(context, 'create')
        self.assertEqual(2, len(tasks))

    def test_snappable_nodes(self):

        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sys = VolMgrMockSystem(item_id='s1')
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp
        VolMgrMock._set_state_applied([node, sys])

        # Node will be snappable
        snappable_nodes = self.plugin._snappable_nodes([node],'restore',
                                                       driver='lvm')
        self.assertEqual([node], snappable_nodes)

        # Node will be snappable
        VolMgrMock._set_state_applied([node, sys])
        snappable_nodes = self.plugin._snappable_nodes([node],'not_restore',
                                                       driver='lvm')
        self.assertEqual([node], snappable_nodes)

        # Node is for removal - will be snappable
        VolMgrMock._set_state_removed([sys])
        snappable_nodes = self.plugin._snappable_nodes([node],'not_restore',
                                                       driver='lvm')
        self.assertEqual([node], snappable_nodes)

    @nottest
    def test__create_snapshot_task(self):
        self.setup_model()
        self._create_vxvm_profile("vxvm", "ext4")
        node = MagicMock()
        node.hostname = 'localhost'
        task = self.plugin.vxvm_driver._create_snapshot_task(node, 'test_dg',
                                                             'test_vn', '2G')
        self.assertTrue(isinstance(task, CallbackTask))

    @nottest
    def test__create_snapshot_tasks(self):
        self.setup_model()
        self._create_vxvm_profile("vxvm", "ext4")
        context, cluster, storage_profile, node1 = self._setup_mocks()
        self.assertFalse(node1.is_ms())
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,['L_fs1_']),
                                                                       'test_dg2': (node1,[])})
        tasks = self.plugin.vxvm_driver._create_snapshot_tasks(context)
        self.assertEqual(4, len(tasks), tasks)
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))

    def test__remove_snapshot_tasks_vxvm(self):
        self.setup_model()
        self._create_vxvm_profile("vxvm", "ext4")
        context, cluster, storage_profile, node1 = self._setup_mocks()
        context.snapshot_action = lambda: 'remove'
        self.assertFalse(node1.is_ms())
        self.plugin.vxvm_driver._vg_metadata = MagicMock(
            return_value={
                'test_dg1': (
                    node1, {
                        'snaps': ['L_fs1_', 'L_fs2_'],
                        'co': ['LOfs1_', 'LOfs2_'],
                        'cv': ['LVfs1_', 'LVfs2_']
                    }
                ),
                'test_dg2': (
                    node1, {
                        'snaps': ['L_fs3_', 'L_fs4_'],
                        'co': ['LOfs3_', 'LOfs4_'],
                        'cv': ['LVfs3_', 'LVfs4_']
                    }
                )
            }
        )
        rpc_result_ok = {
            "node1": {
                "errors": "",
                "data": {"status": 0, "err": "", "out": "off"}
            }
        }
        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        cluster.is_initial = MagicMock(return_value=False)
        tasks = self.plugin.vxvm_driver._remove_snapshot_tasks(context)
        self.assertEqual(6, len(tasks))
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))
        self.plugin.vxvm_driver._vg_metadata = MagicMock(
            return_value={
                'test_dg1': (
                    node1, {
                        'snaps': [],
                        'co': ['LOfs1_', 'LOfs2_'],
                        'cv': ['LVfs1_', 'LVfs2_']
                    }
                ),
                'test_dg2': (
                    node1, {
                        'snaps': ['L_fs3_', 'L_fs4_'],
                        'co': ['LOfs3_', 'LOfs4_'],
                        'cv': ['LVfs3_', 'LVfs4_']
                    }
                )
            }
        )
        tasks = self.plugin.vxvm_driver._remove_snapshot_tasks(context)
        self.assertEqual(6, len(tasks))
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))

    def test__delete_snapshot_vxvm_init_cluster(self):
        self.setup_model()
        self._create_vxvm_profile("vxvm", "ext4")
        context, cluster, storage_profile, node1 = self._setup_mocks()
        context.snapshot_action = lambda: 'remove'
        self.assertFalse(node1.is_ms())
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']}),
                                                                       'test_dg2': (node1,{'snaps':['L_fs3_', 'L_fs4_'],'co':['LOfs3_', 'LOfs4_'], 'cv':['LVfs3_', 'LVfs4_']})})
        tasks = self.plugin.vxvm_driver._remove_snapshot_tasks(context)
        # check-restore-in-progress + check-active-nodes + OLD(restore-fs)x4
        self.assertEqual(6, len(tasks))

        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':[],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']}),
                                                                       'test_dg2': (node1,{'snaps':['L_fs3_', 'L_fs4_'],'co':['LOfs3_', 'LOfs4_'], 'cv':['LOfs3_', 'LVfs4_']})})
        context.cluster.is_initial=lambda: True
        tasks = self.plugin.vxvm_driver._remove_snapshot_tasks(context)
        self.assertEqual(6, len(tasks))
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))

    def test__delete_snapshot_tasks(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           snap_size='50',
                           mount_point='/jump')

        pd1 = VolMgrMockPD(item_id='pd1', device_name='hd0')

        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='app-vg')
        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)

        context = VolMgrMockContext()
        context.snapshot_action = lambda: 'remove'

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        sp1.volume_groups.append(vg1)

        sys1 = VolMgrMockSystem(item_id='s1')
        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name=pd1.device_name,
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        sys1.disks.append(disk1)

        def _mock_sys_query(arg, **kwargs):
            if arg == 'disk' or arg == 'disk-base':
                return [disk1]
            return []
        sys1.query = _mock_sys_query

        ms1 = VolMgrMockMS()
        node1 = VolMgrMockNode("n1", "mn1")
        node2 = VolMgrMockNode("n2", "mn2")

        for node in [ms1, node1, node2]:
            node.system = sys1
            node.storage_profile = sp1

        VolMgrMock._set_state_applied([fs1, pd1, vg1, sp1, sys1,
                                       disk1, ms1, node1, node2])

        snapshot = Mock()

        def _mock_query(arg, **kwargs):
            if arg in ['ms']:
                return [ms1]
            elif arg == 'node':
                return [node1, node2]
            elif arg == 'vcs-cluster':
                return []
            elif arg == 'storage-profile':
                return [sp1]
            elif arg == 'snapshot-base':
                return [snapshot]
            else:
                return []

        context.query = _mock_query

        def _mock_snapshot_model():
            return Mock(query=_mock_query)

        context.snapshot_model = _mock_snapshot_model

        context.snapshot_name = MagicMock(return_value='snapshot')

        context.is_snapshot_action_forced = lambda: False

        tasks = self.plugin._get_snapshot_tasks(context, 'remove')
        # 15 tasks =
        # /jump on 2 x MNs = 2
        # +
        # /jump + /, /var, /home, /var/www , /var/tmp, /var/lib/puppetdb, /var/opt/rh on MS = 8
        # +
        # 3 x Remove Grub backups
        # +
        # 1 x check-all-MNs-reachable
        # +
        # 1 x check_restore_in_progress_task
        # => 15
        self.assertEquals(15, len(tasks))

    def test__restore_snapshot_tasks(self):
        self.setup_model()
        self._create_vxvm_profile("vxvm", "ext4")
        context, cluster, storage_profile, node1 = self._setup_mocks(1)
        self.assertFalse(node1.is_ms())
        self.plugin.vxvm_driver._vg_metadata = MagicMock(
            return_value=
                {'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],
                 'co':['LOfs1_', 'LOfs2_'],
                 'cv':['LVfs1_', 'LVfs2_']}),
                }
            )
        self.plugin.vxvm_driver._check_snapshot_is_valid = MagicMock(return_value=True)
        context.snapshot_action = lambda: 'restore'

        tasks = self.plugin.vxvm_driver._restore_snapshot_tasks(context)
        # check-active-nodes, check-presence, check-validity
        # remove_snapshot on FS 1 - 4
        self.assertEqual(7, len(tasks))
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))

        expected = [
            'Check that all nodes are reachable and an active node exists for each VxVM volume group on cluster(s) "test_c1"',
            'Check VxVM snapshots are present on cluster(s) "test_c1"',
            'Check VxVM snapshots are valid on cluster(s) "test_c1"',
            'Restore VxVM deployment snapshot "L_fs1_" for cluster "test_c1", volume group "test_dg1"',
            'Restore VxVM deployment snapshot "L_fs2_" for cluster "test_c1", volume group "test_dg1"',
            'Restore VxVM deployment snapshot "L_fs3_" for cluster "test_c1", volume group "test_dg2"',
            'Restore VxVM deployment snapshot "L_fs4_" for cluster "test_c1", volume group "test_dg2"',
        ]

        for exp, t in zip(expected, tasks):
            self.assertEqual(exp, t.description)

        context, cluster, storage_profile, node1 = self._setup_mocks(2)
        context.snapshot_action = lambda: 'restore'
        tasks = self.plugin.vxvm_driver._restore_snapshot_tasks(context)
        self.assertEqual(7, len(tasks))

        # case 2: -f option specified

        context.is_snapshot_action_forced = lambda: True
        tasks = self.plugin.vxvm_driver._restore_snapshot_tasks(context)
        # check-active-nodes, check-validity
        # restore_snapshot on FS 1 - 4
        self.assertEqual(5, len(tasks))
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))

        expected = [
            'Check VxVM snapshots are valid on cluster(s) "test_c1"',
            'Restore VxVM deployment snapshot "L_fs1_" for cluster "test_c1", volume group "test_dg1"',
            'Restore VxVM deployment snapshot "L_fs2_" for cluster "test_c1", volume group "test_dg1"',
            'Restore VxVM deployment snapshot "L_fs3_" for cluster "test_c1", volume group "test_dg2"',
            'Restore VxVM deployment snapshot "L_fs4_" for cluster "test_c1", volume group "test_dg2"',
        ]

        for exp, t in zip(expected, tasks):
            self.assertEqual(exp, t.description)

        context, cluster, storage_profile, node1 = self._setup_mocks(2)
        context.snapshot_action = lambda: 'restore'
        context.is_snapshot_action_forced = lambda: True
        tasks = self.plugin.vxvm_driver._restore_snapshot_tasks(context)
        self.assertEqual(5, len(tasks))

    def test__generate_vx_name(self):
        vg = MockVG("volume_id", "volume_id")
        fs = MockFs("fs_id", "10G", "100")
        vx_name = self.plugin.vxvm_driver._generate_vx_name(vg, fs)
        self.assertEquals('volume_id_fs_id', vx_name)

    @nottest
    def test__gen_create_snapshot_tasks(self):
        context, cluster, storage_profile, node1 = self._setup_mocks()
        self.assertFalse(node1.is_ms())
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']})})
        try:
            result = self.plugin.vxvm_driver._gen_create_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertFalse(result == 'Cluster "test_cl" reply with message volume group "test_dg1" is not imported')
        self.assertEqual(len(result), 3)
        self.assertEqual(self.plugin.vxvm_driver._vg_metadata.call_count, 1)
        for task in result:
            self.assertTrue(isinstance(task, CallbackTask))
        self.plugin.vxvm_driver.get_snapshot_tag = MagicMock(return_value='test_named')
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']})})
        try:
            result = self.plugin.vxvm_driver._gen_create_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertEqual(len(result), 3)
        self.assertEqual(self.plugin.vxvm_driver._vg_metadata.call_count, 1)
        for task in result:
            self.assertTrue('test_named' in task.description)

    @nottest
    def test__gen_create_snapshot_tasks_snap_size_zero(self):
        context, cluster, storage_profile, node1 = self._setup_mocks(None,'0')
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'dg': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']})})
        try:
            result = self.plugin.vxvm_driver._gen_create_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertEqual(self.plugin.vxvm_driver._vg_metadata.call_count, 0)
        self.assertFalse(result == 'Cluster "test_cl" reply with message volume group "test_dg1" is not imported')
        self.assertEqual(len(result), 0)

    @nottest
    def test__gen_delete_snapshot_tasks(self):
        context, cluster, storage_profile, node1 = self._setup_mocks()
        self.plugin.vxvm_driver.get_snapshot_tag = MagicMock(return_value='')
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']}),
                                                                          })
        rpc_result_ok = {"node1": {
                            "errors": "",
                            "data": {
                              "status": 0,
                              "err": "",
                              "out": "off"
                            }}}
        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.assertFalse(node1.is_ms())
        try:
            result = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(
                context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)

        self.assertTrue(result == 'Disk group test_dg2 not available on node node1 even after reimporting. Please check your system')

        self.plugin.vxvm_driver.get_snapshot_tag = MagicMock(return_value='test_named')
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']})})
        try:
            result = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(
                context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertEqual(len(result), 0)

        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_test_named'],'co':['LOfs1_test_named'], 'cv':['LVfs1_test_named']})})
        try:
            result = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(
                context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertEqual(len(result), 1)
        self.assertTrue(any('L_fs1_test_named' in res.description
                            for res in result))

        self.plugin.vxvm_driver.get_snapshot_tag = MagicMock(return_value='')
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_test_named'],'co':['LOfs1_', 'LOfs2_test_named'], 'cv':['LVfs1_', 'LVfs2_test_named']})})
        try:
            result = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(
                context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertEqual(len(result), 1)
        self.assertTrue(any('L_fs1_' in res.description for res in result))
        rpc_result_bad = {"node1": {
                            "errors": "",
                            "data": {
                              "status": 0,
                              "err": "",
                              "out": "on"
                            }}}
        context.rpc_command = MagicMock(return_value=rpc_result_bad)
        try:
            result = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(
                context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        # TODO self.assertEqual(result, 'VxVM rollback sync not complete for filesystem "fs1", disk group "test_dg1"')

    # def test__gen_delete_snapshot_tasks_snap_size_zero(self):
    #     context, cluster, storage_profile, node1 = self._setup_mocks(None, '0')
    #     self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']}),
    #                                                                       })
    #     tasks = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(context,
    #                                                                cluster,
    #                                                                storage_profile)
    #     self.assertEqual([], tasks)

    # def test__gen_delete_snapshot_tasks_snap_external_true(self):
    #     context, cluster, storage_profile, node1 = self._setup_mocks(None, '100', 'true')
    #     self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_'],'co':['LOfs1_', 'LOfs2_'], 'cv':['LVfs1_', 'LVfs2_']}),
    #                                                                       })
    #     tasks = self.plugin.vxvm_driver._gen_remove_snapshot_tasks(context,
    #                                                                cluster,
    #                                                                storage_profile)
    #     self.assertEqual([], tasks)

    @nottest
    def test__gen_restore_snapshot_tasks(self):
        context, cluster, storage_profile, node1 = self._setup_mocks()
        self.assertFalse(node1.is_ms())

        self.plugin.vxvm_driver.import_disk_group = lambda: True
        tasks = self.plugin.vxvm_driver._gen_restore_snapshot_tasks(context, cluster, storage_profile)

        # 2x restore_snapshot
        # 2x remove_snapshot

        self.assertEqual(2, len(tasks), tasks)
        for task in tasks:
            self.assertTrue(isinstance(task, CallbackTask))

        self.plugin.vxvm_driver.get_dg_hostname = MagicMock(\
            side_effect=DgNotImportedError('dg1 is not imported'))
        self.assertRaises(PluginError,
                          self.plugin.vxvm_driver._gen_restore_snapshot_tasks,
                            context,
                            cluster,
                            storage_profile)

        self.plugin.vxvm_driver.get_dg_hostname = MagicMock(side_effect =\
            PluginError('test_error'))
        try:
            result = self.plugin.vxvm_driver._gen_restore_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertTrue(result == 'test_error')
        self.plugin.vxvm_driver.get_dg_hostname = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_test'],'co':['LOfs1_', 'LOfs2_test'], 'cv':['LVfs1_', 'LVfs2_test']})})
        try:
            result = self.plugin.vxvm_driver._gen_restore_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertFalse(result == 'Cluster "test_cl" reply with message volume group "test_dg2" is not imported')
        self.assertEqual(1, len(result))
        self.plugin.vxvm_driver.get_dg_hostname = MagicMock(return_value={'test_dg1': (node1, {'snaps':[],'co':[],'cv':[]})})
        try:
            result = self.plugin.vxvm_driver._gen_restore_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)
        self.assertFalse(result == 'Cluster "test_cl" reply with message volume group "test_dg2" is not imported')
        self.assertEqual(0, len(result))

    def test_import_disk_group(self):
        context = MagicMock()
        node = MagicMock()
        node.hostname = 'node1'
        disk_group_name = 'test_dg'
        result = None
        rpc_result_ok = {"node1": {
                            "errors": "",
                            "data": {
                              "status": 0,
                              "err": "",
                              "out": "test_output"
                            }}}
        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        try:
            self.plugin.vxvm_driver.import_disk_group(context, node, disk_group_name)
        except CallbackExecutionException as e:
            self.assertFalse(1)
        self.assertTrue(1)

    def test_vg_metadata(self):
        context = MagicMock()
        node = MockNode("node1", "node1")
        disk_group_name = 'test_dg'
        result = None
        rpc_result_ok = {"node1": {
                            "errors": "",
                            "data": {
                              "status": 0,
                              "err": "",
                              "out": '{"test_dg": ["test_snap"]}'
                            }}}
        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        try:
            result = self.plugin.vxvm_driver._vg_metadata(context, [node], [disk_group_name], False)
        except PluginError as e:
            result = str(e)
        self.assertEqual({'test_dg': (node, ['test_snap'])}, result)
        context.rpc_command = MagicMock(side_effect = RpcExecutionException)
        self.assertRaises(PluginError,
                          self.plugin.vxvm_driver._vg_metadata,
                          context, [node], disk_group_name, False)

        rpc_result_err = {"node1": {
                            "errors": "test_error",
                            "data": {
                              "status": 1,
                              "err": "error",
                              "out": "test_output"
                            }}}
        context.rpc_command = MagicMock(return_value=rpc_result_err)
        try:
            result = self.plugin.vxvm_driver._vg_metadata(context, [node], [disk_group_name], False)
        except PluginError as e:
            result = str(e)
        self.assertTrue(result == "test_error")

    def test_get_disk_from_node(self):
        cluster = VolMgrMockCluster(item_id="c1",
                                    cluster_type="sfha")
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='50G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        pd1 = VolMgrMockPD(item_id="pd1",
                           device_name="disk1")
        pd2 = VolMgrMockPD(item_id="pd2",
                           device_name="disk_not_present")
        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)
        node1 = VolMgrMockNode(item_id="n1",
                               hostname="mn1")
        node1.system = sys1
        cluster.nodes = [node1]
        disk = self.plugin.vxvm_driver._get_disk_from_cluster_node(cluster, pd1)
        self.assertEqual(disk.name, disk1.name)

        # Try with PD not referencing disk
        disk = self.plugin.vxvm_driver._get_disk_from_cluster_node(cluster, pd2)
        self.assertEqual(disk, None)

    def test__errors_in_agent(self):
        pom = IgnoredCodesAgentProcessor()
        node = 'node1'
        agent_result = {'data': {'status': 0, 'err': '0'}}
        err = pom._errors_in_agent(node, agent_result, [10, 2])
        self.assertTrue(err == '')
        agent_result = {'data': {'status': 1, 'err': '12'}}
        err = pom._errors_in_agent(node, agent_result, [10, 2])
        self.assertTrue('node1 failed with message: 12' == err)
        err = pom._errors_in_agent(node, agent_result, [1, 3])
        self.assertTrue('' == err)
        agent_result = {'data': {'status': 5, 'err': 'missing snap'}}
        err = pom._errors_in_agent(node, agent_result, [12, 5])
        self.assertEqual('', err)
        agent_result = {'data': {'status': 5, 'err': 'merge problem'}}
        err = pom._errors_in_agent(node, agent_result, [12, 404])
        self.assertEqual('node1 failed with message: merge problem', err)

    def test_validate_vxfs_fs_lvm_profile(self):
        self._create_vxvm_profile("lvm", "vxfs")

        self.context.snapshot_name = MagicMock(return_value='x'*10)
        self.context.snapshot_action = MagicMock(return_value='create')
        errors = self.plugin.validate_model_snapshot(self.context)
        self.assertEquals([], errors)

        self.context.snapshot_name = MagicMock(return_value='x'*119)
        self.context.snapshot_action = MagicMock(return_value='create')
        errors = self.plugin.validate_model_snapshot(self.context)
        self.assertEqual(1, len(errors))

    def test__validate_vxfs_fs_name(self):
        invalid_id = "x"*24
        fs = VolMgrMockFS(item_id=invalid_id,
                           size='10G',
                           mount_point='/')
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="app_vg")
        sp = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='vxvm')

        vg.file_systems.append(fs)
        sp.volume_groups.append(vg)

        errors = self.plugin._validate_vxfs_fs_name([sp])
        msg = 'Filesystem "{0}" exceeds 23 characters maximum length.'
        msg = msg.format(fs.item_id)
        expected = ValidationError(item_path=fs.get_vpath(),
                                   error_message=msg)
        self.assertTrue(expected in errors)

    def test__validate_lvm_fs_type(self):
        fs = VolMgrMockFS(item_id="fs1",
                           size='10G',
                           mount_point='/')
        fs.type = "vxfs"
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="app_vg")
        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')

        vg.file_systems.append(fs)
        sp.volume_groups.append(vg)

        errors = self.plugin._validate_lvm_fs_type([sp])
        msg = 'The "volume_driver" property of the storage-profile ' \
              'has a value "lvm"; the "type" property on all ' \
              'file-systems must have a value of "ext4", "xfs" or "swap".'
        expected = ValidationError(item_path=fs.get_vpath(),
                                   error_message=msg)
        self.assertTrue(expected in errors)

    def test_update_model_primary_disk_only(self):
        self.setup_model()
        self._create_dataset_primary_disk()
        self.plugin.update_model(self.context)
        tasks = self.plugin.create_configuration(self.context)
        self.assertEqual(3, len(tasks))
        self.assertFalse('mount_options' in self.sp1.children['volume_groups'].children['vg1'].children['file_systems'].children['fs1'].properties)
        self.assertFalse('mount_options' in self.sp1.children['volume_groups'].children['vg1'].children['file_systems'].children['fs2'].properties)
        self.assertEqual('defaults,x-systemd.device-timeout=300',
                         self.sp1.children['volume_groups'].children['vg1'].children['file_systems'].children['fs3'].properties['mount_options'])
        self.assertEqual('defaults',
                         self.sp1.children['volume_groups'].children['vg1'].children['file_systems'].children['fs4'].properties['mount_options'])


    def test_create_configuration_primary_disk_only(self):
        self.setup_model()
        self._create_dataset_primary_disk()
        tasks = self.plugin.create_configuration(self.context)
        self.assertEqual(3, len(tasks))

    def test_create_configuration_two_disks(self):
        self.setup_model()
        self._create_dataset_primary_disk()
        self._create_dataset_secondary_disk()
        tasks = self.plugin.create_configuration(self.context)
        self.assertEqual(6, len(tasks))

    def test_create_configuration_no_root_mount_point(self):
        self.setup_model()
        self._create_dataset_secondary_disk()
        tasks = self.plugin.create_configuration(self.context)
        self.assertEqual(3, len(tasks))

    def test_create_configuration_no_system_linked(self):
        # Do not link Node to a System
        self.setup_model(link_node_to_system=False)

        disk_name = 'primary'

        storage_data = \
        {'VGs': [{'id': 'vg1',
                  'name': 'root_vg',
                  'FSs': [{'id': 'fs1', 'type': 'ext4', 'mp': '/', 'size': '10G'}],
                  'PDs': [{'id': 'pd1', 'device': disk_name}]
                 }
                ],
         'disks': [{'id': 'disk1', 'bootable': 'true', 'uuid': 'ABCD_1234',
                    'name': disk_name, 'size': '12G'}
                  ]
        }

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

        tasks = self.plugin.create_configuration(self.context)
        # No linked System, so no Tasks expected
        self.assertEqual(0, len(tasks))

    def test_create_configuration_vcs_cluster(self):
        def mock_query(type, **properties):
            if (type == "vcs-cluster"):
                cluster = MagicMock()
                cluster.query.return_value = []
                return [cluster]
            else:
                return PluginApiContext(self.model_manager).query(type, **properties)

        def mock_gen_tasks_for_fencing_disks(cluster):
            return ["task1", "task2"]
        self.setup_model()
        self._create_dataset_primary_disk()
        self.context.query = mock_query
        self.plugin.vxvm_driver.gen_tasks_for_fencing_disks = \
                    mock_gen_tasks_for_fencing_disks
        tasks = self.plugin.create_configuration(self.context)
        self.assertEqual(5, len(tasks))

    def _create_profile(self, driver):
        return self.model_manager.create_item('storage-profile',
                                              '/infrastructure/storage/storage_profiles/sp1',
                                              volume_driver=driver)

    def _create_storage_lun_only(self):
        device_name = 'sda'
        storage_data = \
            {'VGs': [{
                    'id': 'my_vg',
                    'name': 'my_vg',
                    'FSs': [{'id': 'my_fs', 'type': 'ext4', 'size': '10G',
                             'snap_size': '20', 'mp': '/opt'}],
                    'PDs': [{'id': 'my_pd', 'device': device_name}]}],
             'disks': [{'id': 'my_disk', 'bootable': 'false', 'uuid': 'uuid1',
                        'name': device_name, 'size': '40G',
                        'item_type_id': 'lun-disk'}]}
        self._create_storage_profile_items(self.sp1, self.system1, storage_data)
        lv_mock_return_value = [{'attrs': 'owi-aos---',
                                 'lv_name': 'my_fs',
                                 'path': '/dev/my_vg/my_fs',
                                 'size': '10.00G',
                                 'vg_name': 'my_vg',
                                 'mount': '/opt'}]
        lsblock_mock_side_effect = lambda context, lv, hostname:{
            '/dev/my_vg/my_fs':('NAME="my_vg-my_fs" '
                                'FSTYPE="ext4" LABEL="" '
                                'UUID="uuid2" '
                                'MOUNTPOINT="/opt"')}[lv]
        return lv_mock_return_value, lsblock_mock_side_effect

    def test_create_snapshot_lun_storage_does_nothing(self):
        self.setup_model()
        lv_mock_return_value, lsblock_mock_side_effect = \
            self._create_storage_lun_only()
        self.model_manager.set_all_applied()

        original_driver_method = self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect
        self.context.snapshot_action = MagicMock(return_value='create')
        with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
            lv_mock.return_value = lv_mock_return_value
            with patch.object(self.plugin.lvm_driver, '_get_lsblock_data') as lsblock_mock:
                lsblock_mock.side_effect = lsblock_mock_side_effect
                with patch.object(self.plugin.lvm_driver, '_vg_snap_operation_allowed_in_san_aspect') as mocked_should:
                    mocked_should.side_effect = original_driver_method
                    tasks = self.plugin.create_snapshot_plan(self.context)
                    self.assertEqual(1, mocked_should.call_count)

        snapshot_create_tasks = [x for x in tasks
                                 if x.args[1:3] == ('snapshot', 'create')]
        self.assertEqual(0, len(snapshot_create_tasks))

        # named snapshots always taken
        self.context.snapshot_name = MagicMock(return_value='foo')
        with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
            lv_mock.return_value = lv_mock_return_value
            with patch.object(self.plugin.lvm_driver, '_get_lsblock_data') as lsblock_mock:
                lsblock_mock.side_effect = lsblock_mock_side_effect
                with patch.object(self.plugin.lvm_driver, '_vg_snap_operation_allowed_in_san_aspect') as mocked_should:
                    mocked_should.side_effect = original_driver_method
                    tasks = self.plugin.create_snapshot_plan(self.context)
                    self.assertEqual(1, mocked_should.call_count)

        snapshot_create_tasks = [x for x in tasks
                                 if x.args[1:3] == ('snapshot', 'create')]
        self.assertEqual(1, len(snapshot_create_tasks))


    def _create_storage_disk_and_lun_the_same_vg(self):
        storage_data = \
            {'VGs': [{
                    'id': 'my_vg',
                    'name': 'my_vg',
                    'FSs': [{'id': 'my_fs', 'type': 'xfs', 'size': '10G', 'snap_size': '20',
                             'mp': '/opt'}],
                    'PDs': [{'id': 'my_pd', 'device': 'sda'},
                            {'id': 'my_pd_lun', 'device': 'sdb'}]}],
             'disks': [{'id': 'my_disk', 'bootable': 'false', 'uuid': 'uuid1',
                        'name': 'sda', 'size': '40G'},
                       {'id': 'my_lun_disk', 'bootable': 'false', 'uuid': 'uuid2',
                        'name': 'sdb', 'size': '40G',
                        'item_type_id': 'lun-disk'}]}
        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)
        lv_mock_return_value = [{'attrs': 'owi-aos---',
                                 'lv_name': 'my_fs',
                                 'path': '/dev/my_vg/my_fs',
                                 'size': '10.00G',
                                 'vg_name': 'my_vg',
                                 'mount': '/opt'}]
        lsblock_mock_side_effect = lambda context, lv, hostname:{
            '/dev/my_vg/my_fs':('NAME="my_vg-my_fs" '
                                'FSTYPE="ext4" LABEL="" '
                                'UUID="uuid3" '
                                'MOUNTPOINT="/opt"')}[lv]
        return lv_mock_return_value, lsblock_mock_side_effect

    def test_create_storage_disk_and_lun_the_same_vg_does_nothing(self):
        self.setup_model()
        lv_mock_return_value, lsblock_mock_side_effect = \
            self._create_storage_disk_and_lun_the_same_vg()
        self.model_manager.set_all_applied()

        original_driver_method = self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect

        self.context.snapshot_action = MagicMock(return_value='create')
        with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
            lv_mock.return_value = lv_mock_return_value
            with patch.object(self.plugin.lvm_driver, '_get_lsblock_data') as lsblock_mock:
                lsblock_mock.side_effect = lsblock_mock_side_effect
                with patch.object(self.plugin.lvm_driver, '_vg_snap_operation_allowed_in_san_aspect') as mocked_should:
                    mocked_should.side_effect = original_driver_method
                    tasks = self.plugin.create_snapshot_plan(self.context)
                    self.assertEqual(1, mocked_should.call_count)

        snapshot_create_tasks = [x for x in tasks
                                 if x.args[1:3] == ('snapshot', 'create')]
        self.assertEqual(0, len(snapshot_create_tasks))
        # named snapshots always taken
        self.context.snapshot_name = MagicMock(return_value='foo')
        with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
            lv_mock.return_value = lv_mock_return_value
            with patch.object(self.plugin.lvm_driver, '_get_lsblock_data') as lsblock_mock:
                lsblock_mock.side_effect = lsblock_mock_side_effect
                with patch.object(self.plugin.lvm_driver, '_vg_snap_operation_allowed_in_san_aspect') as mocked_should:
                    mocked_should.side_effect = original_driver_method
                    tasks = self.plugin.create_snapshot_plan(self.context)
                    self.assertEqual(1, mocked_should.call_count)

        snapshot_create_tasks = [x for x in tasks
                                 if x.args[1:3] == ('snapshot', 'create')]
        self.assertEqual(1, len(snapshot_create_tasks))

    def _create_storage_disk_and_lun_separate_vg(self):
        storage_data = \
            {'VGs': [
                {
                        'id': 'my_lun_vg',
                        'name': 'my_lun_vg',
                        'FSs': [{'id': 'my_sanAAA', 'type': 'ext4', 'size': '10G',
                                 'snap_size': '20', 'mp': '/opt', 'snap_external': 'false'}],
                        'PDs': [{'id': 'my_lun_pd', 'device': 'sda'}]},
                {
                        'id': 'my_vg',
                        'name': 'my_vg',
                        'FSs': [{'id': 'my_fs', 'type': 'xfs', 'size': '10G',
                                 'snap_size': '20', 'mp': '/var', 'snap_external': 'false'}],
                        'PDs': [{'id': 'my_pd', 'device': 'sdb'}]}],
             'disks': [{'id': 'my_lun_disk', 'bootable': 'false', 'uuid': 'uuid1',
                       'name': 'sda', 'size': '40G', 'item_type_id': 'lun-disk'},
                      {'id': 'my_disk', 'bootable': 'false', 'uuid': 'uuid2',
                       'name': 'sdb', 'size': '40G'}]}
        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)
        lv_mock_return_value = [{'attrs': 'owi-aos---',
                                 'lv_name': 'my_fs',
                                 'path': '/dev/my_vg/my_fs',
                                 'size': '10.00G',
                                 'vg_name': 'my_vg',
                                 'mount': '/var'},
                                {'attrs': 'owi-aos---',
                                 'lv_name': 'my_san',
                                 'path': '/dev/my_lun_vg/my_san',
                                 'size': '10.00G',
                                 'vg_name': 'my_lun_vg',
                                 'mount': '/opt'}]
        lsblock_mock_side_effect = lambda context, lv, hostname:{
            '/dev/my_vg/my_fs':('NAME="my_vg-my_fs" '
                                'FSTYPE="xfs" LABEL="" '
                                'UUID="uuid3" '
                                'MOUNTPOINT="/var"'),
            '/dev/my_lun_vg/my_san':('NAME="my_lun_vg-my_san" '
                                'FSTYPE="ext4" LABEL="" '
                                'UUID="uuid4" '
                                'MOUNTPOINT="/opt"')}[lv]
        return lv_mock_return_value, lsblock_mock_side_effect

    def test_create_storage_disk_and_lun_separate_vg_does_only_disk(self):
        self.setup_model()
        lv_mock_return_value, lsblock_mock_side_effect = \
            self._create_storage_disk_and_lun_separate_vg()
        self.model_manager.set_all_applied()

        original_driver_method = self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect
        self.context.snapshot_action = MagicMock(return_value='create')
        with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
            lv_mock.return_value = lv_mock_return_value
            with patch.object(self.plugin.lvm_driver, '_get_lsblock_data') as lsblock_mock:
                lsblock_mock.side_effect = lsblock_mock_side_effect
                with patch.object(self.plugin.lvm_driver, '_vg_snap_operation_allowed_in_san_aspect') as mocked_should:
                    mocked_should.side_effect = original_driver_method
                    tasks = self.plugin.create_snapshot_plan(self.context)
                    # once per fs
                    self.assertEqual(2, mocked_should.call_count)
        snapshot_create_tasks = [x for x in tasks
                                 if x.args[1:3] == ('snapshot', 'create')]
        self.assertEqual(1, len(snapshot_create_tasks))
        # named snapshots always taken
        self.context.snapshot_name = MagicMock(return_value='foo')
        with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
            lv_mock.return_value = lv_mock_return_value
            with patch.object(self.plugin.lvm_driver, '_get_lsblock_data') as lsblock_mock:
                lsblock_mock.side_effect = lsblock_mock_side_effect
                with patch.object(self.plugin.lvm_driver, '_vg_snap_operation_allowed_in_san_aspect') as mocked_should:
                    mocked_should.side_effect = original_driver_method
                    tasks = self.plugin.create_snapshot_plan(self.context)
                    self.assertEqual(2, mocked_should.call_count)

        snapshot_create_tasks = [x for x in tasks
                                 if x.args[1:3] == ('snapshot', 'create')]
        self.assertEqual(2, len(snapshot_create_tasks))

    def test_create_snapshot_empty(self):
        def mock_ms_snap(a, b):
            return []
        self.setup_model()
        self._create_dataset_primary_disk()
        self.plugin.lvm_driver._create_ms_non_modeled_ks_snapshot_tasks = mock_ms_snap
        self.context.snapshot_action = MagicMock(return_value='create')
        tasks = self.plugin.create_snapshot_plan(self.context)
        self.assertEqual(0, len(tasks))

    def test_create_snapshot_plan(self):

        context = VolMgrMockContext()
        context.snapshot_action = lambda: 'create'
        context.is_snapshot_action_forced = lambda: True

        def mock_query(*arg, **kw):
            if arg == 'vcs-cluster':
                return []
            else:
                return []

        context.query = mock_query

        tasks = self.plugin.create_snapshot_plan(context)
        self.assertEqual([], tasks)

        # Test that exception is raised correctly
        def _raise_exception():
            raise Exception
        context.snapshot_action = _raise_exception
        self.assertRaises(PluginError,
                          self.plugin.create_snapshot_plan, context)

    def test__get_snapshot_tasks(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           mount_point='/',
                           snap_size='20')
        fs2 = VolMgrMockFS(item_id='fs2',
                           size='14G',
                           mount_point='/home',
                           snap_size='50')
        fs3 = VolMgrMockFS(item_id='fs3',
                           size='2G',
                           mount_point='/swap',
                           snap_size='50')
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        vg1 = VolMgrMockVG(item_id="vg1",
                           volume_group_name="vg_root")
        vg1.physical_devices.append(pd1)
        vg1.file_systems.extend([fs1, fs2, fs3])
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        fs4 = VolMgrMockFS(item_id='fs4',
                           size='10G',
                           mount_point='/opt',
                           snap_size='20')
        fs5 = VolMgrMockFS(item_id='fs5',
                           size='20G',
                           mount_point='/var',
                           snap_size='40')

        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='disk2')

        vg2 = VolMgrMockVG(item_id="vg2",
                           volume_group_name="app_vg")
        vg2.physical_devices.append(pd2)
        vg2.file_systems.extend([fs4, fs5])
        sp.volume_groups.append(vg1)
        sp.volume_groups.append(vg2)

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='53G',
                              uuid='ABCD_1234')

        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='false',
                              name='disk2',
                              size='40G',
                              uuid='ABCD_1235')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk2, disk2])

        def _mock_system_query(args, **kwargs):
            if 'disk' == args or 'disk-base' == args:
                return [disk1, disk2]
            return []
        sys.query = _mock_system_query

        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        node.storage_profile = sp

        for fs in [fs1, fs2, fs3, fs4, fs5]:
            fs.get_node = lambda: node

        all_items = [fs1, fs2, fs3, fs4, fs5, pd1, pd2,
                     vg1, vg2, sp, disk1, disk2, sys, node]

        VolMgrMock._set_state_applied(all_items)

        inf1 = MagicMock()
        inf1.storage.storage_profiles = [sp]
        infrastructure = [inf1]

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            elif args == 'infrastructure':
                return infrastructure
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query
        context.snapshot_action = lambda: 'create'
        context.snapshot_name = lambda: 'snapshot'

        def _mock_snap_query(arg, **kwargs):
            if arg == 'node':
                return [node]
            elif arg == 'vcs-cluster':
                return []
            elif arg == 'storage-profile':
                return [sp]
            else:
                return []

        def _mock_snapshot_model():
            return Mock(query=_mock_snap_query)

        context.snapshot_model = _mock_snapshot_model

        # Not interested in MS tasks for this test
        self.plugin.lvm_driver._create_ms_non_modeled_ks_snapshot_tasks = MagicMock(return_value=[])

        self.plugin.lvm_driver._snap_operation_allowed = MagicMock(return_value=True)

        backup_tasks = self.plugin._get_snapshot_tasks(context, 'create')
        self.assertEqual(6, len(backup_tasks))


        context.snapshot_model = lambda: None
        tasks = self.plugin._get_snapshot_tasks(context, 'remove')
        self.assertEqual([], tasks)

        self.assertRaises(PluginError, self.plugin._get_snapshot_tasks,
                          context, 'restore')

    def test__create_snapshot_cb(self):
        context = VolMgrMockContext()
        self.plugin.base_processor = MagicMock()
        rpc_return = ('', {'n1' : ['err1']})
        self.plugin.base_processor.execute_rpc_and_process_result = MagicMock(return_value=rpc_return)
        self.assertRaises(CallbackExecutionException,
                          self.plugin._create_snapshot_cb,
                          context, 'n1', 'agent', 'action',
                          timeout=10, retries=5)

    def test_generate_snapshot_name(self):
        self.setup_model()
        self.assertEqual("L_fs1_",
                VolMgrUtils.gen_snapshot_name("fs1"))
        self.assertEqual("L_fs2_",
                VolMgrUtils.gen_snapshot_name("fs2"))
        self.assertEqual("L_fs1_test1",
                VolMgrUtils.gen_snapshot_name("fs1", 'test1'))
        self.assertEqual("L_fs2_test2",
                VolMgrUtils.gen_snapshot_name("fs2", 'test2'))

    def test_generate_snapshot_size(self):

        fs1 = VolMgrMockFS("fs1", "100G", "/foo1", snap_size="10")
        fs2 = VolMgrMockFS("fs2", "100M", "/foo2", snap_size="33")
        fs3 = VolMgrMockFS("fs3",  "50T", "/foo3", snap_size="33")

        self.assertEqual("10240.0M", VolMgrUtils.compute_snapshot_size(fs1))
        self.assertEqual("33.0M", VolMgrUtils.compute_snapshot_size(fs2))
        self.assertEqual("17301504.0M", VolMgrUtils.compute_snapshot_size(fs3))

    @patch('litp.core.callback_api.CallbackApi')
    def test_rpc_output_treated(self, mock_cb_api):
        instance = mock_cb_api.return_value
        instance.rpc_command.return_value = {
            'node1': {
                'data': {
                    'status': 3,
                    'err': 'uh oh',
                    'out': ''
                },
                'errors':'bad exec'
            },
            'ms1': {
                'data': {
                    'status': 0,
                    'err': 'dont mind me',
                    'out': 'all grand'
                },
                'errors': ''
            }
        }

        self.assertRaises(CallbackExecutionException, self.plugin._base_rpc_task,
                          instance,
                          ['ms1', 'node1'],
                          'snapshot',
                          'create',
                          **{'path': 'hoho',
                           'size': '1G',
                           'name': 'snapshot'})
        # 0 exit code, no errors
        instance.rpc_command.return_value = {'node1': {'data': {'status': 0,
                                                           'err': 'just kidding',
                                                           'out': ''},
                                                       'errors':''},
                                                 'ms1': {'data': {'status': 0,
                                                         'err': 'dont mind me',
                                                         'out': 'all grand'},
                                                         'errors':''}}
        self.assertEqual(None, self.plugin._base_rpc_task(
                                                          instance,
                                                          ['ms1', 'node1'],
                                                          'snapshot',
                                                          'create',
                                                          **{'path': '/ho/ho/ho',
                                                           'size': '1G',
                                                           'name': 'snapshot'}))
        self.assertRaises(CallbackExecutionException,
            self.plugin._vx_init_rpc_task,instance,
                                          ['node1'],
                                          'vxvm',
                                          'clear_keys',
                                          **{'uuid': '1234'
                                           })

        disk = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='1234',
                              size='50G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        self.assertEqual(None,
            self.plugin._vx_init_rpc_task(instance,
                                          ['node1'],
                                          'vxvm',
                                          'setup_disk',
                                          **{'disk_name': disk.get_vpath()
                                           }))
        self.assertEqual(None,
            self.plugin._vx_init_rpc_task(instance,
                                          ['node1'],
                                          'vxvm',
                                          'setup_disk',
                                          **{'disk_name': disk.get_vpath()
                                           }))
        def mock_process(cb_api, nodes, agent, action, kwargs, timeout, retries):
            self.assertEqual(kwargs['disk_name'], 'wxvm_disk_54321_dev')
            return None,None
        def mock_base_task(cb_api, nodes, agent, action, **kwargs):
            self.assertEqual(kwargs['uuid'], '123456')
            return None,None
        def mock_mock_query(the_path):
            self.assertEqual(the_path,'/path/to/disk')
            disk= MagicMock()
            disk.uuid='123456'
            return disk

        with patch.object(self.plugin.base_processor,'execute_rpc_and_process_result',
                    MagicMock(side_effect=mock_process)):
            cb_api = MagicMock()
            disk= MagicMock()
            disk.uuid='54321'
            cb_api.query_by_vpath = MagicMock(return_value=disk)
            self.assertEqual(None,self.plugin._vx_init_rpc_task(cb_api,
                                              ['node1'],
                                              'vxvm',
                                              'setup_disk',
                                              **{'disk_name': disk
                                               }))
        with patch.object(self.plugin,'_base_rpc_task',MagicMock(side_effect=mock_base_task)):
            cb_api = MagicMock()

            cb_api.query_by_vpath = MagicMock(side_effect=mock_mock_query)
            self.assertEqual(None,self.plugin._task_with_future_uuid(cb_api,
                                              ['node1'],
                                              'vxvm',
                                              'setup_disk',
                                              **{'uuidfromview': '/path/to/disk'
                                               }))

            self.assertRaises(CallbackExecutionException,
                self.plugin._task_with_future_uuid,cb_api,
                                              ['node1'],
                                              'vxvm',
                                              'setup_disk',
                                              **{'disk_name': disk
                                               })

    def test_get_ms_snap_tasks(self):
        mock_context = MagicMock()
        mock_context.rpc_command.return_value = \
        {"node1": {'data': {'out': "/dev/vg_root/lv_test1 10g owi-aos-- /test1\n" +
                                   "/dev/vg_root/lv_test2 20g owi-aos-- /test2",
                            'status': 0,
                            'err': ''
                            },
                   'errors': ''
                   }
        }

        def mock_grub_backup_task(a):
            return "grub_task"

        def mock_ms_non_modeled_ks_fs(context, ms, fs):
            return True

        self.setup_model()
        self._create_dataset_primary_disk()
        self.plugin.lvm_driver._gen_grub_backup_task = mock_grub_backup_task
        self.plugin.lvm_driver._ms_non_modeled_ks_fs = mock_ms_non_modeled_ks_fs
        self.plugin.lvm_driver.get_snapshot_tag = MagicMock(return_value='')
        tasks = self.plugin.lvm_driver._create_ms_non_modeled_ks_snapshot_tasks(mock_context, self.node1)
        self.assertEqual(3, len(tasks))
        self.assertEqual(tasks[0].args[1], "snapshot")
        self.assertEqual(tasks[0].args[2], "create")
        self.assertEqual(tasks[0].kwargs['name'], "L_lv_test1_")
        self.assertEqual(tasks[0].kwargs['path'], "/dev/vg_root/lv_test1")
        self.assertEqual(tasks[0].kwargs['size'], "10G")
        self.assertEqual(tasks[1].kwargs['name'], "L_lv_test2_")
        self.assertEqual(tasks[1].kwargs['path'], "/dev/vg_root/lv_test2")
        self.assertEqual(tasks[1].kwargs['size'], "20G")

    def test_check_is_ext4(self):
        mock_context = MagicMock()
        mock_context.rpc_command.return_value = {"test_hostname": {'data': {'out': "FSTYPE=ext4",
                                                                            'status': 0,
                                                                            'err': ''},
                                                                   'errors': ''
                                                                   }
                                                 }
        self.setup_model()
        self._create_dataset_primary_disk()
        res = self.plugin.lvm_driver._check_is_lv_not_swap(mock_context, "lv_path", "test_hostname")
        self.assertTrue(res)
        mock_context.rpc_command.return_value = {"test_hostname": {'data': {'out': "FSTYPE=swap",
                                                                            'status': 0,
                                                                            'err': ''},
                                                                   'errors': ''
                                                                   }
                                                 }
        res = self.plugin.lvm_driver._check_is_lv_not_swap(mock_context, "lv_path", "test_hostname")
        self.assertFalse(res)

    def test_check_size(self):
        self.setup_model()
        self.assertFalse(self.plugin.lvm_driver._check_size("0G"))
        self.assertTrue(self.plugin.lvm_driver._check_size("10G"))
        self.assertTrue(self.plugin.lvm_driver._check_size("0.5M"))

    def test_check_not_snapshot(self):
        self.setup_model()
        self.assertFalse(self.plugin.lvm_driver._check_not_snapshot("swi-aos--"))
        self.assertTrue(self.plugin.lvm_driver._check_not_snapshot("owi-aos--"))

    def test_snapshot_tasks_needed(self):

        context = VolMgrMockContext()
        context.snapshot_action = lambda: 'remove'
        context.snapshot_name = lambda: 'snapshot'
        context.is_snapshot_action_forced = lambda: True

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        ms1 = VolMgrMockMS()
        ms1.storage_profile = sp1

        sys1 = VolMgrMockSystem(item_id='s1')
        ms1.system = sys1

        VolMgrMock._set_state_applied([sp1, sys1, ms1])

        snapshot_object = self.model_manager.create_item('snapshot-base',
                                             '/snapshots/bob',
                                             active='true')
        self.assertFalse(isinstance(snapshot_object, list), snapshot_object)

        context.query = VolMgrMock.mock_query({
            'ms': [ms1],
            'snapshot-base': [snapshot_object]
        })

        context.snapshot_model = lambda: context

        tasks = self.plugin.create_snapshot_plan(context)

        #   1 x _check_lvm_restores_in_progress_cb
        #   7 x _remove_snapshot_cb for KS {home, var, root, var_www}
        # + 1 x _remove_grub_cb
        # ---
        #   9 task total generated for MS

        self.assertEqual(9, len(tasks))

    def test_nodes_and_vgs_node_state(self):
        nodes = []
        for _ in range(5):
            node = MagicMock()
            node.system.is_initial.return_value = False
            node.system.is_applied.return_value = False
            node.system.is_updated.return_value = False
            node.system.is_for_removal.return_value = False
            node.is_initial.return_value = False
            node.is_applied.return_value = False
            node.is_updated.return_value = False
            node.is_for_removal.return_value = False
            node.system.storage_profile.volume_groups = ['vg']
            node.system.storage_profile.volume_driver = 'lvm'
            nodes.append(node)
        nodes[0].system.is_initial.return_value = True
        nodes[1].system.is_applied.return_value = True
        nodes[2].system.is_updated.return_value = True
        nodes[3].system.is_for_removal.return_value = True
        nodes[0].is_initial.return_value = True
        nodes[1].is_applied.return_value = True
        nodes[2].is_initial.return_value = True
        nodes[3].is_for_removal.return_value = True
        nodes[4].is_applied.return_value = True
        self.assertEquals([nodes[1],
                           nodes[2],
                           nodes[3],
                           nodes[4]],
                          list(self.plugin._snappable_nodes(nodes, 'create')))

        self.assertEquals([nodes[1],
                           nodes[3],
                           nodes[4]],
                          list(self.plugin._snappable_nodes(nodes, 'restore')))

    def test_get_tasks_hostnames(self):
        task1 = MagicMock("task1")
        task1.args = [["node1"]]
        task2 = MagicMock("task2")
        task2.args = [["node2"]]
        task3 = MagicMock("task3")
        task3.args = [["node1"]]
        tasks = [task1, task2, task3]
        result = self.plugin._get_tasks_hostnames(tasks)
        self.assertTrue("node1" in result)
        self.assertTrue("node2" in result)
        self.assertEquals(2, len(result))

    def test_not_repeated_grub_tasks(self):
        # http://jira-oss.lmera.ericsson.se/browse/LITPCDS-4799
        self.setup_model()
        self._create_dataset_primary_disk()

        # Now it will have 2 VGs
        self._create_dataset_secondary_disk()

        # Make node1 create snapshot tasks
        self.node1.system.set_applied()

        for vg in self.node1.storage_profile.volume_groups:
            vg.set_applied()
            for fs in vg.file_systems:
                fs.set_applied()

        # Not interested in MS tasks for this test
        self.plugin.lvm_driver._create_ms_non_modeled_ks_snapshot_tasks = MagicMock(return_value=[])

        self.plugin.lvm_driver._snap_operation_allowed = MagicMock(return_value=True)
        self.context.snapshot_action = MagicMock(return_value='create')

        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')

        self.assertEquals(1, len([t for t in tasks if 'grub' in t.description]))

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_validation_for_removed_items(self):

        disk1 = VolMgrMockDisk(
            item_id="disk1", name="hd0",
            size="1G", uuid="abcd", bootable="true")
        disk1.disk_part = "true"
        disk1.set_property = MagicMock(return_value=True)

        disk2 = VolMgrMockDisk(
            item_id="disk2",
            name="hd1", size="1G", uuid="wxyz", bootable="false")
        disk2.disk_part = "false"
        disk2.set_property = MagicMock(return_value=True)

        system1 = VolMgrMockSystem(item_id='sys1')

        pd1 = VolMgrMockPD(item_id='pd1',device_name='hd0')
        pd2 = VolMgrMockPD(item_id='pd2', device_name='hd1')

        fs1 = VolMgrMockFS(
            mount_point="/", size='200M', snap_size='0', item_id='abc')
        fs2 = VolMgrMockFS(
            mount_point="/wx", size='200M', snap_size='0', item_id='xyz')
        vg1 = VolMgrMockVG(item_id='vg1',volume_group_name='grp_1')
        vg2 = VolMgrMockVG(item_id='vg2',volume_group_name='grp_2')

        sp1 = VolMgrMockStorageProfile(item_id='sp1',volume_driver='lvm')
        sp1.view_root_vg='grp_1'

        cluster = VolMgrMockCluster(item_id="c1")
        node1 = VolMgrMockNode(item_id="node1",hostname="node1")
        cluster.nodes = [node1]

        vg1.physical_devices.append(pd1)
        vg1.file_systems.append(fs1)

        vg2.physical_devices.append(pd2)
        vg2.file_systems.append(fs2)

        disks = [disk1, disk2]
        physical_devices = [pd1, pd2]
        volume_groups = [vg1, vg2]
        file_systems = [fs1, fs2]

        sp1.volume_groups.extend(volume_groups)

        system1.disks.extend(disks)

        node1.system = system1
        node1.storage_profile = sp1
        node1.cluster_for_get=cluster

        for ilist in ([cluster],disks, physical_devices, volume_groups, [system1]):
            VolMgrMock._set_state_initial(ilist)

        for ilist in ([node1], file_systems):
            VolMgrMock._set_state_applied(ilist)

        storage = Mock(storage_profiles=[])

        infra1 = Mock(storage=storage)

        def _mock_query(query_item_type, **kwargs):
            if 'node' == query_item_type:
                return [node1]
            elif 'infrastructure' == query_item_type:
                return [infra1]
            elif 'ms' == query_item_type:
                return []
            else:
                return []

        context = Mock(query=_mock_query,
                       snapshot_name = MagicMock(return_value=''))
        errors = self.plugin.validate_model(context)
        self.assertEqual([], errors)

        context.snapshot_name = MagicMock(return_value='x'*100)
        errors = self.plugin.validate_model(context)
        self.assertEqual(0, len(errors))

        context.snapshot_name = MagicMock(return_value='x'*100)
        context.snapshot_action = MagicMock(return_value='create')
        with patch.object(self.plugin, '_validate_snap_tag_length',
                          MagicMock(return_value=['called'])):
            errors = self.plugin.validate_model_snapshot(context)
            self.assertEqual(1, len(errors))
            self.assertEqual('called', errors[0])
        context.snapshot_action = MagicMock(return_value='remove')
        with patch.object(self.plugin, '_validate_snap_tag_length',
                          MagicMock(return_value=['called'])):
            errors = self.plugin.validate_model(context)
            self.assertEqual(0, len(errors))

        context.snapshot_name = MagicMock(return_value='snapshot')
        context.snapshot_action = MagicMock(return_value='remove')
        with patch.object(self.plugin, '_validate_snap_tag_length',
                          MagicMock(return_value=['called'])):
            errors = self.plugin.validate_model(context)
        self.assertEqual(0, len(errors))

        #Cause 3 errors
        disk2.size = '50M'
        disk1.size = '50M'
        context.snapshot_name = MagicMock(return_value='x'*100)
        errors = self.plugin.validate_model(context)
        self.assertEqual(2, len(errors))

        # reset after test
        disk2.size = '1G'
        disk1.size = '1G'

        context.snapshot_name = MagicMock(return_value='test')

        disk3 = VolMgrMockDisk(
            item_id="hd2",
            name="hd2", size="1G", uuid="klmn", bootable=True)

        VolMgrMock._set_state_initial([disk3])
        disk3.set_property = MagicMock(return_value=True)
        system1.disks.append(disk3)

        VolMgrMock._set_state_applied([pd1])

        node1.get_state = MagicMock(return_value="Updated")
        VolMgrMock._set_state_updated([node1])

        tasks = self.plugin.create_configuration(context)
        self.assertEqual(3, len(tasks))

        VolMgrMock._set_state_for_removal([node1])

        errors = self.plugin.validate_model(context)
        self.assertEqual(0, len(errors))
        tasks = self.plugin.create_configuration(context)
        self.assertEqual(0, len(tasks))

    def test_veto_removal_of_ks_fs(self):
        # Case 1: Validation should report
        # an error for ks-fs in for removal
        # state

        fs = VolMgrMockFS(item_id='fs1',
                          size='50G',
                          mount_point='/')
        fs.type = 'ext4'
        VolMgrMock._set_state_for_removal([fs])

        vg = VolMgrMockVG("vg1", volume_group_name='vg_root')
        vg.file_systems = [fs]
        profile = VolMgrMockStorageProfile("kickstart", "lvm")
        profile.volume_groups = [vg]

        msg = 'The removal of an MS Kickstarted ' \
              'LVM file-system is not supported'

        val_error = ValidationError(
            item_path=fs.get_vpath(),
            error_message=msg
        )

        errors = self.plugin._validate_ms_veto_removal_of_ks_fs(
                                                      profile)

        self.assertEqual([val_error], errors)

        # Case 2: Validation should not report
        # an error for a non ks-fs in for removal state
        fs.mount_point = '/nonvar'
        errors = self.plugin._validate_ms_veto_removal_of_ks_fs(
            profile)
        self.assertEqual([], errors)

    def test_snapshots_on_ms_ks_fs(self):
        # Case 1: Modelling a kickstart file with
        # no increase in size while a snapshot is
        # present should raise an error
        fs = VolMgrMockFS(item_id='fs1',
                          size='50G',
                          mount_point='/')
        fs.type = 'ext4'
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_root")
        vg.file_systems.append(fs)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg)
        VolMgrMock._set_state_initial([sp, vg, fs])

        mock_snapshot = Mock(
            get_vpath=lambda: '/snapshots/snapshot',
            is_initial=lambda: True,
            is_for_removal=lambda: False,
            is_updated=lambda: False
        )

        def _mock_context_query(args, **kwargs):
            return [mock_snapshot]

        api_context = VolMgrMockContext()
        api_context.query = _mock_context_query

        msg = 'Modeling an MS Kickstarted file-system ' \
              'while a snapshot exists is not supported'

        val_error = ValidationError(
            item_path=fs.get_vpath(),
            error_message=msg
        )

        errors = self.plugin._validate_ms_ks_fs_snapshot_not_present(sp,
                                                                     api_context)

        self.assertEqual([val_error], errors)

        # Case 2: Modelling a new kickstart file with
        # an increase in size while a snapshot is
        # present should raise an error
        fs.size = '60G'
        errors = self.plugin._veto_fs_size_chg_if_snap_present(fs, api_context)

        msg = 'Snapshot(s) with name(s) "snapshot" exist. ' \
              'Changing the "size" property of an "ext4", "xfs" or ' \
              '"vxfs" file system while a snapshot exists ' \
              'is not supported'

        val_error = ValidationError(
            item_path=fs.get_vpath(),
            error_message=msg
        )

        self.assertEqual([val_error], errors)

        # Case 3: Modifying a kickstart file with
        # an increase in size while a snapshot is
        # present should raise an error
        fs.size = '60G'
        VolMgrMock._set_state_updated([fs])

        val_error = ValidationError(
            item_path=fs.get_vpath(),
            error_message=msg
        )

        errors = self.plugin._veto_fs_size_chg_if_snap_present(fs, api_context)

        self.assertEqual([val_error], errors)

    def test_validate_ks_fs_size_update(self):
        fs = VolMgrMockFS(item_id='fs1',
                           size='70G',
                           mount_point='/')

        vg = VolMgrMockVG("vg1", volume_group_name='vg_root')
        vg.file_systems = [fs]
        profile = VolMgrMockStorageProfile("kickstart", "lvm")
        profile.volume_groups = [vg]

        VolMgrMock._set_state_applied([vg])
        VolMgrMock._set_state_initial([fs])

        # --- fs is initial and size is == to kickstart size
        errors = self.plugin._validate_ms_ks_fs_size_update(profile)
        self.assertEqual([], errors)

        # --- fs is initial and size is greater than kickstart size
        fs.size = '80G'
        errors = self.plugin._validate_ms_ks_fs_size_update(profile)
        self.assertEqual([], errors)

        # --- fs is initial and size is less than kickstart size
        fs.size = '10G'
        errors = self.plugin._validate_ms_ks_fs_size_update(profile)
        expected = ValidationError(item_path='/file-system/fs1',
                                    error_message="Decreasing the 'size' property "
                                                  "of a file-system is not supported")
        self.assertEqual([expected], errors)

    def test_validate_fs_size_update(self):
        fs = Mock(is_updated=lambda: False,
                  get_vpath=lambda: '/a/b/c',
                  size="100G",
                  type='ext4',
                  applied_properties={'size': '100G'})
        vg = MockVG("vg1", "vg1")
        vg.file_systems = [fs]
        profile = MockStorageProfile("st1", "lvm")
        profile.volume_groups = [vg]
        api_context = Mock(
            query=lambda x: []
        )

        # --- 'is_updated' is false: no validation done
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        self.assertEqual([], errors)

        # Validly increasing from 10G to 100G - everything AOK
        fs.is_updated = lambda: True
        fs.applied_properties = {'size': '10G'}
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        self.assertEqual([], errors)

        # ----
        # Validly increasing size - but changing Units - everything AOK
        fs.size = '1G'
        fs.applied_properties = {'size': '57M'}
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        self.assertEqual([], errors)
        fs.size = '100G'

        # ----
        # Invalidly decreasing size
        fs.applied_properties = {'size': '200G'}
        expected = ValidationError(item_path='/a/b/c',
                                   error_message="Decreasing the 'size' property of a file-system is not supported")
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        self.assertEqual([expected], errors)

        # ----
        # Invalidly changing size for a 'swap' FS
        fs.applied_properties = {'size': '10G'}
        fs.type = 'swap'
        expected = ValidationError(item_path='/a/b/c',
                                   error_message="A change of 'size' is only permitted on a file-system of type 'ext4', 'xfs' or 'vxfs'")
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        self.assertEqual([expected], errors)

        # Validly increasing from 10G to 100G, but snapshot present
        mock_snapshot = Mock(
            get_vpath=lambda: '/snapshots/snapshot',
            is_initial=lambda: True,
            is_for_removal=lambda: False,
            is_updated=lambda: False
        )

        api_context.query = lambda x: [mock_snapshot]
        fs.is_updated = lambda: True
        fs.applied_properties = {'size': '10G'}
        fs.type = 'ext4'
        msg = 'Snapshot(s) with name(s) "snapshot" exist. ' \
              'Changing the "size" property of an "ext4", "xfs" or "vxfs" ' \
              'file system while a snapshot exists is not supported'
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        val_error = ValidationError(
                item_path=fs.get_vpath(),
                error_message = msg
                )
        self.assertEqual([val_error], errors)

        # Validly increasing from 10G to 100G for vxfs, but snapshot present
        fs = Mock(is_updated=lambda: True,
                  get_vpath=lambda: '/a/b/c',
                  size="100G",
                  type='vxfs',
                  applied_properties={'size': '10G'})
        vg = MockVG("vg1", "vg1")
        vg.file_systems = [fs]
        profile = MockStorageProfile("st1", "vxvm")
        profile.volume_groups = [vg]
        api_context.query = lambda x: [mock_snapshot]
        errors = self.plugin._validate_fs_size_update(profile, api_context)
        self.assertEqual([val_error], errors)

    def test__find_snapshot_names(self):
        mock_snapshot1 = Mock(
            get_vpath=lambda: '/snapshots/snapshot',
            is_for_removal = lambda: False
        )
        mock_snapshot2 = Mock(
            get_vpath=lambda: '/snapshots/test_snap',
            is_for_removal = lambda: False
        )
        snapshots = [mock_snapshot1, mock_snapshot2]

        context = VolMgrMockContext()
        def _ctx_query(args):
            if args == 'snapshot-base':
                return snapshots
        context.query = _ctx_query

        snapshot_names = self.plugin._find_snapshot_names(context)
        expected_names = "snapshot, test_snap"
        self.assertEqual(snapshot_names, expected_names)

        def _ctx_query(args):
            if args == 'snapshot-base':
                return []
        context.query = _ctx_query
        snapshot_names = self.plugin._find_snapshot_names(context)
        self.assertEqual(snapshot_names, None)

    def test__get_active_snapshots(self):
        context = VolMgrMockContext()
        def _ctx_query(args):
            if args == 'snapshot-base':
                return []
        context.query = _ctx_query

        context.snapshot_action = lambda: 'remove'
        snaps = self.plugin._get_active_snapshots(context)
        self.assertEqual([], snaps)

        def _raises_error():
            raise NoSnapshotItemError("err")

        context.snapshot_action = _raises_error
        snaps = self.plugin._get_active_snapshots(context)
        self.assertEqual([], snaps)

        context.snapshot_action = lambda: 'not_remove'
        snaps = self.plugin._get_active_snapshots(context)
        self.assertEqual([], snaps)

    def test_validate_disk_exist_on_every_node(self):
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='40G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        disk2 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk2',
                              size='40G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77305')
        disk3 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk2',
                              size='35G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77306')

        VolMgrMockDisk.set_properties(disk1)
        VolMgrMockDisk.set_properties(disk2)
        VolMgrMockDisk.set_properties(disk3)
        all_disks = [disk1, disk2, disk3]
        VolMgrMockDisk._set_state_applied(all_disks)


        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)
        sys1.disks.append(disk2)
        sys2 = VolMgrMockSystem(item_id='s2')
        sys2.disks.append(disk3)

        node1 = VolMgrMockNode(item_id="n1",
                               hostname="mn1")
        node2 = VolMgrMockNode(item_id="n2",
                               hostname="mn2")

        node1.system = sys1
        node2.system = sys2

        sp = VolMgrMockStorageProfile(item_id="sp",
                                      volume_driver="vxvm")

        VolMgrMock._set_state_applied([node1, node2, sp])
        cluster = VolMgrMockCluster(item_id="c1",
                                    cluster_type="sfha")

        cluster.storage_profile = [sp]
        cluster.nodes = [node1, node2]
        pd = VolMgrMockPD(item_id="pd1",
                           device_name="disk1")
        pd2 = VolMgrMockPD(item_id="pd2",
                          device_name="disk2")

        expected = "VxVM disk 'disk1' does not exist on node 'mn2'"
        errors = self.plugin._validate_disk_exist(cluster, pd)
        self.assertEqual(expected, errors[0].error_message)

        #Validate that every node in cluster must have same VxVM
        #disk
        errors = self.plugin._validate_disk_exist(cluster, pd2)
        self.assertEqual(6, len(errors))

        # if the host is for removal, the error should not be thrown
        node2.is_for_removal = lambda: True
        errors = self.plugin._validate_disk_exist(cluster, pd)
        self.assertEqual([], errors)

        # Node is not for removal, and disk is added. Error should not be thrown
        node2.is_for_removal = lambda: True
        sys2.disks.append(disk1)
        errors = self.plugin._validate_disk_exist(cluster, pd)
        self.assertEqual([], errors)

    def test_validate_node_profile_is_lvm(self):
        node2 = MagicMock()
        node2.hostname = "node2"
        node2.storage_profile.volume_driver = "vxvm"
        errors = self.plugin._validate_node_profile_is_lvm(node2)

        self.assertEqual(1, len(errors))
        self.assertTrue(
            errors[0].error_message,
            "ValidationError - A storage_profile at node level must have its "
            "volume_driver property set to 'lvm'"
        )

    def test_vxvm_profile_vcs_cluster(self):

        cluster = MockCluster("c1", "sfha")
        storage_profile = MockStorageProfile("sp1", "vxvm")
        cluster.query = MagicMock(return_value=[storage_profile])
        output = self.plugin._validate_cluster_type_sfha(cluster)
        self.assertEqual([], output)

        cluster = MockCluster("c1", "sfha")
        cluster.query = MagicMock(return_value=[])
        output = self.plugin._validate_cluster_type_sfha(cluster)
        self.assertEqual([], output)

        cluster = MockCluster("c1", "vcs")
        storage_profile = MockStorageProfile("sp1", "vxvm")
        cluster.query = MagicMock(return_value=[storage_profile])
        error = self.plugin._validate_cluster_type_sfha(cluster)

        self.assertEqual(1, len(error))
        self.assertTrue(
            error[0].error_message,
            "ValidationError - A cluster with vxvm storage profile "
            "must be of type 'sfha'"
        )

    def test_validate_unique_dg_name(self):

        cluster = MockCluster("c1", "sfha")
        vg1 = MockVG("vg1", "vg1")
        vg1.get_vpath = MagicMock(return_value="/my/path1")
        vg2 = MockVG("vg2", "vg2")
        vg2.get_vpath = MagicMock(return_value="/my/path2")
        vg3 = MockVG("vg2", "vg2")
        vg3.get_vpath = MagicMock(return_value="/my/path3")
        storage_profile = MockStorageProfile("sp2", "vxvm")
        storage_profile.volume_groups = [vg1, vg2, vg3]
        cluster.query = MagicMock(return_value=[storage_profile])

        errors = self.plugin._validate_unique_dg_name(cluster)
        self.assertEqual(len(errors), 1)
        self.assertEqual('VxVM volume_group_name "vg2" is not unique'
                         ' for cluster "c1"',
                         errors[0].error_message
                         )

        cluster2 = MockCluster("c1", "sfha")
        storage_profile2 = MockStorageProfile("sp2", "vxvm")
        storage_profile2.volume_groups = [vg1, vg2]
        cluster2.query = MagicMock(return_value=[storage_profile2])
        errors = self.plugin._validate_unique_dg_name(cluster2)
        self.assertEqual(len(errors), 0)
        self.assertEqual(errors, [])

        def test_validate_unique_dg_name(self):


            cluster = MockCluster("c1", "sfha")
            vg1 = MockVG("vg1", "vg1")
            vg1.get_vpath = MagicMock(return_value="/my/path1")
            vg2 = MockVG("vg2", "vg2")
            vg2.get_vpath = MagicMock(return_value="/my/path2")
            vg3 = MockVG("vg2", "vg2")
            vg3.get_vpath = MagicMock(return_value="/my/path3")
            storage_profile = MockStorageProfile("sp2", "vxvm")
            storage_profile.volume_groups = [vg1, vg2, vg3]
            cluster.query = MagicMock(return_value=[storage_profile])

            errors = self.plugin._validate_unique_dg_name(cluster)
            self.assertEqual(len(errors), 1)
            self.assertEqual('VxVM volume_group_name "vg2" is not unique'
                             ' for cluster "c1"',
                             errors[0].error_message
                             )

            cluster2 = MockCluster("c1", "sfha")
            storage_profile2 = MockStorageProfile("sp2", "vxvm")
            storage_profile2.volume_groups = [vg1, vg2]
            cluster2.query = MagicMock(return_value=[storage_profile2])
            errors = self.plugin._validate_unique_dg_name(cluster2)
            self.assertEqual(len(errors), 0)
            self.assertEqual(errors, [])

    def test_validate_model_duplicate_disk_uuid(self):

        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='true',
                              name='disk2',
                              size='33G',
                              uuid='ABCD_1234')

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1, disk2])
        node = VolMgrMockNode(item_id="n1",
                              hostname="mn1")
        node.system = sys
        all_items = [disk1, disk2, node]
        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if 'node' == args:
                return [node]
            else:
                return []

        context = VolMgrMockContext()
        context.query = _mock_context_query

        errors = self.plugin.validate_model(context)
        expected = "System Disk uuid '{0}' is not unique"
        expected = expected.format(disk1.uuid)
        self.assertEqual(2, len(errors))
        self.assertTrue(all([err.error_message == expected
                         for err in errors]))

        # LITPCDS-11128 - validation must catch differences in
        # capitalisation
        disk2.uuid = 'abcd_1234'
        errors = self.plugin.validate_model(context)
        base_msg = "System Disk uuid '{0}' is not unique"
        exp1 = ValidationError(
            error_message=base_msg.format(disk1.uuid),
            item_path=disk1.get_vpath()
        )
        exp2 = ValidationError(
            error_message=base_msg.format(disk2.uuid),
            item_path=disk2.get_vpath()
        )
        self.assertEqual(2, len(errors))
        self.assertTrue(all([exp1 in errors, exp2 in errors]))

    def generate_cluster_with_many_disks_12928(self):

        # disks in the cluster used in LVM and VXVM storage profiles
        ms1_disk0 = VolMgrMockDisk(
            'disk0', bootable='true', name='sda', size='100G', uuid='abc-123'
        )

        ms1_disk1 = VolMgrMockDisk(
            'disk1', bootable='false', name='sdb', size='40G', uuid='abc-124'
        )

        # unreferenced disk, check physical devices
        ms1_disk2 = VolMgrMockDisk(
            'disk2', bootable='false', name='sdc', size='80G', uuid='abc-125'
        )

        mn1_disk0 = VolMgrMockDisk(
            'disk0', bootable='true', name='sda', size='40G', uuid='xyz-123'
        )

        mn1_disk1 = VolMgrMockDisk(
            'disk1', bootable='false', name='sdb', size='20G', uuid='xyz-124'
        )

        # unreferenced disk, check physical devices
        mn1_disk3 = VolMgrMockDisk(
            'disk3', bootable='false', name='sdc', size='20G', uuid='xyz-444'
        )

        # disks a sfha cluster on vxvm
        mn1_disk2 = VolMgrMockDisk(
            'vx0', bootable='false', name='vxdisk0', size='20G', uuid='aabbcc'
        )

        # lun disks to test LITPCDS-13765
        mn1_lundisk0 = VolMgrMockOtherDisk(
            'lun0', name='lundisk0', size='40G', uuid='xyz-126',
            bootable='false'
        )

        mn1_lundisk1 = VolMgrMockOtherDisk(
            'lun1', name='lundisk1', size='40G', uuid='xyz-127',
            bootable='false'
        )

        ms1_disks = [ms1_disk0, ms1_disk1, ms1_disk2]
        mn1_disks = [mn1_disk0, mn1_disk1, mn1_disk2, mn1_disk3,
                     mn1_lundisk0, mn1_lundisk1]

        sys0 = VolMgrMockSystem('s0', system_name='MS1')
        sys1 = VolMgrMockSystem('s1', system_name='MN1')
        sys1.query = VolMgrMock.mock_query({
            'disk': mn1_disks,
        })

        # add disks to the systems
        sys0.disks += ms1_disks
        sys1.disks += mn1_disks

        ms1 = VolMgrMockMS()
        ms1.system = sys0

        node1 = VolMgrMockNode("n1", hostname="mn1")
        node1.system = sys1

        # MS set up storage profile with disks
        ms1_sp0 = VolMgrMockStorageProfile(
            'sp0', volume_driver='lvm', ms_for_get=ms1
        )

        ms1_vg0 = VolMgrMockVG(
            'vg0', volume_group_name='vg_foo', ms_for_get=ms1
        )

        ms1_pd0 = VolMgrMockPD('pd0', device_name='sda')
        ms1_pd0.get_source = lambda: ms1_disk0

        ms1_pd1 = VolMgrMockPD('pd1', device_name='sdb')
        ms1_pd1.get_source = lambda: ms1_disk1

        ms1_pds = [ms1_pd0, ms1_pd1]
        ms1_vg0.physical_devices += ms1_pds
        ms1_sp0.volume_groups.append(ms1_vg0)

        ms1.storage_profile = ms1_sp0
        ms1.storage_profile.query = VolMgrMock.mock_query({
            'physical-device': ms1_pds,
            'volume-group': [ms1_vg0],
        })

        # MN set up storage profile with disks
        mn1_sp0 = VolMgrMockStorageProfile(
            'sp0', volume_driver='lvm', node_for_get=node1
        )

        mn1_vg0 = VolMgrMockVG(
            'vg0', volume_group_name='vg_foo', node_for_get=node1
        )

        mn1_pd0 = VolMgrMockPD('pd0', device_name='sda')
        mn1_pd0.get_source = lambda: mn1_disk0

        mn1_pd1 = VolMgrMockPD('pd1', device_name='sdb')
        mn1_pd1.get_source = lambda: mn1_disk1

        mn1_pds = [mn1_pd0, mn1_pd1]
        mn1_vg0.physical_devices += mn1_pds
        mn1_sp0.volume_groups.append(mn1_vg0)

        # See LITPCDS-13765
        mn1_lun_vg = VolMgrMockVG(
            'vg1', volume_group_name='vg_lun', node_for_get=node1
        )

        mn1_lun_pd0 = VolMgrMockPD('mn1_lun_pd0', device_name='lundisk1')
        mn1_lun_pd0.get_source = lambda: mn1_lundisk1

        mn1_lun_pds = [mn1_lun_pd0]
        mn1_lun_vg.physical_devices += mn1_lun_pds
        mn1_sp0.volume_groups.append(mn1_lun_vg)

        mn1_pds.extend(mn1_lun_pds)

        node1.storage_profile = mn1_sp0
        node1.storage_profile.query = VolMgrMock.mock_query({
            'physical-device': mn1_pds,
            'volume-group': [mn1_vg0, mn1_lun_vg],
        })

        # Cluster set up storage profile with disks
        c1 = VolMgrMockVCSCluster("c1", cluster_type="sfha")

        vx_sp0 = VolMgrMockStorageProfile(
            "vx_sp", volume_driver="vxvm", cluster_for_get=c1
        )

        vx_vg0 = VolMgrMockVG(
            'vg0', volume_group_name='vx_vg_foo', cluster_for_get=c1
        )

        vx_pd0 = VolMgrMockPD('vx_pd0', device_name='vxdisk0')
        vx_pd0.get_source = lambda: mn1_disk2

        vx_vg0.physical_devices.append(vx_pd0)
        vx_sp0.volume_groups.append(vx_vg0)
        cluster_pds = [vx_pd0]

        # See LITPCDS-13765
        lun_vg0 = VolMgrMockVG(
            'vg1', volume_group_name='lun_vg_foo', cluster_for_get=c1
        )

        lun_pd0 = VolMgrMockPD('lun_pd0', device_name='lundisk0')
        lun_pd0.get_source = lambda: mn1_lundisk0

        lun_vg0.physical_devices.append(lun_pd0)
        vx_sp0.volume_groups.append(lun_vg0)
        lun_pds = [lun_pd0]

        cluster_pds.extend(lun_pds)

        vx_sp0.query = VolMgrMock.mock_query({
            'physical-device': cluster_pds,
            'volume-group': [vx_vg0, lun_vg0],
        })

        c1.storage_profile.append(vx_sp0)
        c1.nodes.append(node1)
        c1.query = VolMgrMock.mock_query({
            'storage-profile': [vx_sp0],
            'node': [node1],
        })

        all_disks = ms1_disks + mn1_disks
        all_nodes = [node1]
        all_mss = [ms1]
        all_sps =[ms1_sp0, mn1_sp0, vx_sp0]
        all_vgs = [ms1_vg0, mn1_vg0, vx_vg0, lun_vg0, mn1_lun_vg]
        all_pds = ms1_pds + mn1_pds + cluster_pds + lun_pds
        all_clusters = [c1]

        all_items = (
            all_disks + all_nodes + all_mss +
            all_sps + all_vgs + all_pds + all_clusters
        )

        VolMgrMock._set_state_applied(all_items)

        context = VolMgrMockContext()

        context.query = VolMgrMock.mock_query({
            'disk': all_disks,
            'disk-base': all_disks,
            'vcs-cluster': all_clusters,
            'node': all_nodes,
            'ms': all_mss,
            'storage-profile': all_sps,
            'volume-group': all_vgs,
            'physical-device': all_pds,
        })

        return context

    def test_validate_ms_disk_properties(self):

        context = self.generate_cluster_with_many_disks_12928()

        mss = context.query('ms')
        ms1 = mss[0]

        # Case 1: positive case no updated items
        errors = self.plugin._validate_ms_disk_properties(ms1)
        self.assertEquals(0, len(errors))

        # get the disk so that you can change the relevant values
        pds = ms1.storage_profile.query('physical-device', device_name='sda')
        self.assertEquals(1, len(pds))
        disk = VolMgrUtils.get_node_disk_for_pd(ms1, pds[0])

        # Case 2: positive case where MS uuid is updated
        old_uuid = disk.uuid
        disk.uuid = 'something-else'
        VolMgrMock._set_state_updated([disk])
        errors = self.plugin._validate_ms_disk_properties(ms1)
        self.assertEquals(0, len(errors))
        disk.uuid = old_uuid
        VolMgrMock._set_state_applied([disk])

        # Case 3: positive case where the MS disk size is increased
        old_size = disk.size
        disk.size = '120G'
        VolMgrMock._set_state_updated([disk])
        errors = self.plugin._validate_ms_disk_properties(ms1)
        self.assertEquals(0, len(errors))
        disk.size = old_size
        VolMgrMock._set_state_applied([disk])

        # Case 4: negative case where the MS disk size is decreased
        old_size = disk.size
        disk.size = '60G'
        VolMgrMock._set_state_updated([disk])
        errors = self.plugin._validate_ms_disk_properties(ms1)
        self.assertEquals(1, len(errors))
        disk.size = old_size
        VolMgrMock._set_state_applied([disk])

    def test_validate_mn_disk_properties(self):

        context = self.generate_cluster_with_many_disks_12928()

        mns = context.query('node')
        mn1 = mns[0]

        # get the disk so that you can change the relevant values
        pds = mn1.storage_profile.query('physical-device', device_name='sda')
        self.assertEquals(1, len(pds))
        disk = VolMgrUtils.get_node_disk_for_pd(mn1, pds[0])

        # Case 1: negative case where MN uuid is updated
        old_uuid = disk.uuid
        disk.uuid = 'something-else'
        VolMgrMock._set_state_updated([disk])
        errors = self.plugin._validate_mn_disk_properties(mn1)
        self.assertEquals(1, len(errors))
        disk.uuid = old_uuid
        VolMgrMock._set_state_applied([disk])

        # Case 2: negative case where the MN disk size is decreased
        old_size = disk.size
        disk.size = '30G'
        VolMgrMock._set_state_updated([disk])
        errors = self.plugin._validate_mn_disk_properties(mn1)
        self.assertEquals(1, len(errors))
        disk.size = old_size

        # Case 3: See LITPCDS-13765
        pds = mn1.storage_profile.query('physical-device', device_name='lundisk1')
        self.assertEquals(1, len(pds))
        disk = VolMgrUtils.get_node_disk_for_pd(mn1, pds[0])
        disk.uuid = 'something-else'
        disk.size = '60G'

        VolMgrMock._set_state_updated([disk])

        errors = self.plugin._validate_mn_disk_properties(mn1)
        self.assertEquals(0, len(errors))

        # Case 4: positive case where the MN disk size is increased
        old_size = disk.size
        disk.size = '50G'
        VolMgrMock._set_state_updated([disk])
        errors = self.plugin._validate_mn_disk_properties(mn1)
        self.assertEquals(0, len(errors))
        disk.size = old_size

    def test_validate_node_disk_properties(self):

        context = self.generate_cluster_with_many_disks_12928()

        mns = context.query('node')
        mn1 = mns[0]
        pds = mn1.storage_profile.query('physical-device', device_name='sda')
        self.assertEquals(1, len(pds))
        mn1_pd = pds[0]
        mn1_disk = VolMgrUtils.get_node_disk_for_pd(mn1, pds[0])

        mss = context.query('ms')
        ms1 = mss[0]
        pds = ms1.storage_profile.query('physical-device', device_name='sda')
        self.assertEquals(1, len(pds))
        ms1_pd = pds[0]
        ms1_disk = VolMgrUtils.get_node_disk_for_pd(ms1, ms1_pd)

        mn1_disk.uuid = "xxxxxx"
        mn1_disk.size = "100G"
        ms1_disk.uuid = "yyyyyy"
        ms1_disk.size = "60G"

        VolMgrMock._set_state_updated([mn1_disk, ms1_disk])

        with patch.object(self.plugin.lvm_driver, '_get_vg_data_by_rpc') as m:

            m.return_value = {'uuid': 'xxxxxx'}

            errors = self.plugin.validate_model(context)

            self.assertEquals(2, len(errors), errors)

            expected = [
                ValidationError(
                    mn1_disk.get_vpath(),
                    error_message=(
                        'Updating the "uuid" property of disk "{device_name}" '
                        'associated with LVM storage-profile on peer node '
                        '"{hostname}" is not supported').format(
                        device_name=mn1_disk.name,
                        hostname=mn1.hostname
                    )
                ),
                ValidationError(
                    ms1_disk.get_vpath(),
                    error_message=(
                        'Decreasing the "size" property of disk '
                        '"{device_name}" associated with LVM '
                        'storage-profile on management server '
                        '"{hostname}" is not supported').format(
                        device_name=ms1_disk.name,
                        hostname=ms1.hostname
                    )
                ),
            ]

            VolMgrMock.assert_validation_errors(
                self, expected, errors
            )

        # Ensure that if the disk is decreased you don't also get validation
        # errors about size requirements because this is misleading
        VolMgrMock._set_state_applied([mn1_disk])
        ms1_disk.size = "12M"
        VolMgrMock._set_state_updated([ms1_disk])

        with patch.object(self.plugin.lvm_driver, '_get_vg_data_by_rpc') as m:

            errors = self.plugin.validate_model(context)

            self.assertEquals(1, len(errors), errors)

            expected = [
                ValidationError(
                    ms1_disk.get_vpath(),
                    error_message=(
                        'Decreasing the "size" property of disk '
                        '"{device_name}" associated with LVM '
                        'storage-profile on management server '
                        '"{hostname}" is not supported').format(
                        device_name=ms1_disk.name,
                        hostname=ms1.hostname
                    )
                ),
            ]

            VolMgrMock.assert_validation_errors(
                self, expected, errors
            )

    def test_validate_cluster_disk_properties(self):

        context = self.generate_cluster_with_many_disks_12928()

        clusters = context.query('vcs-cluster')
        self.assertEquals(1, len(clusters))
        c1 = clusters[0]

        vx_disks = c1.nodes[0].system.query('disk', name='vxdisk0')
        self.assertEquals(1, len(vx_disks))

        vx_pd = c1.storage_profile[0].volume_groups[0].physical_devices[0]

        lun_disks = c1.nodes[0].system.query('disk', name='lundisk0')
        self.assertEquals(1, len(lun_disks))

        with patch.object(self.plugin.lvm_driver, '_get_vg_data_by_rpc') as m:

            m.return_value = {'uuid': 'abc-123'}

            vxdisk_on_node1 = vx_disks[0]

            vxdisk_on_node1.size = "30G"
            VolMgrMock._set_state_updated([vxdisk_on_node1])

            # positive test
            errors = self.plugin.validate_model(context)
            self.assertEquals(0, len(errors))
            vxdisk_on_node1.size = "20G"
            VolMgrMock._set_state_applied([vxdisk_on_node1])

            # See LITPCDS-13765
            vxdisk_on_node1.uuid = "xxxxxx"
            vxdisk_on_node1.size = "10G"
            VolMgrMock._set_state_updated([vxdisk_on_node1])

            errors = self.plugin.validate_model(context)
            self.assertEquals(2, len(errors), errors)

            expected = [
                ValidationError(
                    vxdisk_on_node1.get_vpath(),
                    error_message=(
                        'Decreasing the "size" property of disk "{device_name}" '
                        'associated with VxVM storage-profile on peer node '
                        '"{hosts}" in cluster "{cluster}" is not supported'
                    ).format(
                        device_name=vxdisk_on_node1.name,
                        hosts=VolMgrUtils.format_list([c1.nodes[0].hostname]),
                        cluster=c1.item_id
                    )
                ),
                ValidationError(
                    vxdisk_on_node1.get_vpath(),
                    error_message=(
                        'Updating the "uuid" property of disk "{device_name}" '
                        'associated with VxVM storage-profile on peer node '
                        '"{hosts}" in cluster "{cluster}" is not supported'
                    ).format(
                        device_name=vx_pd.device_name,
                        hosts=VolMgrUtils.format_list([c1.nodes[0].hostname]),
                        cluster=c1.item_id
                    )
                ),
            ]

            VolMgrMock.assert_validation_errors(
                self, expected, errors
            )

            VolMgrMock._set_state_applied([vxdisk_on_node1])

        with patch.object(self.plugin.lvm_driver, '_get_vg_data_by_rpc') as m:

            lundisk_on_node1 = lun_disks[0]

            lundisk_on_node1.size = "50G"
            VolMgrMock._set_state_updated([lundisk_on_node1])

            # positive test
            errors = self.plugin.validate_model(context)
            self.assertEquals(0, len(errors))
            VolMgrMock._set_state_applied([lundisk_on_node1])

            # See LITPCDS-13765
            lundisk_on_node1.uuid = "xxxxxx"
            lundisk_on_node1.size = "10G"
            VolMgrMock._set_state_updated([lundisk_on_node1])

            errors = self.plugin.validate_model(context)
            self.assertEquals(0, len(errors), errors)

    def test_cb_vcs_callback_wrapper(self):

        self.plugin.vxvm_driver.vx_disk_group_setup = MagicMock(return_value="test_return")
        self.plugin._vx_diskgroup_setup("context", "disk_group", "a", "b")
        self.assertTrue(1)

    def test_validate_unique_pd_name_fail(self):
        pd1 = VolMgrMockPD(item_id="pd1",
                           device_name="hd0")
        pd2 = VolMgrMockPD(item_id="pd2",
                           device_name=pd1.device_name)
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_vxvm")
        vg.physical_devices.append(pd1)
        vg.physical_devices.append(pd2)

        VolMgrMock._set_state_applied([pd1, pd2])

        sp = VolMgrMockStorageProfile(item_id="sp",
                                      volume_driver="vxvm")

        sp.volume_groups.append(vg)

        def _mock_query(query_item_type, **kwargs):
            if 'storage-profile' == query_item_type:
                return [sp]
            else:
                return []

        clus = VolMgrMockCluster(item_id="c1",
                                 cluster_type="sfha")

        clus.query = _mock_query

        clus.storage_profile = [sp]

        errors = self.plugin._validate_unique_pd_name(clus)
        expected = "Physical device name is not unique for the VxVM {0} profile"
        expected = expected.format(sp.item_id)
        self.assertEqual(expected, errors[0].error_message )

    def test_validate_unique_pd_name_pass(self):
        pd1 = VolMgrMockPD(item_id="pd1",
                           device_name="hd0")
        pd2 = VolMgrMockPD(item_id="pd2",
                           device_name="hd1")
        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg_vxvm")

        VolMgrMock._set_state_applied([pd1, pd2])

        vg.physical_devices.append(pd1)
        vg.physical_devices.append(pd2)
        sp = VolMgrMockStorageProfile(item_id="sp",
                                      volume_driver="vxvm")
        sp.volume_groups.append(vg)
        clus = VolMgrMockCluster(item_id="c1",
                                 cluster_type="sfha")

        def _mock_query(query_item_type, **kwargs):
            if 'storage-profile' == query_item_type:
                return [sp]
            else:
                return []

        clus.query = _mock_query

        clus.storage_profile = [sp]
        VolMgrMock._set_state_applied([clus])
        errors = self.plugin._validate_unique_pd_name(clus)

        self.assertEqual(errors, [])

    def test_validate_veto_of_pd_remove(self):
        mock_applied_pd = MagicMock()
        mock_initial_pd = MagicMock()
        mock_removed_pd = MagicMock()

        mock_applied_pd.is_for_removal = MagicMock(return_value=False)
        mock_initial_pd.is_for_removal = MagicMock(return_value=False)
        mock_removed_pd.is_for_removal = MagicMock(return_value=True)
        mock_removed_pd.get_vpath = MagicMock(return_value = '/some/path')

        mock_vg = MagicMock()
        mock_vg.physical_devices = [mock_applied_pd, mock_removed_pd, mock_initial_pd]
        mock_vg.is_applied = MagicMock(return_value=True)

        profile = MagicMock()
        profile.volume_groups = [mock_vg]

        self.assertEqual(
            [ValidationError(
                item_path=mock_removed_pd.get_vpath(),
                error_message='Removal of active physical device is not permitted.'
                )],
            self.plugin._validate_veto_removal_of_pd(profile)
            )

    def test_validate_snapshot_name_length(self):

        # Case 1: validate LVM snapshot name lengths
        context = self._generate_test_model_for_lvm_create_snapshot()

        # Part 1: fails because the tag exceeds max allowed
        context.snapshot_name = MagicMock(return_value='x'*200)
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(1, len(errors))

        # Part 2: passes because the tag is less than the max allowed
        context.snapshot_name = MagicMock(return_value='test_lvm_tag_name')
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(0, len(errors))

        context.snapshot_action = MagicMock(return_value='remove')
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(0, len(errors))

        def mock_snap_action():
            raise NoSnapshotItemError("oops")

        context.snapshot_action = MagicMock(side_effect=mock_snap_action)
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(0, len(errors))

        # Case 2: validate VXVM snapshot name lengths
        context = self._generate_test_model_for_vxvm_create_snapshot()
        context.snapshot_name = MagicMock(return_value = 'test_vxvm_tag_name')

        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual([], errors)

        context.snapshot_name = MagicMock(return_value='x'*50)
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(1, len(errors))

        context.snapshot_name = MagicMock(return_value='snapshot')
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(0, len(errors))

        context.snapshot_name = MagicMock(return_value='')
        errors = self.plugin.validate_model_snapshot(context)
        self.assertEqual(0, len(errors))

    def test__validate_all_same_disks(self):
        disk1=MagicMock()
        disk2=MagicMock()
        disk3=MagicMock()
        disk1.properties={'a':1,'b':2,'c':3}
        disk2.properties={'a':1,'b':2,'c':3}
        disk3.properties={'a':1,'b':2}
        disks = [disk1, disk2, disk3]
        self.assertEqual(False, self.plugin._validate_all_same_disks(disks))

    def test_get_error_parameter_not_same(self):
        clus = VolMgrMockCluster(item_id="c1",
                                    cluster_type="sfha")
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='hd0')
        infra_item = pd1.get_source()
        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk1",
                               size='50G',
                               uuid='uuid1')
        # Different name
        disk2 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk2",
                               size='50G',
                               uuid='uuid1')
        # Different size
        disk3 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk1",
                               size='500G',
                               uuid='uuid1')
        # Different bootable
        disk4 = VolMgrMockDisk(item_id='d1',
                               bootable='true',
                               name="disk1",
                               size='50G',
                               uuid='uuid1')
        # Different UUID
        disk5 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk1",
                               size='50G',
                               uuid='uuid2')
        #Different name and size
        disk6 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk3",
                               size='55G',
                               uuid='uuid1')
        #Two initial disk with no uuid
        disk7 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk3",
                               size='55G',
                               uuid=None)
        disk8 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk3",
                               size='55G',
                               uuid=None)


        error_template = 'Property "{0}" with value "{1}" on VxVM disk must be ' \
                         'the same on every node in cluster "{2}"'

        applied_disks = [disk1, disk2, disk3, disk4, disk5, disk6]
        VolMgrMock._set_state_applied(applied_disks)
        initial_disks = [disk7, disk8]
        VolMgrMock._set_state_initial(initial_disks)
        all_disks = applied_disks + initial_disks
        for disk in all_disks:
            VolMgrMock.set_properties(disk)

        # Test different name
        expected = []
        disks = [disk1, disk2]
        for disk in disks:
            expected.append(error_template.format("name",
                                                  getattr(disk, "name"),
                                                  clus.item_id))
        errors = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual(2, len(errors))
        self.assertTrue(all([err.error_message in expected
                             for err in errors ]))

        # Test different size
        expected = []
        disks = [disk1, disk3]
        for disk in disks:
            expected.append(error_template.format("size",
                                             getattr(disk, "size"),
                                             clus.item_id))
        errors = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual(2, len(errors))
        self.assertTrue(all([err.error_message in expected
                             for err in errors ]))

        # Test different bootable
        expected = []
        disks = [disk1, disk4]
        for disk in disks:
            expected.append(error_template.format("bootable",
                                             getattr(disk, "bootable"),
                                             clus.item_id))
        errors = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual(2, len(errors))
        self.assertTrue(all([err.error_message in expected
                             for err in errors ]))


        # Test different uuid
        expected = []
        disks = [disk1, disk5]
        for disk in disks:
            expected.append(error_template.format("uuid",
                                             getattr(disk, "uuid"),
                                             clus.item_id))
        errors = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual(2, len(errors))
        self.assertTrue(all([err.error_message in expected
                             for err in errors ]))

         # Test different name and size
        expected = []
        disks = [disk1, disk6]
        for disk in disks:
            expected.append(error_template.format("name",
                                             getattr(disk, "name"),
                                             clus.item_id))
            expected.append(error_template.format("size",
                                             getattr(disk, "size"),
                                             clus.item_id))
        errors = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual(4, len(errors))
        self.assertTrue(all([err.error_message in expected
                             for err in errors ]))

        # Test all same
        disks = [disk1, disk1]
        errors = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual([], errors)

        #Test 1 disk
        disks = [disk1]
        error = self.plugin._get_error_parameter_not_same(disks,
                                                          clus)
        self.assertEqual([], error)

    def test_format_error_disk_property_not_same(self):
        clus = VolMgrMockCluster(item_id="c1",
                                    cluster_type="sfha")
        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk1",
                               size='50G',
                               uuid='uuid1')
        # Different name
        disk2 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name="disk2",
                               size='50G',
                               uuid='uuid1')
        disks = [disk1]
        errors = self.plugin._format_error_disk_property_not_same(disks,
                                                                  clus,
                                                                  "bootable")
        self.assertEqual(1, len(errors))

        disks = [disk1, disk2]
        errors = self.plugin._format_error_disk_property_not_same(disks,
                                                                  clus,
                                                                  "name")
        self.assertEqual(2, len(errors))

        disks = [disk1, disk2]
        errors = self.plugin._format_error_disk_property_not_same(disks,
                                                                  clus,
                                                                  "size")
        self.assertEqual(2, len(errors))

        disks = [disk2]
        errors = self.plugin._format_error_disk_property_not_same(disks,
                                                                  clus,
                                                                  "uuid")
        self.assertEqual(1, len(errors))

        disks = []
        errors = self.plugin._format_error_disk_property_not_same(disks,
                                                                  clus,
                                                                  "uuid")
        self.assertEqual([], errors)

    def test__restore_vxvm_snapshot_task(self):
        cluster = MagicMock()
        cluster.vpath= 'path'
        cb_api = MagicMock()
        cb_api.query=MagicMock(return_value=[cluster])
        node1 = MagicMock()
        node1.hostname= 'n1'
        agent = 'vxvm'
        action = 'something'
        dg_name = 'storeg'
        kwargs = dict()
        def mock_get_dg_hostname(ctx, cl, dg_names,
                                                    ignore_unreachable=False):
            self.assertEqual(cluster, cl)
            self.assertTrue(len(dg_names)==1)
            self.assertTrue(dg_names[0]=='storeg')
            return {'storeg':(node1, [])}
        def mock_base_task(api, hostname_list, agent, action, **kwargs):
            self.assertEqual(api,cb_api)
            self.assertEqual(hostname_list[0],'n1')
            self.assertEqual(agent,'vxvm')
            self.assertEqual(action,'something')
        with patch.object(self.plugin.vxvm_driver,'get_dg_hostname',
            MagicMock(side_effect=mock_get_dg_hostname)):
            self.plugin._base_rpc_task = MagicMock(side_effect=mock_base_task)
            self.plugin._base_with_imported_dg(cb_api, 'path', agent, action,
                                    dg_name, **kwargs)

    def test__wait_for_vxvm_restore_rpc(self):
        nodes = ['n1']
        callback_api = MagicMock()
        callback_api.rpc_command.return_value = {"n1": {'data': {'out': "off",
                                                                'status': 0,
                                                                'err': ''},
                                                        'errors': ''
                                                        }
                                                 }

        rpc_data = {'max_wait': 3600, 'disk_group': 'storeg', 'volume_name': 'vol0'}
        bad_rpc_data = {'max_wait': 3600, 'disk_group': 'storeg'}
        try:
            self.plugin._wait_for_vxvm_restore_rpc(callback_api, nodes, **bad_rpc_data)
            self.assertFail()
        except Exception as e:
            self.assertEqual(type(e), CallbackExecutionException)
            self.assertEqual(str(e), "The disk group name or the volume name are missing")

        self.assertTrue(None ==
            self.plugin._wait_for_vxvm_restore_rpc(callback_api, nodes, **rpc_data))

        callback_api.rpc_command.return_value = {"n1": {'data': {'out': "on",
                                                                'status': 0,
                                                                'err': ''},
                                                        'errors': ''
                                                        }
                                                 }

        rpc_data = {'max_wait': -1, 'disk_group': 'storeg', 'volume_name': 'vol0'}
        try:
           self.plugin._wait_for_vxvm_restore_rpc(callback_api, nodes, **rpc_data)
           self.assertFail()
        except Exception as e:
           self.assertEqual(type(e), CallbackExecutionException)
           self.assertEqual(str(e), "Restore has not completed within -1 seconds")
        callback_api.rpc_command.side_effect = RpcExecutionException("Oops I did it again")
        try:
            self.plugin._wait_for_vxvm_restore_rpc(callback_api, nodes, **rpc_data)
            self.assertFail()
        except Exception as e:
            self.assertEqual(type(e), PluginError)
            self.assertEqual(str(e), "Oops I did it again")

    def test__restore_ss_rpc_task(self):
        self.plugin.base_processor.execute_rpc_and_process_result= MagicMock(return_value=(None, {'err1':['test1'],'err2':['test2']}))
        self.assertRaises(CallbackExecutionException, self.plugin._restore_ss_rpc_task, 1, 2, 3, 4)
        self.plugin.base_processor.execute_rpc_and_process_result= MagicMock(return_value=(None, {'err1':['Unable to merge invalidated snapshot LV'],'err2':['test2']}))
        self.assertRaises(CallbackExecutionException, self.plugin._restore_ss_rpc_task, 1, 2, 3, 4)
        self.plugin.base_processor.execute_rpc_and_process_result= MagicMock(side_effect=RpcExecutionException())
        self.assertRaises(CallbackExecutionException, self.plugin._restore_ss_rpc_task, 1, 2, 3, 4)

    def test__remove_snapshot_rpc(self):

        self.plugin.base_processor.execute_rpc_and_process_result = MagicMock(
            return_value=(None, {'err1': ['test1'], 'err2': ['test2']})
        )

        cb_api = self.plugin.lvm_driver.rpc_callbacks['remove_snapshot']

        self.assertRaises(
            CallbackExecutionException,
            self.plugin._remove_snapshot_cb, cb_api, 'hostname', timeout=200
        )
        self.plugin.base_processor.execute_rpc_and_process_result = \
            MagicMock(side_effect=RpcExecutionException())

        self.assertRaises(
            CallbackExecutionException,
            self.plugin._remove_snapshot_cb, cb_api, 'hostname', timeout=200,
        )

    def test__restart_nodes(self):
        n1 =MagicMock()
        n2=MagicMock()
        nodes = [n1, n2]
        ss=MagicMock()
        api=MagicMock()
        api.query=MagicMock(return_value=[ss])
        self.plugin._restart_node(api, nodes)

    def test__is_cloud_disk(self):
        disk = VolMgrMockDisk(
            item_id="disk1", name="hd0",
            size="1G", uuid="KGB", bootable="true")

        self.assertTrue(VolMgrPlugin._is_cloud_disk(disk))

        disk.uuid = 'something'
        self.assertFalse(VolMgrPlugin._is_cloud_disk(disk))

        disk.uuid = None
        self.assertFalse(VolMgrPlugin._is_cloud_disk(disk))

        disk.uuid = ''
        self.assertFalse(VolMgrPlugin._is_cloud_disk(disk))

    def test_litpcds_9529(self):

        fs = VolMock(item_type_id='file-system',
                     item_id='fs1',
                     size='100G',
                     type='ext4',
                     snap_size='100',
                     snap_external='false')
        fs.applied_properties = {'size': fs.size,
                                 'type': fs.type,
                                 'snap_size': fs.snap_size,
                                 'snap_external': fs.snap_external}

        vg = VolMock(item_type_id='volume-group',
                     item_id='vg1',
                     file_systems=[fs])

        sp = VolMock(item_type_id='storage-profile',
                     item_id='sp1',
                     volume_driver='lvm',
                     volume_groups=[vg])

        node = VolMock(item_type_id='node',
                       item_id='n1',
                       storage_profile=sp)

        cluster = VolMock(item_type_id='vcs-cluster',
                          item_id='c1',
                          storage_profile=[sp])

        snapshot = VolMock(item_type_id='snapshot-base',
                           item_id='ss1')

        storage1 = VolMock(item_type_id = 'storage',
                           item_id = 'storage',
                           storage_profiles=[sp])

        infra = VolMock(item_type_id='infrastructure',
                        item_id='infra1',
                        storage=storage1)

        TestVolMgrPlugin._set_state_applied([snapshot, sp, fs, vg, infra, cluster, node])

        def _mock_context_query(query_item_type, **kwargs):
            if 'snapshot-base' == query_item_type:
                return [snapshot]
            elif 'infrastructure' == query_item_type:
                return [infra]
            elif 'node' == query_item_type:
                return [node]
            elif 'vcs-cluster' == query_item_type:
                return [cluster]
            else:
                return []

        api_context = Mock(query=_mock_context_query,
                           snapshot_name=lambda: "test")

        for action in ['create', 'restore', 'remove']:
            api_context.snapshot_action = lambda: action
            errors = self.plugin.validate_model_snapshot(api_context)
            self.assertEquals([], errors)

        # ----
        fs.snap_size = '50'
        fs.applied_properties['snap_size'] = '40'
        errors = self.plugin.validate_model_snapshot(api_context)
        self.assertEquals([], errors)

    def test_litpcds_9496(self):

        fs = VolMock(item_type_id='file-system',
                     item_id='fs1',
                     size='100G')
        fs.applied_properties = {'size': '50G'}

        vg = VolMock(item_type_id='volume-group',
                     item_id='vg1',
                     file_systems=[fs])

        sp = VolMock(item_type_id='storage-profile',
                     item_id='sp1',
                     volume_driver='lvm',
                     volume_groups=[vg])

        node = VolMock(item_type_id='node',
                       item_id='n1',
                       storage_profile=sp)

        cluster = VolMock(item_type_id='vcs-cluster',
                          item_id='c1',
                          storage_profile=[sp])

        TestVolMgrPlugin._set_state_applied([sp, vg, cluster, node])
        TestVolMgrPlugin._set_state_updated([fs])

        def _mock_context_query(query_item_type, **kwargs):
            if 'node' == query_item_type:
                return [node]
            elif 'vcs-cluster' == query_item_type:
                return [cluster]
            else:
                return []

        api_context = Mock(query=_mock_context_query,
                           snapshot_name=lambda: "snapshot_name",
                           snapshot_action = lambda: 'create')

        expected = ValidationError(item_path=fs.get_vpath(),
                error_message= 'A snapshot may not be created while' \
                ' a file system size update is pending.')

        # Two errors, one for node, one for cluster
        errors = self.plugin.validate_model_snapshot(api_context)
        self.assertEquals([expected, expected], errors)

    def _create_node_for_litpcds_9500(self, idx):

        fs = VolMgrMockFS(
            item_id='fs' + idx,
            size='100G',
            snap_size='10',
            snap_external='false'
        )

        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')

        vg = VolMgrMockVG(item_id='vg' + idx, volume_group_name='vg_root')

        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)

        vg.query = VolMgrMock.mock_query({
            'file-system': [fs],
            'physical-device': [pd]
        })

        sp = VolMgrMockStorageProfile(item_id='sp' + idx, volume_driver='lvm')
        sp.volume_groups.append(vg)

        node = VolMgrMockNode(item_id='n' + idx, hostname='sc' + idx)
        node.storage_profile = sp
        system = VolMgrMockSystem('sys' + idx)
        node.system = system

        disk = VolMgrMockDisk(
            item_id='disk0',
            name='hd0',
            size='10G',
            uuid='abcdef-xxxxxxx',
            bootable='true'
        )

        node.system.disks.append(disk)

        node.system.query = VolMgrMock.mock_query({
            'disk': [disk],
            'disk-base': [disk]
        })

        vg.get_node = lambda: node

        VolMgrMock._set_state_applied([fs, pd, vg, sp, node, disk, system])

        return fs, pd, vg, sp, node, disk, system

    def test_litpcds_9500(self):

        root = Mock(item_type_id='root')

        snapshot = VolMock(item_type_id='snapshot-base',
                            item_id='ss1')

        fs1, pd1, vg1, sp1, node1, disk1, sys1 = \
            self._create_node_for_litpcds_9500('1')

        fs2, pd2, vg2, sp2, node2, disk2, sys2 = \
            self._create_node_for_litpcds_9500('2')

        nodes = [node1, node2]
        vgs = [vg1, vg2]

        c1 = VolMgrMockCluster(item_id='c1')
        c1.nodes = nodes

        c1.query = VolMgrMock.mock_query({
            'node': nodes,
            'volume-group': vgs
        })

        clusters = [c1]

        d1 = VolMgrMockDeployment(item_id='d1')
        d1.query =VolMgrMock.mock_query({'cluster': clusters})
        d1.ordered_clusters = clusters

        deployments = [d1]

        VolMgrMock._set_state_applied([c1, d1])

        context = VolMgrMockContext()

        context.query = VolMgrMock.mock_query({
            'cluster': clusters,
            'deployment': deployments,
            'root': [root],
            'snapshot-base': [snapshot],
            'node': nodes,
        })

        context.snapshot_action = lambda: 'restore'
        context.snapshot_model = lambda: context
        context.snapshot_name = lambda: 'snapshot'
        context.is_snapshot_action_forced = lambda: False

        def _mock_snapshotted_vg_lv_in_mn(node, context):

            return [('vg_root', 'lv_test1', 'owi-aos--'),
                    ('vg_root', 'lv_test2', 'owi-aos--'),
                    ('vg_root', 'L_vg1_fs1_', 'owi-aos--'),
                    ('vg_root', 'L_vg2_fs2_', 'owi-aos--')]

        self.plugin.lvm_driver._snapshotted_vg_lv_in_node = \
            _mock_snapshotted_vg_lv_in_mn


        # 1 task = check-all-nodes-reachable and all-snaps-present      => 1
        # 1 task = check-all-snaps valid                                => 1
        # 1 task = Restart all nodes                                    => 1
        # Per Node: 3 tasks = Restore FS, Restore Grub, Wait-for-node   => 6

        tasks = self.plugin.create_snapshot_plan(context)

        self.assertEquals(9, len(tasks), tasks)

        candidate_task1 = CallbackTask(node1.system,
                                       'Wait for node "%s" to restart' % node1.hostname,
                                       self.plugin._wait_for_node_up,
                                       [node1.hostname])

        candidate_task2 = CallbackTask(node2.system,
                                       'Wait for node "%s" to restart' % node2.hostname,
                                       self.plugin._wait_for_node_up,
                                       [node2.hostname])

        # ----
        fs1.current_snap_size = '0'
        TestVolMgrPlugin._set_state_updated([fs1])

        tasks = self.plugin.create_snapshot_plan(context)

        # 1 task = check-all-nodes-reachable and all-snaps-present       => 1
        # 1 task = check-all-snaps valid                                 => 1
        # 1 task = Restart all nodes                                     => 1
        # For Node1: 1 tasks = Wait-for-node                             => 1
        # For Node2: 3 tasks = Restore FS, Restore Grub, Wait-for-node   => 3
        self.assertEquals(7, len(tasks))

        self.assertTrue(candidate_task1 in tasks)
        self.assertTrue(candidate_task2 in tasks)

        fs1.current_snap_size = fs1.applied_properties['snap_size']
        TestVolMgrPlugin._set_state_updated([fs1])

        # ----
        fs2.snap_external = 'true'
        TestVolMgrPlugin._set_state_updated([fs2])

        tasks = self.plugin.create_snapshot_plan(context)

        # 1 task = check-all-nodes-reachable and all-snaps-present       => 1
        # 1 task = check-all-snaps valid                                 => 1
        # 1 task = Restart all nodes                                     => 1
        # For Node1: 3 tasks = Restore FS, Restore Grub, Wait-for-node   => 3
        # For Node2: 1 tasks = Wait-for-node                             => 1
        self.assertEquals(7, len(tasks))

        self.assertTrue(candidate_task1 in tasks)
        self.assertTrue(candidate_task2 in tasks)

        fs2.snap_external = fs2.applied_properties['snap_external']
        TestVolMgrPlugin._set_state_updated([fs2])


    def _create_node_for_litpcds_12577(self, index):

        fs = VolMock(item_type_id='file-system',
                     item_id='fs' + index,
                     size='100G',
                     snap_size='10',
                     snap_external='false',
                     type='ext4')
        fs.applied_properties = {'size': fs.size,
                                 'snap_size': fs.snap_size,
                                 'snap_external': fs.snap_external,
                                 'type': fs.type}

        pd = VolMgrMockPD(item_id='pd1',
                          device_name='hd0')

        vg = VolMgrMockVG(item_id='vg' + index,
                          volume_group_name='vg_root')
        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)

        def _mock_vg_query(args, **kwargs):
            if args == 'file-system':
                return [fs]
            elif args == 'physical-device':
                return [pd]

        vg.query = _mock_vg_query

        sp = VolMgrMockStorageProfile(item_id='sp' + index,
                                      volume_driver='lvm')
        sp.volume_groups.append(vg)

        node = VolMgrMockNode(item_id='n' + index,
                              hostname='sc' + index)
        node.storage_profile = sp
        node.system = VolMgrMockSystem('sys' + index)

        disk = VolMgrMockDisk('d1', 'hd0', '10G', 'd1', True)
        node.system.disks.append(disk)

        node.system.query = lambda args, **kargs: [disk]

        vg.get_node = lambda: node

        TestVolMgrPlugin._set_state_applied([fs, pd, vg, sp, node])

        return (fs, node, vg)

    def test_litpcds_12577(self):
        # regression test
        root = Mock(item_type_id='root')

        snapshot1 = VolMock(item_type_id='snapshot-base',
                            item_id='ss1')

        (fs1, node1, vg1) = self._create_node_for_litpcds_12577('1')
        (fs2, node2, vg2) = self._create_node_for_litpcds_12577('2')

        nodes = [node1, node2]

        def _mock_cluster_query(args, **kwargs):
            if args == 'node':
                return nodes
            elif args == 'volume-group':
                return [vg1, vg2]

        cluster1 = VolMgrMockCluster(item_id='c1')
        cluster1.nodes = nodes
        cluster1.query = _mock_cluster_query

        deployment1 = Mock(item_type_id='deployment')
        deployment1.query = lambda x: [cluster1]
        deployment1.ordered_clusters = [cluster1]

        TestVolMgrPlugin._set_state_applied([
            snapshot1,
            cluster1,
            deployment1])

        def _mock_query(args, **kwargs):
            if args == 'cluster':
                return [cluster1]
            elif args == 'deployment':
                return [deployment1]
            elif args == 'snapshot-base':
                return [snapshot1]
            elif args == 'root':
                return [root]
            elif args == 'node':
                return nodes
            else:
                return []

        context = Mock(query=_mock_query)

        def _mock_snapshotted_vg_lv_in_mn(node, context):
            return [('vg_root', 'lv_test1', 'owi-aos--'),
                    ('vg_root', 'lv_test2', 'owi-aos--'),
                    ('vg_root', 'L_vg1_fs1_', 'owi-aos--'),
                    ('vg_root', 'L_vg2_fs2_', 'owi-aos--')]

        self.plugin.lvm_driver._snapshotted_vg_lv_in_node = _mock_snapshotted_vg_lv_in_mn

        snapshot_model = Mock(query=_mock_query)

        # 1 task = check-all-nodes-reachable and all-snaps-present      => 1
        # 1 task = check-all-snaps valid                                => 1
        # 1 task = Restart all nodes                                    => 1
        # Per Node: 3 tasks = Restore FS, Restore Grub, Wait-for-node   => 6

        context.snapshot_action = MagicMock(return_value='restore')
        context.snapshot_model = MagicMock(return_value=snapshot_model)
        context.snapshot_name = MagicMock(return_value='snapshot')
        context.is_snapshot_action_forced = lambda: False
        model_item = "/snapshots/snapshot"
        fs1.current_snap_size = '10'
        fs2.current_snap_size = '10'

        tasks = self.plugin.create_snapshot_plan(context)

        candidate_task1 = CallbackTask(
            cluster1,
            'Restart node(s) "%s" and "%s"' % (node1.hostname, node2.hostname),
            self.plugin._restart_node,
            [node1.hostname, node2.hostname])

        self.assertTrue(candidate_task1 in tasks)


    @nottest
    def test_litpcds_9475(self):
        fs1 = VolMock(item_type_id='file-system',
                      item_id='fs1',
                      type='vxfs',
                      size='10G',
                      snap_size='0',
                      snap_external='false')

        fs1.applied_properties = {'type': fs1.type,
                                  'size': fs1.size,
                                  'snap_size': fs1.snap_size,
                                  'snap_external': fs1.snap_external}

        pd1 = VolMock(item_type_id='physical-device',
                      item_id='pd1',
                      properties={'device_name': 'sda'})

        vg1 = VolMock(item_type_id='volume-group',
                      item_id='vg1',
                      file_systems=[fs1],
                      volume_group_name='vg1',
                      physical_devices=[pd1])

        sp1 = VolMock(item_type_id='storage-profile',
                      item_id='sp1',
                      volume_groups=[vg1],
                      volume_driver='vxvm')

        node1 = VolMock(item_type_id='node',
                        item_id='n1',
                        storage_profile=None,
                        hostname='sc1')
        d1 = VolMgrMockDisk(item_id='d1',
                            bootable='false',
                            name="sda",
                            size='50G',
                            uuid='uuid2')
        fs1.get_node = MagicMock(return_value=node1)
        fs1.parent = MagicMock()
        fs1.parent.parent = vg1
        node1.system.query = MagicMock(return_value=[d1])
        vg1.query = MagicMock(return_value=[pd1])

        def _mock_cluster_query(args, **kwargs):
            if args== 'storage-profile':
                return [sp1]
            elif args== 'node':
                return [node1]
            else:
                return []

        cluster1 = VolMock(item_type_id='vcs-cluster',
                           item_id='c1',
                           storage_profile=sp1,
                           query=_mock_cluster_query)

        TestVolMgrPlugin._set_state_applied([fs1, vg1, sp1, node1, cluster1])

        def _mock_context_query(args, **kwargs):
            if args== 'ms':
                return []
            elif args == 'node':
                return [node1]
            elif args == 'vcs-cluster':
                return [cluster1]
            else:
                return []

        context = Mock(query=_mock_context_query,
                       snapshot_name=lambda: 'snapshot',
                       snapshot_action=lambda: 'create')

        snap_name = "L_%s_" % fs1.item_id
        self.plugin.vxvm_driver.get_dg_hostname = MagicMock(return_value = {"vg1": (node1, [snap_name])})

        tasks = self.plugin.vxvm_driver.gen_tasks_for_snapshot(context)
        self.assertEquals([], tasks)

        # ----

        fs1.size = str(VolMgrUtils.get_size_megabytes(fs1.size)) + 'M'

        for size in ['10', '40', '70', '100']:

            fs1.snap_size = size
            expected = CallbackTask(
                node1,
                'Create VxVM deployment snapshot "%s" on node "%s", '
                'disk group "%s"' % (
                    snap_name, node1.hostname, vg1.volume_group_name
                ),
                self.plugin.vxvm_driver.rpc_callbacks['base'],
                [node1.hostname],
                'vxvmsnapshot',
                'create_snapshot',
                disk_group=vg1.volume_group_name,
                volume_name=fs1.item_id,
                snapshot_name=snap_name,
                volume_size=VolMgrUtils.compute_snapshot_size(fs1, 'vxvm')
            )

            tasks = self.plugin.vxvm_driver.gen_tasks_for_snapshot(context)
            self.assertEquals([expected], tasks)

    @nottest
    def test_vxvm_snapshots_integrity(self):

        # LITPCSD-5960

        context, cluster, storage_profile, node1 = self._setup_mocks()
        self.plugin.vxvm_driver._vg_metadata = MagicMock(return_value={'test_dg1': (node1,{'snaps':['L_fs1_', 'L_fs2_']}),
                                                                          })

        self.plugin.vxvm_driver._check_snapshot_is_valid = MagicMock(return_value= False)

        try:
            tasks = self.plugin.vxvm_driver._gen_restore_snapshot_tasks(context, cluster, storage_profile)
        except PluginError as e:
            result = str(e)

        self.assertEqual([], tasks)

    def test_rpc_snapshot_integrity(self):
        hostname = 'mn1'
        rpc_result_ok = {hostname: {'errors': '',
                                    'data': {'status': 0,
                                             'err': '',
                                             'out': 'test_output'}}}

        context = MagicMock(rpc_command=MagicMock(return_value=rpc_result_ok))
        snap_name = VolMgrUtils.gen_snapshot_name('test_fs')

        snapshot_valid = self.plugin.vxvm_driver._check_snapshot_is_valid(context,
                                                                          hostname,
                                                                          'test_dg',
                                                                          snap_name)
        self.assertTrue(snapshot_valid)

    def test_rpc_snapshot_integrity_fail(self):
        hostname = 'mn1'

        rpc_result_error = {hostname: {'errors': 'Snapshot is invalid',
                                       'data': {'status': 1,
                                                'err': '',
                                                'out': 'test_output'
                                               }
                                      }
                           }

        context = MagicMock(rpc_command=MagicMock(return_value=rpc_result_error))
        snap_name = VolMgrUtils.gen_snapshot_name('test_fs')

        self.assertRaises(PluginError,
                self.plugin.vxvm_driver._check_snapshot_is_valid,
                                        context,
                                        hostname,
                                        'test_dg',
                                        snap_name)

    def test_litpcds_9564_tc1(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           mount_point='/unique')

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='hd0')

        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='app-vg')

        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        sp1.volume_groups.append(vg1)

        ms1 = VolMgrMockMS()
        ms1.storage_profile = sp1

        sys1 = VolMgrMockSystem(item_id='s1')
        ms1.system = sys1

        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='false',
                               name=pd1.device_name,
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        sys1.disks.append(disk1)

        VolMgrMock._set_state_applied([fs1, vg1, pd1, sp1, ms1, sys1, disk1])

        def _mock_context_query(args, **kwargs):
            if args== 'ms':
                return [ms1]
            else:
                return []

        context = Mock(query=_mock_context_query)

        def _mock_rpc_vg_data(context, node):
            return {'uuid': 'ATA_VBOX_HARDDISK_VB24150799-28e77304'}

        self.plugin.lvm_driver._get_vg_data_by_rpc = _mock_rpc_vg_data

        errors = self.plugin.validate_model(context)
        self.assertEquals([], errors)

        # ----

        fs1.mount_point = '/var'
        msg = 'mount_point "/var" conflicts with MS Kickstarted LVM file-system. ' + \
              'Reserved MS Kickstarted LVM mount points: /, /home, swap, /var, /var/tmp, /var/opt/rh, /var/lib/puppetdb, /var/log, /var/www, /software'
        expected = ValidationError(item_path=fs1.get_vpath(),
                                   error_message=msg)
        errors = self.plugin.validate_model(context)

        self.assertEquals([expected], errors, errors)
        fs1.mount_point = '/unique'

    def _create_mock_kickstart_fs(self):
        return [{'attrs': 'owi-aos---',
                 'lv_name': 'lv_home',
                  'path': '/dev/vg_root/lv_home',
                  'size': '10G',
                  'vg_name': 'vg_root',
                  'mount': '/home'},
                  {'attrs': 'owi-aos---',
                  'lv_name': 'lv_var',
                  'path': '/dev/vg_root/lv_var',
                  'size': '20G',
                  'vg_name': 'vg_root',
                  'mount': '/var'}]

    def test_litpcds_9564_tc2(self):

        fs_orig_size = '10G'
        fs_orig_snap_size = '20'
        fs_prop_names = ['mount_point', 'size', 'type', 'snap_size']

        fs1 = VolMgrMockFS(item_id='fs1',
                           size=fs_orig_size,
                           snap_size=fs_orig_snap_size,
                           mount_point='/jump')

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='hd0')

        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='vg_root')
        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        sp1.volume_groups.append(vg1)
        sp1.view_root_vg = vg1.volume_group_name

        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='true',
                               name=pd1.device_name,
                               size='940G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)

        ms1 = VolMgrMockMS()
        ms1.storage_profile = sp1
        ms1.system = sys1

        all_items = [fs1, vg1, pd1, sp1, sys1, disk1, ms1]

        VolMgrMock._set_state_initial(all_items)

        def _mock_context_query(args, **kwargs):
            if args== 'ms':
                return [ms1]
            else:
                return []

        context = Mock(query=_mock_context_query)

        def _mock_rpc_vg_data(context, node):
            return {'uuid': disk1.uuid.lower().replace('-', '_')}

        self.plugin.lvm_driver._get_vg_data_by_rpc = _mock_rpc_vg_data
        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            with patch.object(self.plugin.lvm_driver, '_get_lv_metadata') as lv_mock:
                lv_mock.return_value = mock_fs.return_value
                errors = self.plugin.validate_model(context)
                self.assertEquals([], errors)

        # ----

        # A vg_root group must be on a bootable disk
        disk1.bootable = 'false'
        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors = self.plugin.validate_model(context)
        msg = ("The system disk modeled for '%s' volume-group on MS '%s' " + \
               "must have 'bootable' set to 'true'") % \
              (vg1.volume_group_name, ms1.get_vpath())
        expected = ValidationError(error_message=msg,
                                   item_path=disk1.get_vpath())
        self.assertTrue(expected in errors)
        disk1.bootable = 'true'

        # ----
        # A-n-other group on MS must not be on the install disk
        vg1.volume_group_name = 'something-different'
        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors = self.plugin.validate_model(context)
        msg = ('Non "vg_root" volume-group "%s" may not use the ' + \
               'Kickstarted "vg_root" disk "%s"') % \
               (vg1.volume_group_name, disk1.uuid)
        expected = ValidationError(error_message=msg,
                                   item_path=vg1.get_vpath())
        self.assertTrue(expected in errors)
        vg1.volume_group_name = 'vg_root'

        # ----
        # The modeled vg_root disk UUID doesn't match the actual install disk UUID
        install_uuid = 'the-real-kickstarted-install-uuid'
        self.plugin.lvm_driver._get_vg_data_by_rpc = lambda x,y: {'uuid': install_uuid}
        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors = self.plugin.validate_model(context)
        msg = ("The system disk 'ata_vbox_harddisk_vb24150799_28e77304' " + \
               "modeled for 'vg_root' volume-group on MS " + \
               "'%s' is invalid; it is not the real Kickstarted " + \
               "device, which has a UUID of '%s'") % \
               (ms1.get_vpath(), install_uuid)
        expected = ValidationError(error_message=msg,
                                   item_path=vg1.get_vpath())
        self.assertEquals([expected], errors)
        self.plugin.lvm_driver._get_vg_data_by_rpc = _mock_rpc_vg_data

        # ----

        fs1.size = '100G'

        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors1 = self.plugin.validate_model(context)

        # With everything "Initial" only Standard plan should raise a sizing error
        msg = "The System Disks (962560 MB) on node 'ms1' are not " + \
              "large enough for volume group requirement (1065560 MB). " + \
              "Volume group requirement = ((file systems including snapshots) " + \
              "1064960 MB) + (LVM metadata 600 MB.)"
        expected1 = ValidationError(error_message=msg,
                                    item_path=vg1.get_vpath())
        self.assertEquals([expected1], errors1)

        context.snapshot_action = lambda: 'create'
        errors2 = self.plugin.validate_model_snapshot(context)
        self.assertEquals([], errors2)

        fs1.size = fs_orig_size

        # "Apply" a size of 40G ...
        fs1.size = '40G'
        VolMgrMock._set_state_applied(all_items)

        # Then try to update to a 100% snap-size
        fs1.snap_size = '100'
        VolMgrMock._set_state_updated([fs1])

        # Standard Plan shouldn't care
        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors1 = self.plugin.validate_model(context)
        self.assertEquals([], errors1)

        # But create-snapshot Plan should now throw a sizing error
        msg = "The System Disks (962560 MB) on node 'ms1' are not " + \
              "large enough for volume group requirement (1024600 MB). " + \
              "Volume group requirement = ((file systems including snapshots) " + \
              "1024000 MB) + (LVM metadata 600 MB.)"
        expected2 = ValidationError(error_message=msg,
                                    item_path=vg1.get_vpath())
        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors2 = self.plugin.validate_model_snapshot(context)
        self.assertEquals([expected2], errors2)

        fs1.snap_size = fs_orig_snap_size

    def test_litpcds_10101(self):
        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        ms1 = VolMgrMockMS()
        ms1.storage_profile = sp1

        fs1 = VolMgrMockFS(item_id='fs1',
                           size='10G',
                           snap_size='50',
                           mount_point='/jump')

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='hd0')

        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='vg_root')
        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)

        sp1.volume_groups.append(vg1)
        sp1.view_root_vg = vg1.volume_group_name

        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='true',
                               name=pd1.device_name,
                               size='1200G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)

        ms1.system = sys1

        infra1 = Mock(storage=Mock(storage_profiles=[sp1]))

        def _mock_context_query(args, **kwargs):
            if args== 'ms':
                return [ms1]
            elif args== 'infrastructure':
                return [infra1]
            else:
                return []

        all_items = [fs1, vg1, pd1, sp1, sys1, disk1, ms1]

        VolMgrMock._set_state_initial(all_items)

        context = Mock(query=_mock_context_query)

        def _mock_rpc_vg_data(context, node):
            return {'uuid': disk1.uuid.lower().replace('-', '_')}

        self.plugin.lvm_driver._get_vg_data_by_rpc = _mock_rpc_vg_data

        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors = self.plugin.validate_model(context)

        self.assertEquals([], errors)

        # ----
        sp1.volume_driver = 'vxvm'

        with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
            mock_fs.refurn_value = self._create_mock_kickstart_fs()
            errors = self.plugin.validate_model(context)

        msg1 = 'The "volume_driver" property on an MS storage-profile must have a value of "lvm"'
        expected1 = ValidationError(item_path=sp1.get_vpath(),
                                    error_message=msg1)

        msg2 = 'The "volume_driver" property of the storage-profile has a value "vxvm"; ' + \
               'the "type" property on all file-systems must have a value of "vxfs".'
        expected2 = ValidationError(item_path=fs1.get_vpath(),
                                    error_message=msg2)
        self.assertEquals(set([expected1, expected2]), set(errors))

    def test_litpcds_10103(self):

        ms1 = VolMgrMockMS()

        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='true',
                               name='hd0',
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)

        ms1.system = sys1

        all_items = [ms1, disk1, sys1]
        VolMgrMock._set_state_initial(all_items)

        errors = self.plugin._validate_bootable_disk(ms1)

        self.assertEquals([], errors)

        disk2 = VolMgrMockDisk(item_id='d2',
                               bootable='false',
                               name='hd1',
                               size='50M',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77404')
        sys1.disks.append(disk2)

        VolMgrMock._set_state_initial([disk2])

        errors = self.plugin._validate_bootable_disk(ms1)
        self.assertEquals([], errors)

        disk2.bootable = 'true'
        msg = "At most one system disk may have the 'bootable' property set to 'true'"
        expected = ValidationError(error_message=msg,
                                   item_path=sys1.get_vpath())
        errors = self.plugin._validate_bootable_disk(ms1)
        self.assertEquals([expected], errors)

    def test_litpcds_10191(self):
        ms1 = VolMgrMockMS()

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        sys1 = VolMgrMockSystem(item_id='s1')

        all_items = [ms1, sp1, sys1]
        VolMgrMock._set_state_initial(all_items)

        # ----
        # Neither system nor storage-profile inherited to the MS
        errors = self.plugin._validate_storage_profile_and_system_pair(ms1)
        self.assertEquals([], errors)

        # ----
        # Just a system inherited to the MS
        ms1.system = sys1
        errors = self.plugin._validate_storage_profile_and_system_pair(ms1)
        self.assertEquals([], errors)

        # ----
        # Both system and storage-profile inherited to the MS
        ms1.storage_profile = sp1
        errors = self.plugin._validate_storage_profile_and_system_pair(ms1)
        self.assertEquals([], errors)

        # ----
        # Just a storage-profile inherited to the MS  => Error
        ms1.system = None
        errors = self.plugin._validate_storage_profile_and_system_pair(ms1)

        msg = 'A "storage-profile" is inherited to the MS; ' + \
              'a "system" must also be inherited to the MS.'
        expected = ValidationError(item_path=ms1.get_vpath(),
                                   error_message=msg)
        self.assertEquals([expected], errors)

    def test_litpcds_4331_validate(self):
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='hd0')
        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='hd1')

        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='vg_root')

        vg1.physical_devices.append(pd1)
        vg1.physical_devices.append(pd2)

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='vxvm')

        VolMgrMock._set_state_applied([sp1])
        sp1.volume_groups.append(vg1)

        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='true',
                               name="hd0",
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        disk2 = VolMgrMockDisk(item_id='d2',
                               bootable='false',
                               name="hd1",
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77305')

        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)
        sys1.disks.append(disk2)
        sys2 = VolMgrMockSystem(item_id='s2')
        sys2.disks.append(disk1)

        node1 = VolMgrMockNode(item_id="n1",
                               hostname="mn1")
        node2 = VolMgrMockNode(item_id="n2",
                               hostname="mn2")
        node1.system = sys1
        node2.system = sys2

        VolMgrMock._set_state_applied([node1, node2])

        clus = VolMgrMockCluster(item_id="c1",
                                 cluster_type="sfha")
        clus.storage_profile = [sp1]
        clus.nodes = [node1, node2]

        def _mock_cluster_query(args, **kwargs):
            if args == "storage-profile":
                return clus.storage_profile
            else:
                return []

        clus.query = _mock_cluster_query
        all_items = [clus, sp1, vg1, pd1, pd2, sys1, disk1, disk2]
        VolMgrMock._set_state_initial(all_items)

        # Mock context
        context = MagicMock()
        def _mock_context_query(args, **kwargs):
            if args == 'volume-group':
                return [vg1]
            elif args == 'storage-profile':
                return [sp1]
            elif args == 'vcs-cluster':
                return [clus]
            else:
                return []

        context.query = _mock_context_query

        # Validate Model with one disk not shared
        errors = self.plugin.validate_model(context)
        expected = "VxVM disk '{0}' does not exist on node '{1}'"
        expected = expected.format(disk2.name, node2.hostname)
        self.assertEquals(expected, errors[0].error_message)

        # Now test again with both disks shared
        sys2.disks.append(disk2)
        errors = self.plugin.validate_model(context)
        self.assertEquals([], errors)

    def test__validate_fs_is_updatable(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='4G',
                           mount_point='/mount',
                           type='vxfs')
        fs1.applied_properties["mount_point"] = '/new_mount'
        fs1.applied_properties["size"] = '4G'
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        vg1 = VolMgrMockVG(item_id="vg1",
                           volume_group_name="vg_root")

        vg1.physical_devices.append(pd1)
        vg1.file_systems.append(fs1)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg1)
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='33G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1])
        node = VolMgrMockNode(item_id="n1", hostname="mn1")
        node.system = sys
        node.storage_profile = sp
        sp.get_node = lambda: node

        context = VolMgrMockContext()
        context.query = VolMgrMock.mock_query({
            'node': [node],
            'storage-profile': [sp],
            'ms': [],
        })

        VolMgrMock._set_state_applied([pd1, vg1, sp, sys, disk1, node])
        VolMgrMock._set_state_updated([fs1])

        def mock_rpc(*args):
            pass
        with patch.object(self.plugin.lvm_driver, '_get_vg_data_by_rpc',
                          mock_rpc) as m:
            errors = self.plugin.validate_model(context)
            err_msg = ('A "mount_point" cannot be updated for a "file-system" of '
                      '"type" \'vxfs\'')
            expected = ValidationError(item_path=fs1.get_vpath(),
                                       error_message=err_msg)
            self.assertEqual([expected], errors)

    def test__validate_ms_ks_fs_mount_point_not_removed(self):
        fs1 = VolMgrMockFS(item_id='fs1',
                           size='4G',
                           mount_point=None,
                           type='ext4')
        fs1.applied_properties["mount_point"] = '/home'
        fs1.applied_properties["size"] = fs1.size

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='disk1')
        vg1 = VolMgrMockVG(item_id="vg1",
                           volume_group_name="vg_root")

        vg1.physical_devices.append(pd1)
        vg1.file_systems.append(fs1)
        sp = VolMgrMockStorageProfile(item_id="sp1",
                                      volume_driver="lvm")
        sp.volume_groups.append(vg1)
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='disk1',
                              size='1200G',
                              uuid='ABCD_1234')
        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.extend([disk1])
        ms = VolMgrMockMS()
        ms.system = sys
        ms.storage_profile = sp
        sp.get_node = lambda: ms

        context = VolMgrMockContext()
        context.query = VolMgrMock.mock_query({
            'node': [],
            'storage-profile': [sp],
            'ms': [ms],
            'volume-group': [vg1],
            'file-system': [fs1]
        })

        VolMgrMock._set_state_applied([pd1, vg1, sp, sys, disk1, ms])
        VolMgrMock._set_state_updated([fs1])

        def mock_rpc(*args):
            pass
        with patch.object(self.plugin.lvm_driver,'_get_vg_data_by_rpc',
                          mock_rpc) as m:
            with patch.object(self.plugin.lvm_driver, '_get_scanned_file_systems') as mock_fs:
                mock_fs.refurn_value = self._create_mock_kickstart_fs()
                errors = self.plugin.validate_model(context)
                err_msg = 'A "mount_point" cannot be updated for a Kickstarted "file-system"'
                expected = ValidationError(item_path=fs1.get_vpath(),
                                           error_message=err_msg)
                self.assertEqual([expected], errors)

    def test_litpcds_4331_create_configuration(self):
        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name='hd0')
        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name='hd1')

        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='vg_root')

        vg1.physical_devices.append(pd1)
        vg1.physical_devices.append(pd2)

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='vxvm')
        sp1.volume_groups.append(vg1)

        disk1 = VolMgrMockDisk(item_id='d1',
                               bootable='true',
                               name="hd0",
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        disk2 = VolMgrMockDisk(item_id='d2',
                               bootable='false',
                               name="hd1",
                               size='50G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77305')

        sys1 = VolMgrMockSystem(item_id='s1')
        sys1.disks.append(disk1)
        sys1.disks.append(disk2)
        sys2 = VolMgrMockSystem(item_id='s2')
        sys2.disks.append(disk1)
        sys2.disks.append(disk2)

        node1 = VolMgrMockNode(item_id="n1",
                               hostname="mn1")
        node2 = VolMgrMockNode(item_id="n2",
                               hostname="mn2")
        node1.system = sys1
        node2.system = sys2

        clus = VolMgrMockCluster(item_id="c1",
                                 cluster_type="sfha")
        clus.storage_profile = [sp1]
        clus.nodes = [node1, node2]

        def _mock_cluster_query(args, **kwargs):
            if args == "storage-profile":
                return clus.storage_profile
            else:
                return []

        clus.query = _mock_cluster_query
        clus.fencing_disks = MagicMock()
        all_items = [clus, sp1, vg1, pd1, pd2, sys1, disk1, disk2]
        VolMgrMock._set_state_initial(all_items)

        # Mock context
        context = MagicMock()
        def _mock_context_query(args, **kwargs):
            if args == 'volume-group':
                return [vg1]
            elif args == 'storage-profile':
                return [sp1]
            elif args == 'vcs-cluster':
                return [clus]
            else:
                return []

        context.query = _mock_context_query

        expected = ['Clear SCSI-3 registration keys from disk "hd0" on node "mn1"',
                    'Setup VxVM disk "hd0" on node "mn1"',
                    'Clear SCSI-3 registration keys from disk "hd1" on node "mn1"',
                    'Setup VxVM disk "hd1" on node "mn1"',
                    'Create VxVM disk group "vg_root" on node "mn1"' ]

        tasks = self.plugin.create_configuration(context)[0].task_list
        self.assertTrue(all([task.description in expected
                              for task in tasks]))

    def test_timeout(self):
        test_time = 1
        timeout = Timeout(test_time)
        self.assertFalse(timeout.has_elapsed())
        self.assertTrue(0 < timeout.get_elapsed_time())
        self.assertTrue(0 < timeout.get_remaining_time())
        timeout.sleep(test_time)
        self.assertTrue(timeout.has_elapsed())

    def test_filter_reachable_nodes(self):
        nodes = ["sc-1", "mn2", "mn3"]
        cb_api = MagicMock()
        with patch.object(self.plugin, '_is_reachable_node') as mock1:
            mock1.return_value = True
            nodes_up, nodes_down = self.plugin._filter_reachable_nodes(cb_api, nodes)
            self.assertEqual(nodes, nodes_up)

    def test_ping_and_remove_snapshot_cb(self):
        cb_api = MagicMock()
        with patch.object(self.plugin, '_is_reachable_node') as mock1:
            mock1.return_value = False
            self.assertTrue(None ==
                self.plugin._ping_and_remove_snapshot_cb(cb_api, 'hostname', timeout=200))

    def test_wait_for_nodes_up(self):
        cb_api = MagicMock()
        nodes = ["sc-1", "sc-2", "sc-3"]

        def snap_query(args):
            if args == 'snapshot-base':
                return [snapshot]

        with patch.object(self.plugin, '_is_reachable_node') as mock1:
            mock1.return_value = True
            cluster = MockCluster("test_c1", "sfha")
            cluster.vpath = '/a/b/c'
            snapshot = VolMock(item_type_id='snapshot-base',
                           item_id='ss1')
            cb_api.query = snap_query

            with patch.object(coreutils, '_wait_for_callback') as mock2:
                mock2 = time.sleep(1)
                self.assertTrue(None ==
                    self.plugin._wait_for_nodes_up(cb_api, nodes))
                with patch.object(VolMgrPlugin, '_set_reboot_time') as mock_set_reboot_time:
                    self.plugin._wait_for_nodes_up(cb_api, nodes)
                    self.assertEqual(mock_set_reboot_time.call_count, 1)

    def test_check_snapshots_lvs_expired(self):
        def mock_process(cb_api, hosts, agent, action, timeout, retries, **kwargs):
            return [{'ms1': '/dev/new_vg/L_vg3_fs3_ 109051904B swi-a-s--- /foo\n  '
                            '/dev/new_vg/vg4_fs4 2147483648B owi-aos--- /bar\n  '
                            '/dev/vg_root/lv_home 42823843840B owi-aos--- /home\n  '
                            '/dev/vg_root/lv_root 53687091200B owi-aos--- /\n  '
                            '/dev/vg_root/lv_swap 42823843840B -wi-ao---- [SWAP]\n  '
                            '/dev/vg_root/lv_var 53687091200B owi-aos--- /var'},
                    {'mn1': ['mn1: execution expired'],
                     'mn2': ['mn2: execution expired']}]

        expected_snapshots = {'mn1': {'snapshots': ['L_vg3_fs3_', 'L_vg4_fs4_']},
                              'mn2': {'snapshots': ['L_vg3_fs3_', 'L_vg4_fs4_']}}

        with patch.object(RpcCommandOutputNoStderrProcessor,'execute_rpc_and_process_result',
                          MagicMock(side_effect=mock_process)):
            cb_api = MagicMock()
            self.assertRaises(CallbackExecutionException, self.plugin._check_snapshots_exist, cb_api, ['mn1', 'mn2'],
                'lv', 'lvs', expected_snapshots=expected_snapshots)

    def test_check_snapshots_exist(self):
        mock_process_result = {'ms1': '/dev/new_vg/vg4_fs4 2147483648B owi-aos--- /foo\n '
                            '/dev/vg_root/lv_home 42823843840B owi-aos--- /home\n '
                            '/dev/vg_root/lv_root 53687091200B owi-aos--- /\n  '
                            '/dev/vg_root/lv_swap 42823843840B -wi-ao---- [SWAP]\n '
                            '/dev/vg_root/lv_var 53687091200B owi-aos--- /var',
                     'mn1': '/dev/new_vg/L_vg4_fs4_ 109051904B swi-a-s--- [NOT-MOUNTED]\n  '
                            '/dev/new_vg/vg3_fs3 1073741824B -wi-ao---- /foo\n '
                            '/dev/new_vg/vg4_fs4 2147483648B owi-aos--- /bar\n '
                            '/dev/vg_root/lv_home 42823843840B owi-aos--- /home\n  '
                            '/dev/vg_root/lv_root 53687091200B owi-aos--- /root\n  '
                            '/dev/vg_root/lv_swap 42823843840B -wi-ao---- [SWAP]\n  '
                            '/dev/vg_root/lv_var 53687091200B owi-aos--- /var'}

        def mock_process(cb_api, hosts, agent, action, timeout, retries, **kwargs):
            return [mock_process_result, {}]

        expected_snapshots = {'mn1': {'snapshots': ['L_vg3_fs3_', 'L_vg4_fs4_']},
                              'mn2': {'snapshots': ['L_vg3_fs3_', 'L_vg4_fs4_']}}

        with patch.object(RpcCommandOutputNoStderrProcessor,
                          'execute_rpc_and_process_result',
                          MagicMock(side_effect=mock_process)):

            cb_api = MagicMock()
            msgs = self.plugin._check_snapshots_exist(cb_api, ['mn1', 'mn2'],
                 'lv', 'lvs', expected_snapshots=expected_snapshots)
            expected_messages = ['Snapshot L_vg3_fs3_ is missing on node mn1',
                                 'Snapshot L_vg3_fs3_ is missing on node mn2',
                                 'Snapshot L_vg4_fs4_ is missing on node mn2']
            self.assertEquals(expected_messages, msgs)

    def test__current_snap_size_view_behaviour(self):
        self.setup_model()
        rsp = self.model_manager.create_item('volume-group',
                                             '/infrastructure/storage/storage_profiles/sp1/volume_groups/vg1',
                                             volume_group_name="vg_root")
        self.assertFalse(isinstance(rsp, list), rsp)
        vg1 = rsp

        rsp = self.model_manager.create_item('file-system',
                                             '/infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/file_systems/fs1',
                                             mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        n1_url = '/deployments/d1/clusters/c1/nodes/n1'
        rsp = self.model_manager.remove_item(
            n1_url + '/storage_profile')
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.create_inherited(
            '/infrastructure/storage/storage_profiles/sp1',
            n1_url + '/storage_profile')
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        self.context.snapshot_action = MagicMock(return_value='create')
        self.context.snapshot_name = MagicMock(return_value='snapshot')
        self.context.snapshot_model = MagicMock(return_value=self.context)
        self.plugin.lvm_driver.gen_tasks_for_ms_kickstart_snapshot = MagicMock(return_value=[])
        self.plugin.lvm_driver.gen_tasks_for_ms_non_modeled_ks_snapshots = MagicMock(return_value=[])

        # 1
        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')
        ss_task = "[<CallbackTask /deployments/d1/clusters/c1/nodes/n1/system - _create_snapshot_cb: ('node1', 'snapshot', 'create') [Initial]>, <CallbackTask /deployments/d1/clusters/c1/nodes/n1/system - _base_rpc_task: (['node1'], 'snapshot', 'save_grub') [Initial]>]"
        self.assertEquals(ss_task, str(tasks))

        # 2
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')
        self.assertEquals(0, len(tasks))

        # 3
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size="100", mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.remove_snapshot_item('snapshot')
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.create_item('snapshot-base',
                                             '/snapshots/test3')
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')
        self.assertEquals(2, len(tasks))

        # 4
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size=None, mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')
        self.assertEquals(0, len(tasks))

        # 5
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="100", backup_snap_size="0", mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')
        self.assertEquals(0, len(tasks))

        # 6
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size="100", mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.remove_snapshot_item('test3')
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.create_item('snapshot-base',
                                             '/snapshots/snapshot')
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'create')
        self.assertEquals(0, len(tasks))

        # 7
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="100", backup_snap_size=None, mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        self.context.snapshot_action = MagicMock(return_value='remove')
        tasks = self.plugin._get_snapshot_tasks(self.context, 'remove')
        self.assertEquals(4, len(tasks))

        # 8
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size=None, mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'remove')
        self.assertEquals(1, len(tasks))

        # 9
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size="100", mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.remove_snapshot_item('snapshot')
        self.assertFalse(isinstance(rsp, list), rsp)

        rsp = self.model_manager.create_item('snapshot-base',
                                             '/snapshots/test')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'remove')
        self.assertEquals(4, len(tasks))

        # 10
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size="0", mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'remove')
        self.assertEquals(1, len(tasks))

        # 11
        rsp = self.model_manager.update_item(
                                             n1_url+'/storage_profile/volume_groups/vg1/file_systems/fs1',
                                             snap_size="0", backup_snap_size=None, mount_point="/root",type="ext4",snap_external="false", size="8G")
        self.assertFalse(isinstance(rsp, list), rsp)

        self.model_manager.set_all_applied()
        tasks = self.plugin._get_snapshot_tasks(self.context, 'remove')
        self.assertEquals(1, len(tasks))


class VolmgrUtilsTestCase(unittest.TestCase):
    def test_01_2_vgs(self):
        node = Mock()
        node.is_ms = MagicMock(return_value=True)
        node.item_type_id = 'node'
        node.item_id = 'ms'
        vg1 = MagicMock()
        vg1.volume_group_name = 'non_root'
        vg1.item_id = 'vg1'
        vg2 = MagicMock()
        MS_VG_ROOT = 'vg_root'
        vg2.volume_group_name = MS_VG_ROOT
        vg2.item_id = 'vg2'
        self.assertNotEqual(
            MS_VG_ROOT,
            VolMgrUtils.get_root_vg_name_from_modeled_vgs(node, vg1))
        self.assertEqual(
            MS_VG_ROOT,
            VolMgrUtils.get_root_vg_name_from_modeled_vgs(node, vg2))
