# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

from collections import defaultdict
import unittest
from mock import MagicMock, Mock, patch, call

from litp.core.plugin_manager import PluginManager
from litp.core.model_manager import ModelManager
from litp.core.plugin_context_api import PluginApiContext
from litp.core.execution_manager import PluginError
from litp.core.future_property_value import FuturePropertyValue
from litp.core.validators import ValidationError
from litp.core.litp_logging import LitpLogger
from litp.extensions.core_extension import CoreExtension
from volmgr_extension.volmgr_extension import VolMgrExtension
from volmgr_plugin.volmgr_plugin import VolMgrPlugin
from volmgr_plugin.volmgr_plugin import VolMgrUtils
from volmgr_plugin.drivers.lvm import LvmDriver

from .mock_vol_items import (
    VolMgrMock,
    VolMgrMockDisk,
    VolMgrMockMS,
    VolMgrMockNode,
    VolMgrMockSystem,
    VolMgrMockCluster,
    VolMgrMockVCSCluster,
    VolMgrMockFS,
    VolMgrMockPD,
    VolMgrMockVG,
    VolMgrMockOS,
    VolMgrMockStorageProfile,
    VolMgrMockContext,
    MockConfigTask
)
from nose.tools import nottest


class TestLvmVolMgrDriver(unittest.TestCase):

    def setUp(self):
        """
        Construct a model manager, sufficient for test cases
        that you wish to implement in this suite.
        """
        self.model_manager = ModelManager()
        self.plugin_manager = PluginManager(self.model_manager)
        self.context = PluginApiContext(self.model_manager)
        self.snapshot = None
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

    # TODO (igor): Too complex! 11 McCabe index.
    def _create_storage_profile_items(self, profile, system, data):

        if profile:
            profile_url = profile.get_vpath()
            for vg in data['VGs']:
                vg_url = profile_url + '/volume_groups/' + vg['id']

                rsp = self.model_manager.create_item(
                    'volume-group', vg_url, volume_group_name=vg['name'])

                self.assertFalse(isinstance(rsp, list), rsp)
                if 'FSs' in vg:
                    for fs in vg['FSs']:
                        fs_url = vg_url + '/file_systems/' + fs['id']

                        snap_size = '0'
                        if 'lvm' == profile.volume_driver and  \
                                fs['type'] in ['ext4', 'xfs']:
                            if 'snap_size' in fs:
                                snap_size = fs['snap_size']
                            else:
                                snap_size = '10'

                        rsp = self.model_manager.create_item(
                            'file-system',
                            fs_url,
                            type=fs['type'],
                            mount_point=fs['mp'],
                            size=fs['size'],
                            snap_size=snap_size,
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
                rsp = self.model_manager.create_item('disk',
                                                     disk_url,
                                                     bootable=disk['bootable'],
                                                     uuid=disk['uuid'],
                                                     name=disk['name'],
                                                     size=disk['size'])
                self.assertFalse(isinstance(rsp, list), rsp)

    def setup_model(self, link_node_to_system=True):

        rsp = self.model_manager.create_item('snapshot-base', '/snapshots/snapshot')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.snapshot = rsp
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

        n2_url = '/deployments/d1/clusters/c1/nodes/n2'
        rsp = self.model_manager.create_item('node', n2_url,
                                             hostname='node2')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.node2 = rsp

        s1_url = '/infrastructure/systems/s1'
        sys_name = 'MN1SYS'
        rsp = self.model_manager.create_item('system',
                                             s1_url,
                                             system_name=sys_name)
        self.assertFalse(isinstance(rsp, list), rsp)
        self.system1 = rsp
        s2_url = '/infrastructure/systems/s2'
        sys_name = 'MN2SYS'
        rsp = self.model_manager.create_item('system',
                                             s2_url,
                                             system_name=sys_name)
        self.assertFalse(isinstance(rsp, list), rsp)
        self.system2 = rsp

        sp1_name = 'storage_profile_1'
        rsp = self.model_manager.create_item('storage-profile',
                                             '/infrastructure/storage/storage_profiles/sp1',
                                             volume_driver='lvm')
        self.assertFalse(isinstance(rsp, list), rsp)
        self.sp1 = rsp

        rsp = self.model_manager.create_inherited(
            '/infrastructure/storage/storage_profiles/sp1',
            n1_url + '/storage_profile')
        self.assertFalse(isinstance(rsp, list), rsp)
        rsp = self.model_manager.create_inherited(
            '/infrastructure/storage/storage_profiles/sp1',
            n2_url + '/storage_profile')
        self.assertFalse(isinstance(rsp, list), rsp)

        if link_node_to_system:
            rsp = self.model_manager.create_inherited(s1_url,
                                                      n1_url + '/system')
            self.assertFalse(isinstance(rsp, list), rsp)
            rsp = self.model_manager.create_inherited(s2_url,
                                                      n2_url + '/system')
            self.assertFalse(isinstance(rsp, list), rsp)
        snap_obj = MagicMock()
        snap_obj.item_id = 'snapshot'
        self.context.snapshot_object = MagicMock(return_value=[snap_obj])

    def _create_dataset_primary_disk(self):

        disk1_name = 'primary'

        storage_data = \
            {'VGs': [{
                'id': 'vg1',
                'name': 'root_vg',
                'FSs': [{'id': 'fs1', 'type': 'ext4', 'mp': '/',     'size': '10G', 'snap_size': '20'},
                        {'id': 'fs2', 'type': 'swap', 'mp': 'swap',  'size': '2G',  'snap_size': '50'},
                        {'id': 'fs3', 'type': 'ext4', 'mp': '/home', 'size': '14G', 'snap_size': '50'},
                        {'id': 'fs4', 'type': 'xfs', 'mp': '/var', 'size': '14G', 'snap_size': '40'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]}],
            'disks': [{'id': 'disk1', 'bootable': 'true', 'uuid': 'ABCD_1234', 'name': disk1_name, 'size': '53G'}]}

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

    def _create_dataset_primary_disk_xfs(self):

        disk1_name = 'primary'

        storage_data = \
            {'VGs': [{
                'id': 'vg1',
                'name': 'root_vg',
                'FSs': [{'id': 'fs1', 'type': 'xfs', 'mp': '/',     'size': '10G', 'snap_size': '20'},
                        {'id': 'fs2', 'type': 'swap', 'mp': 'swap',  'size': '2G',  'snap_size': '50'},
                        {'id': 'fs3', 'type': 'xfs', 'mp': '/home', 'size': '14G', 'snap_size': '50'}],
                'PDs': [{'id': 'pd1', 'device': disk1_name}]}],
            'disks': [{'id': 'disk1', 'bootable': 'true', 'uuid': 'ABCD_1234', 'name': disk1_name, 'size': '53G'}]}

        self._create_storage_profile_items(self.sp1,
                                           self.system1,
                                           storage_data)

    @patch("volmgr_plugin.volmgr_utils.VolMgrUtils.is_the_rootvg")
    def test__physical_device_name(self, is_the_rootvg):
        is_the_rootvg.return_value = True
        node = VolMgrMockNode(item_id='mn1',
                              hostname='mn1')
        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='root')

        disk = VolMgrMockDisk(item_id='d1',
                              uuid='kgb',
                              size='10G',
                              name='sda',
                              bootable='true')
        VolMgrMock._set_state_initial([node, disk, vg])

        self.assertEquals(r'$::disk_sda3',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg, Mock))

        disk.name = 'sdb'
        disk.bootable = 'false'
        self.assertEquals(r'$::disk_sdb',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg, Mock))

        # Tests for disk UUIDs mappings. Morph the disk into a lun-disk.
        disk.item_type_id = 'lun-disk'
        disk.uuid = '123'
        disk.name = 'bar'
        self.assertEquals(FuturePropertyValue,
                          type(self.plugin.lvm_driver._physical_device_name(node, disk, vg, Mock)))
        self.assertEquals('disk_fact_name',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg,Mock).property_name)
        #10993 - any other type of disk with a uuid should be treated like a lun-disk
        disk.item_type_id = 'other-disk'
        disk.uuid = '456'
        disk.name = 'foo'
        self.assertEquals(FuturePropertyValue,
                          type(self.plugin.lvm_driver._physical_device_name(node, disk, vg, Mock)))
        self.assertEquals('disk_fact_name',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg, Mock).property_name)


        #TORF-162871 - increasing Lun-Disk size, used in LVM storage profile
        VolMgrMock._set_state_applied([node, disk, vg])
        disk.item_type_id = 'lun-disk'
        disk.bootable = 'false'
        disk.uuid = '123'
        disk.name = 'bar'
        self.assertEquals(r'$::disk_123_dev',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg, Mock))

    # Test skipped because of transition to FuturePropertyValue
    # Re-enable when ready
    def rest__physical_device_name_in_sfha_cluster(self):

        cluster = VolMgrMockCluster(item_id='c1',
                                    cluster_type='sfha')
        node = VolMgrMockNode(item_id='mn1',
                              hostname='mn1')
        node.get_cluster = lambda: cluster

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='root')

        disk = VolMgrMockDisk(item_id='d1',
                              name='sda',
                              size='10G',
                              uuid='kgb',
                              bootable='true')
        disk.disk_part = 'true'

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.view_root_vg = vg.volume_group_name
        node.storage_profile = sp

        all_items = [cluster, node, vg, disk, sp]
        VolMgrMock._set_state_initial(all_items)

        self.assertEquals(r'$::disk_sda2',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg))

        # ----

        disk.uuid = 'kgb'
        disk.name = 'sdk'
        disk.bootable = 'false'
        disk.disk_part = 'false'

        vg.volume_group_name = 'data'

        self.assertEquals(r'$::disk_sdk',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg))

        # ----

        disk.uuid = '654'

        self.assertEquals(r'$::disk_654_dev',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg))

        # ----

        disk.uuid = '987'
        VolMgrMock._set_state_applied(all_items)

        self.assertEquals(r'$::disk_987_dev',
                          self.plugin.lvm_driver._physical_device_name(node, disk, vg))

    def test_gen_disk_fact_name(self):
        node, sp, vg, fs = self.__mock_node_with_root_vg()

        disk1 = VolMgrMockDisk(item_id='d1', name='sda', size='2T', uuid='kgb', bootable='true')
        disk1.disk_part = 'true'

        disk2 = VolMgrMockDisk(item_id='d2', name='sdb', size='3T', uuid='kgb', bootable='true')
        disk2.disk_part = 'true'

        disk3 = VolMgrMockDisk(item_id='d3', name='sdc', size='1T', uuid='kgb', bootable='true')
        disk3.disk_part = 'true'

        disk4 = VolMgrMockDisk(item_id='d4', name='sdd', size='10G', uuid='kgb', bootable='false')
        disk4.disk_part = 'true'

        # On threshold
        disk1_fact_name = self.plugin.lvm_driver._gen_disk_fact_name(node, disk1, vg, self.context)
        self.assertEquals("$::disk_kgb_part3_dev", disk1_fact_name)

        # Above threshold
        disk2_fact_name = self.plugin.lvm_driver._gen_disk_fact_name(node, disk2, vg, self.context)
        self.assertEquals("$::disk_kgb_part3_dev", disk2_fact_name)

        disk3_fact_name = self.plugin.lvm_driver._gen_disk_fact_name(node, disk3, vg, self.context)
        self.assertEquals("$::disk_kgb_part3_dev", disk3_fact_name)

        disk4_fact_name = self.plugin.lvm_driver._gen_disk_fact_name(node, disk4, vg, self.context)
        self.assertEquals("$::disk_kgb_part1_dev", disk4_fact_name)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_lvm_without_mount_point(self):

        disk = VolMgrMockDisk(item_id='d1',
                              name='hd1',
                              size='10G',
                              uuid='123',
                              bootable='false')

        system = VolMgrMockSystem(item_id='s1')
        system.disks.append(disk)

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.system = system

        pd = VolMgrMockPD(item_id='pd1',
                          device_name=disk.name)

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='None',
                          snap_size='50')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.volume_groups.append(vg)

        node.storage_profile = sp

        all_items = [disk, system, node, sp, pd, vg]
        VolMgrMock._set_state_applied(all_items)
        VolMgrMock._set_state_initial([fs])

        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)

        self.assertEqual(1, len(tasks))

    def test_create_mn_snapshot_tasks(self):

        fs1 = VolMgrMockFS(item_id='fs1',
                           snap_size='100',
                           size='50G',
                           mount_point='/foo',
                           snap_external='false')
        fs2 = VolMgrMockFS(item_id='fs2',
                           snap_size='100',
                           size='50G',
                           mount_point='/bar',
                           snap_external='false')
        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='vg1')
        vg1.file_systems.extend([fs1, fs2])

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='vxvm')
        sp1.volume_groups.append(vg1)

        node1 = VolMgrMockNode(item_id='n1',
                               hostname='mn1')
        self.assertFalse(node1.is_ms())
        node1.storage_profile = sp1

        for fs in (fs1, fs2):
            fs.get_node = MagicMock(return_value=node1)
            fs.parent = MagicMock()
            fs.parent.parent = vg1

        all_items = [fs1, fs2, vg1, sp1, node1]
        VolMgrMock._set_state_applied(all_items)
        self.context.snapshot_name = MagicMock(return_value='snapshot')
        tasks = self.plugin.lvm_driver._create_snapshot_tasks(self.context, node1)

        self.assertEqual(3, len(tasks))

        self.assertEqual("_create_snapshot_cb", tasks[0].call_type)
        self.assertTrue(tasks[0].description.startswith('Create LVM deployment snapshot'))
        self.assertEqual("_create_snapshot_cb", tasks[1].call_type)
        self.assertTrue(tasks[1].description.startswith('Create LVM deployment snapshot'))
        self.assertEqual("_base_rpc_task", tasks[2].call_type)
        self.assertTrue("grub" in tasks[2].description)

        self.context.snapshot_name = MagicMock(return_value='test_named')
        tasks = self.plugin.lvm_driver._create_snapshot_tasks(self.context, node1)
        self.assertEqual(2, len(tasks))
        self.assertEqual("_create_snapshot_cb", tasks[0].call_type)
        self.assertTrue(tasks[0].description.startswith("Create LVM named backup snapshot"))
        self.assertEqual("_create_snapshot_cb", tasks[1].call_type)
        self.assertTrue(tasks[1].description.startswith("Create LVM named backup snapshot"))

    def test_not_duplicated_grub_snapshot(self):

        # for LITPCDS-10321:
        # Grub snapshot must not be done for MS as it is already
        # done from gen_tasks_for_ms_non_modeled_ks_snapshots

        fs1 = VolMgrMockFS(item_id='fs1',
                           snap_size='100',
                           size='50G',
                           mount_point='/foo',
                           snap_external='false')

        fs2 = VolMgrMockFS(item_id='fs2',
                           snap_size='100',
                           size='50G',
                           mount_point='/bar',
                           snap_external='false')

        vg1 = VolMgrMockVG(item_id='vg1',
                           volume_group_name='vg1')

        vg1.file_systems.extend([fs1, fs2])

        sp1 = VolMgrMockStorageProfile(item_id='sp1',
                                       volume_driver='lvm')
        sp1.volume_groups.append(vg1)

        node1 = VolMgrMockMS()
        node1.storage_profile = sp1

        for fs in (fs1, fs2):
            fs.get_node = MagicMock(return_value=node1)
            fs.parent = MagicMock()
            fs.parent.parent = vg1

        all_items = [fs1, fs2, vg1, sp1, node1]
        VolMgrMock._set_state_applied(all_items)

        self.context.snapshot_name = MagicMock(return_value='snapshot')
        tasks = self.plugin.lvm_driver._create_snapshot_tasks(self.context, node1)

        # We should have only two tasks, for fs1 and fs2, but not for grub.
        self.assertEqual(2, len(tasks))

        self.assertEqual('Create LVM deployment'
                         ' snapshot "L_vg1_fs1_" on'
                         ' node "ms1"', tasks[0].description)

        self.assertEqual('Create LVM deployment'
                         ' snapshot "L_vg1_fs2_" on'
                         ' node "ms1"', tasks[1].description)

    def test_delete_mn_snapshot_tasks(self):
        (node, fs1, fs2, snap_data) = self._make_snap_item_and_lvs_data()
        self.assertFalse(node.is_ms())
        #self.plugin.lvm_driver._snapshotted_vg_lv_in_node = MagicMock(return_value = snap_data)

        tasks = self.plugin.lvm_driver._remove_snapshot_tasks(
            self.context, node
        )

        self.assertEqual(3, len(tasks))
        for task in tasks[0:1]:
            self._verify_delete_ss_rpc_task(task)

        self.assertEqual("_remove_grub_cb", tasks[2].call_type)
        self.assertTrue("Remove" in tasks[2].description)
        self.assertTrue("grub" in tasks[2].description)
        # ----

        fs1.snap_external = fs2.snap_external = 'true'
        tasks = self.plugin.lvm_driver._remove_snapshot_tasks(
            self.context, node
        )

        self.assertEquals([], tasks)

    def test_delete_ms_snapshot_tasks(self):
        (ms, fs1, fs2, snap_data) = self._make_snap_item_and_lvs_data(node_type='ms')
        #self.plugin.lvm_driver._snapshotted_vg_lv_in_node = MagicMock(
        # return_value = snap_data)

        # There must not be task to delete grub as this is done at
        # _delete_ms_non_modeled_ks_snapshot_tasks
        tasks = self.plugin.lvm_driver._remove_snapshot_tasks(self.context, ms)
        self.assertEqual(2, len(tasks))
        for task in tasks:
            self.assertFalse('grub' in task.description)

    def _verify_delete_ss_rpc_task(self, task):
        self.assertEqual("_remove_snapshot_cb", task.call_type)
        self.assertTrue("Remove" in task.description)

    def test_restore_mn_snapshot_tasks(self):
        self.setup_model()
        self._create_dataset_primary_disk_xfs()

        for vg in self.node1.storage_profile.volume_groups:
            vg.set_applied()
            for fs in vg.file_systems:
                fs.set_applied()

        self.plugin.lvm_driver.snap_operation_allowed = MagicMock(return_value = True)

        snapshot_model = Mock(query=self.context.query)
        self.context.snapshot_name = MagicMock(return_value='snapshot')
        self.context.is_snapshot_action_forced = lambda: False
        tasks = self.plugin.lvm_driver._restore_snapshot_tasks(
            self.context, snapshot_model, self.context.query('node')
        )

        for i in xrange(0, 10):
            print "task %s ", i, tasks[i].call_type, tasks[i].description

        self._verify_check_ss_rpc_task(tasks[8])
        self._verify_check_valid_ss_rpc_task(tasks[9])

        self.context.is_snapshot_action_forced = lambda: True
        tasks = self.plugin.lvm_driver._restore_snapshot_tasks(
            self.context, snapshot_model, self.context.query('node')
        )

        self.assertEqual(9, len(tasks))

        self.plugin.lvm_driver.snap_operation_allowed = MagicMock(return_value = False)
        tasks = self.plugin.lvm_driver._restore_snapshot_tasks(
            self.context, snapshot_model, self.context.query('node')
        )

        self.assertEqual(0, len(tasks))

    def _make_snap_item_and_lvs_data(self, node_type='node'):
        class MockFs(VolMgrMockFS):
            def __init__(self, item_id):
                super(MockFs, self).__init__(item_id=item_id,
                                             snap_size='50',
                                             snap_external='false',
                                             size='10G',
                                             mount_point="/%s" % item_id)

        fs1 = MockFs("fs1")
        fs2 = MockFs("fs2")

        vg = VolMgrMockVG(item_id="vg1",
                          volume_group_name="vg1")
        vg.file_systems.extend([fs1, fs2])

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver = 'lvm')
        sp.volume_groups.append(vg)

        system = VolMgrMockSystem(item_id='s1')

        if node_type == 'node':
            node = VolMgrMockNode(item_id='n1',
                                  hostname='mn1')
        else:
            node = VolMgrMockMS()

        node.storage_profile = sp
        node.system = system

        VolMgrMock._set_state_applied([fs1, fs2, vg, sp, system, node])

        snap_data = [(vg.item_id, 'L_vg1_fs1_', 'owi-aos--'),
                     (vg.item_id, 'L_vg1_fs2_', 'owi-aos--')]

        return (node, fs1, fs2, snap_data)

    def test__restore_mn_snapshot_tasks_v2(self):
        (node, fs1, fs2, snap_data) = self._make_snap_item_and_lvs_data()
        self.assertFalse(node.is_ms())
        self.plugin.lvm_driver._snapshotted_vg_lv_in_node = MagicMock(return_value = snap_data)

        tasks = self.plugin.lvm_driver._restore_lvs_per_node(node, True, None, False)
        self.assertEqual(3, len(tasks))
        for task in tasks[0:1]:
            self._verify_restore_ss_rpc_task(task)

        self.assertEqual('_base_rpc_task', tasks[2].call_type)
        self.assertTrue("Restore" in tasks[2].description)
        # ----

        fs1.snap_external = fs2.snap_external = 'true'
        tasks = self.plugin.lvm_driver._restore_lvs_per_node(node, True, None, False)
        self.assertEquals([], tasks)

    def _verify_check_ss_rpc_task(self, task):
        hostnames = [node.hostname for node in self.context.query("node")]
        hostnames_list = VolMgrUtils.format_list(hostnames, quotes_char="double")
        self.assertEqual("_check_restore_nodes_reachable_and_snaps_exist_cb", task.call_type)
        expected_description = 'Check peer node(s) %s are reachable with '\
                               'all LVM snapshots present' % hostnames_list
        self.assertEqual(expected_description, task.description)

    def _verify_check_valid_ss_rpc_task(self, task):
        hostnames = [node.hostname for node in self.context.query("node")]
        hostnames_list = VolMgrUtils.format_list(hostnames, quotes_char="double")
        self.assertEqual("_check_valid_ss_rpc_task", task.call_type)
        expected_description = 'Check LVM snapshots on node(s) %s are valid' %\
                               hostnames_list
        self.assertEqual(expected_description, task.description)

    def _verify_restore_ss_rpc_task(self, task):
        self.assertEqual("_restore_ss_rpc_task", task.call_type)
        self.assertTrue("Restore" in task.description)

    def test_parse_lv_scan_line(self):

        line = "/dev/vg_root/lv_home  3552575488B -wi-ao---- /home\n"

        parsed = VolMgrUtils.parse_lv_scan_line(line)

        expected = {
            'path': '/dev/vg_root/lv_home',
            'size': '3552575488B',
            'attrs': '-wi-ao----',
            'vg_name': 'vg_root',
            'lv_name': 'lv_home',
            'mount': '/home',
        }

        self.assertEqual(expected, parsed)

    def test_get_lv_metadata(self):

        # output from lvs agent
        # lvs --unit b -o lv_path,lv_size,lv_attr --noheadings

        node = VolMgrMockNode(item_id='n1', hostname='node')
        context = VolMgrMockContext()

        action_output = {
            node.hostname: "/dev/vg_root/lv_home  3552575488B -wi-ao---- /home\n"
                           "/dev/vg_root/lv_root 12234784768B -wi-ao---- /\n"
                           "/dev/vg_root/lv_swap  2143289344B -wi-ao---- [SWAP]\n"
                           "/dev/vg_root/lv_var  24637341696B -wi-ao---- /var\n"
        }
        action_errors = {}

        self.plugin.lvm_driver._execute_rpc_and_get_output = \
            MagicMock(return_value=(action_output, action_errors))

        lv_metadata = self.plugin.lvm_driver._get_lv_metadata(node, context)

        expected = [
            {
                'path': '/dev/vg_root/lv_home',
                'size': '3552575488B',
                'attrs': '-wi-ao----',
                'vg_name': 'vg_root',
                'lv_name': 'lv_home',
                'mount': '/home',
            },
            {
                'path': '/dev/vg_root/lv_root',
                'size': '12234784768B',
                'attrs': '-wi-ao----',
                'vg_name': 'vg_root',
                'lv_name': 'lv_root',
                'mount': '/',
            },
            {
                'path': '/dev/vg_root/lv_swap',
                'size': '2143289344B',
                'attrs': '-wi-ao----',
                'vg_name': 'vg_root',
                'lv_name': 'lv_swap',
                'mount': '[SWAP]',
            },
            {
                'path': '/dev/vg_root/lv_var',
                'size': '24637341696B',
                'attrs': '-wi-ao----',
                'vg_name': 'vg_root',
                'lv_name': 'lv_var',
                'mount': '/var',
            },
        ]

        self.assertTrue(all([e in lv_metadata for e in expected]))

    def test_get_from_lv_metadata(self):

        node = VolMgrMockNode(item_id='n1', hostname='node')
        context = VolMgrMockContext()

        action_output = {
            node.hostname: "/dev/vg_root/lv_test1 10G owi-aos-- /test1\n"
                           "/dev/vg_root/lv_test2 20G owi-aos-- /test2"
        }
        action_errors = {}

        self.plugin.lvm_driver._execute_rpc_and_get_output = \
            MagicMock(return_value=(action_output, action_errors))

        expected = [
            {
                'path': '/dev/vg_root/lv_test1',
                'size': '10G',
                'attrs': 'owi-aos--',
                'vg_name': 'vg_root',
                'lv_name': 'lv_test1',
                'mount': '/test1'
            },
            {
                'path': '/dev/vg_root/lv_test2',
                'size': '20G',
                'attrs': 'owi-aos--',
                'vg_name': 'vg_root',
                'lv_name': 'lv_test2',
                'mount': '/test2'
            }
        ]

        keys_to_retrieve =['size', 'vg_name', 'path', 'lv_name', 'attrs', 'mount']

        output = self.plugin.lvm_driver._get_from_lv_metadata(
            node, context, keys_to_retrieve
        )

        self.assertEquals(expected, output)

        expected = [{'lv_name': 'lv_test1'}, {'lv_name': 'lv_test2'}]
        keys_to_retrieve =['lv_name']

        output = self.plugin.lvm_driver._get_from_lv_metadata(
            node, context, keys_to_retrieve
        )

        self.assertEquals(expected, output)

        expected = []
        keys_to_retrieve =[]

        output = self.plugin.lvm_driver._get_from_lv_metadata(
            node, context, keys_to_retrieve
        )

        self.assertEquals(expected, output)

    def test__delete_ms_snapshot_tasks(self):
        node1 = VolMgrMockNode(item_id='n1',
                               hostname='mn1')
        self.assertFalse(node1.is_ms())
        node1.storage_profile = VolMgrMockStorageProfile(item_id='sp1',
                                                         volume_driver = 'lvm')

        self.plugin.lvm_driver.get_snapshot_tag = MagicMock(return_value='')
        self.plugin.lvm_driver._check_is_lv_not_swap = MagicMock(return_value=True)

        ms_snapshot_data = [('vg_root', 'L_lv_root_'),
                            ('vg_root', 'L_lv_var_'),
                            ('vg_root', 'L_lv_home_'),
                            ('vg_root', 'L_lv_var_www_'),
                            ('vg_root', 'L_lv_other_')]
        self.plugin.lvm_driver._snapshotted_vg_lv_in_ms = MagicMock(return_value=ms_snapshot_data)

        ms_lv_meta_data = [
          {'lv_name': 'L_lv_root_',     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_root_', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_home_',     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_home_', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_var_',      'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_var_www_',  'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_www_', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'lv_root',               'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_root', 'mount': '/'},
          {'lv_name': 'lv_home',               'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_home', 'mount': '/home'},
          {'lv_name': 'lv_var',                'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var', 'mount': '/var'},
          {'lv_name': 'lv_var_log',            'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_log', 'mount': '/var/log'},
          {'lv_name': 'lv_var_www',            'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_www', 'mount': '/var/www'},
          {'lv_name': 'lv_software',           'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_software', 'mount': '/software'}]
        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value=ms_lv_meta_data)

        tasks = self.plugin.lvm_driver._delete_ms_non_modeled_ks_snapshot_tasks(self.context, node1)
        self.assertEqual(8, len(tasks))


        self.assertEqual('_remove_grub_cb', tasks[7].call_type)
        self.assertTrue('Remove' in tasks[7].description)
        self.assertTrue('grub' in tasks[7].description)

    def test__delete_ms_named_snapshot_tasks(self):
        node1 = VolMgrMockNode(item_id='n1',
                               hostname='mn1')
        self.assertFalse(node1.is_ms())
        node1.storage_profile = VolMgrMockStorageProfile(item_id='sp1',
                                                         volume_driver = 'lvm')

        self.plugin.lvm_driver.get_snapshot_tag = MagicMock(return_value='')
        self.plugin.lvm_driver._check_is_lv_not_swap = MagicMock(return_value=True)

        ms_snapshot_data = [('vg_root', 'L_lv_root_named'),
                            ('vg_root', 'L_lv_var_named'),
                            ('vg_root', 'L_lv_home_named'),
                            ('vg_root', 'L_lv_var_log_named'),
                            ('vg_root', 'L_lv_var_www_named'),
                            ('vg_root', 'L_lv_other_named')]
        self.plugin.lvm_driver._snapshotted_vg_lv_in_ms = MagicMock(return_value=ms_snapshot_data)

        ms_lv_meta_data = [
          {'lv_name': 'L_lv_root_named',     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_root_named', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_home_named',     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_home_named', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_var_named',      'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_named', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_var_log_named',  'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_log_named', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'L_lv_var_www_named',  'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_www_named', 'mount': '[NOT-MOUNTED]'},
          {'lv_name': 'lv_root',             'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_root', 'mount': '/'},
          {'lv_name': 'lv_home',             'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_home', 'mount': '/home'},
          {'lv_name': 'lv_var',              'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var', 'mount': '/var'},
          {'lv_name': 'lv_var_log',          'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_log', 'mount': '/var/log'},
          {'lv_name': 'lv_var_www',          'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_www', 'mount': '/var/www'},
          {'lv_name': 'lv_software',         'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_software', 'mount': '/software'}]

        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value=ms_lv_meta_data)
        self.plugin.lvm_driver.get_snapshot_tag = MagicMock(return_value='named')

        tasks = self.plugin.lvm_driver._delete_ms_non_modeled_ks_snapshot_tasks(self.context, node1)
        self.assertEqual(8, len(tasks))

        for idx in range(0, 4):
            self.assertEqual('_remove_snapshot_cb', tasks[idx].call_type)
            self.assertTrue('Remove' in tasks[idx].description)

    def test__restore_ms_non_modeled_ks_snapshot_tasks(self):
        ms = VolMgrMockMS()
        self.assertTrue(ms.is_ms())
        ms.storage_profile = VolMgrMockStorageProfile(item_id='sp1',
                                                      volume_driver='lvm')
        VolMgrMock._set_state_applied([ms, ms.storage_profile])
        self.plugin.lvm_driver._all_ms_snapshots_present = MagicMock(return_value=True)
        ms_snapshot_data = [("vg_root", "L_lv_root_"),
                            ("vg_root", "L_lv_var_"),
                            ("vg_root", "L_lv_home_"),
                            ("vg_root", "L_lv_var_www_"),
                            ("vg_root", "L_lv_other_")]

        mn_lv_meta_data = {'mn1': '  /dev/root_vg/L_vg1_fs1_     536870912B swi-I-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/root_vg/vg1_fs1        10737418240B owi-aos--- /foo\n' + \
                                  '  /dev/root_vg/vg1_fs2        1073741824B -wi-ao---- /bar\n' + \
                                  '  /dev/root_vg/vg1_fs3        4294967296B -wi-ao---- /baz',
                           'mn2': '  /dev/root_vg/vg1_fs1        10737418240B -wi-ao---- /foo\n' + \
                                  '  /dev/root_vg/vg1_fs2        1073741824B -wi-ao---- /bar\n' + \
                                  '  /dev/root_vg/vg1_fs3        4294967296B -wi-ao---- /baz',
                           'ms1': '  /dev/new_vg/L_vg3_fs3_      54525952B swi-a-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/new_vg/L_vg3_fs4_      109051904B swi-a-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/new_vg/vg3_fs3         1073741824B owi-aos--- /boo\n' + \
                                  '  /dev/new_vg/vg3_fs4         2147483648B owi-aos--- /far\n' + \
                                  '  /dev/vg_root/L_lv_home_     42823843840B swi-a-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/vg_root/L_lv_root_     53687091200B swi-a-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/vg_root/L_lv_var_      98090091200B swi-a-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/vg_root/L_lv_var_www_  23452591200B swi-a-s--- [NOT-MOUNTED]\n' + \
                                  '  /dev/vg_root/lv_home        42823843840B owi-aos--- /home\n' + \
                                  '  /dev/vg_root/lv_root        53687091200B owi-aos--- /\n' + \
                                  '  /dev/vg_root/lv_swap        42823843840B -wi-ao---- [SWAP]\n' + \
                                  '  /dev/vg_root/lv_var         53687091200B owi-aos--- /var\n' + \
                                  '  /dev/vg_root/lv_var_log     53687091200B owi-aos--- /var/log\n' + \
                                  '  /dev/vg_root/lv_var_www     53687091200B owi-aos--- /var/www\n' + \
                                  '  /dev/vg_root/lv_software    53687091200B owi-aos--- /software\n' + \
                                  '  /dev/vg_root/vg1_fs1        10737418240B -wi-a----- /foo\n' + \
                                  '  /dev/vg_root/vg1_fs2        10737418240B -wi-a----- /bar'}

        self.plugin.lvm_driver._snapshotted_vg_lv_in_ms = MagicMock(return_value=ms_snapshot_data)

        ms_lv_meta_data = [
           {"lv_name": "L_lv_root_",    'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_root_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_home_",    'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_home_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_",     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_www_", 'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_www_', 'mount': '[NOT-MOUNTED]'},

           {"lv_name": "L_lv_var_tmp_",          'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_tmp_',          'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_lib_puppetdb_", 'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_lib_puppetdb_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_opt_rh_",       'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_opt_rh_',       'mount': '[NOT-MOUNTED]'},

           {"lv_name": "lv_root",        'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_root', 'mount': '/'},
           {"lv_name": "lv_home",        'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_home', 'mount': '/home'},
           {"lv_name": "lv_var",         'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var', 'mount': '/var'},

           {"lv_name": "lv_var_tmp",          'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_tmp',          'mount': '/var/tmp'},
           {"lv_name": "lv_var_lib_puppetdb", 'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_lib_puppetdb', 'mount': '/var/lib/puppetdb'},
           {"lv_name": "lv_opt_rh",           'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_opt_rh',       'mount': '/var/opt/rh'},

           {"lv_name": "lv_var_log",  'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_log', 'mount': '/var/log'},
           {"lv_name": "lv_var_www",  'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_www', 'mount': '/var/www'},
           {"lv_name": "lv_software", 'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_software', 'mount': '/software'}]

        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value=ms_lv_meta_data)

        self.context.is_snapshot_action_forced = lambda: True
        tasks = self.plugin.lvm_driver._restore_ms_non_modeled_ks_snapshot_tasks(self.context, ms)
        self.assertEqual(8, len(tasks))

        for idx in range(0,6):
            self.assertEqual("_restore_ss_rpc_task", tasks[idx].call_type)
            self.assertTrue("Restore" in tasks[idx].description)

        self.assertEqual("_ping_and_restore_grub_cb", tasks[7].call_type)
        self.assertTrue("Restore" in tasks[7].description)
        self.assertTrue("grub" in tasks[7].description)

        lv_metadata = self.plugin._scan_lv_data(mn_lv_meta_data, 'snapshot')
        for snapshot in lv_metadata:
            attributes = snapshot['attrs']
            rc = VolMgrUtils.check_snapshot_is_valid(attributes)
            if 'I' in attributes:
                self.assertEqual(rc, False)
            else:
                self.assertEqual(rc, True)

        # case 2
        # Missing snapshot: L_lv_var_ is going to be missing ...

        ms_snapshot_data = [("vg_root", "L_lv_var_"),
                            ("vg_root", "L_lv_var_log_"),
                            ("vg_root", "L_lv_var_www_"),
                            ("vg_root", "L_lv_software_"),
                            ("vg_root", "L_lv_home_"),
                            ("vg_root", "L_lv_other_")]
        self.plugin.lvm_driver._snapshotted_vg_lv_in_ms = MagicMock(return_value=ms_snapshot_data)

        ms_lv_meta_data = [
           {"lv_name": "L_lv_root_",     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_root_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_home_",     'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_home_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_log_",  'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_log_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_www_",  'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_www_', 'mount': '[NOT-MOUNTED]'},

           {"lv_name": "L_lv_var_tmp_",          'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_tmp_',          'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_lib_puppetdb_", 'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_lib_puppetdb_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "L_lv_var_opt_rh_",       'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_var_opt_rh_',       'mount': '[NOT-MOUNTED]'},

           {"lv_name": "L_lv_software_", 'vg_name': 'vg_root', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_lv_software_', 'mount': '[NOT-MOUNTED]'},
           {"lv_name": "lv_root",        'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_root', 'mount': '/'},
           {"lv_name": "lv_home",        'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_home', 'mount': '/home'},
           {"lv_name": "lv_var",         'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var', 'mount': '/var'},

           {"lv_name": "lv_var_tmp",          'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_tmp',          'mount': '/var/tmp'},
           {"lv_name": "lv_var_lib_puppetdb", 'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_lib_puppetdb', 'mount': '/var/lib/puppetdb'},
           {"lv_name": "lv_opt_rh",           'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_opt_rh',       'mount': '/var/opt/rh'},

           {"lv_name": "lv_var_log",     'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_log', 'mount': '/var/log'},
           {"lv_name": "lv_var_www",     'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_var_www', 'mount': '/var/www'},
           {"lv_name": "lv_software",    'vg_name': 'vg_root', 'attrs': 'wi-a----',  'path': '/dev/vg1/lv_software', 'mount': '/software'}]
        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value=ms_lv_meta_data)

        self.assertRaises(PluginError,self.plugin.lvm_driver._restore_ms_non_modeled_ks_snapshot_tasks, self.context, ms)
        log = LitpLogger()
        with patch.object(log.trace, 'error') as mock_log:
            try:
                self.plugin.lvm_driver._restore_ms_non_modeled_ks_snapshot_tasks(self.context, ms)
            except:
                pass
            self.assertEqual(mock_log.call_args_list, [call('Snapshot "L_lv_var_" on node "ms1" is missing'),
                                                       call('There are missing snapshots for MS, cannot restore.')])

    def test__restore_lvs_per_cluster(self):

        fs1 = VolMgrMockFS(item_id='fs1',
                           size='50G',
                           mount_point='/foo',
                           snap_size='10',
                           snap_external='false')
        fs2 = VolMgrMockFS(item_id='fs2',
                           size='30G',
                           mount_point='/bar',
                           snap_size='10',
                           snap_external='false')
        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='vg1')
        vg.file_systems.extend([fs1, fs2])

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.volume_groups.append(vg)

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.storage_profile = sp
        self.assertFalse(node.is_ms())

        cluster = VolMgrMockCluster(item_id='c1',
                                    cluster_type='sfha')

        all_items = [fs1, fs2, vg, sp, node, cluster]
        VolMgrMock._set_state_applied(all_items)

        dict1 = {'lv_name': 'L_vg1_fs1_', 'vg_name': 'vg1', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_fs1_', 'mount': '[NOT-MOUNTED]'}

        other_dicts = [
           {'lv_name': 'L_vg1_fs2_',       'vg_name': 'vg1', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_fs2_', 'mount': '[NOT-MOUNTED]'},
           {'lv_name': 'L_fs1_test_named', 'vg_name': 'vg1', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_fs1_test_named', 'mount': '[NOT-MOUNTED]'},
           {'lv_name': 'L_fs2_test_named', 'vg_name': 'vg1', 'attrs': 'swi-a-s--', 'path': '/dev/vg1/L_fs2_test_named', 'mount': '[NOT-MOUNTED]'},
           {'lv_name': 'fs1',              'vg_name': 'vg1', 'attrs': 'owi-aos--', 'path': '/dev/vg1/fs1', 'mount': '/foo'},
           {'lv_name': 'fs2',              'vg_name': 'vg1', 'attrs': 'owi-aos--', 'path': '/dev/vg1/fs2', 'mount': '/bar'}]

        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value = [dict1] + other_dicts)
        snapshot = VolMgrMock(item_type_id='snapshot-base',
                           item_id='/snapshots/snapshot')
        def _mock_context_query(query_item_type, **kwargs):
            if 'snapshot-base' == query_item_type:
                return [snapshot]
            else:
                return []
        self.context.query = _mock_context_query

        tasks = self.plugin.lvm_driver._restore_lvs_on_nodes([node], True, False, "snapshot")
        self.assertEqual(3, len(tasks))

        # ----

        dict1_invalid = dict1.copy()
        dict1_invalid['attrs'] = 'swi-I-s--'

        #self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value = [dict1_invalid] + other_dicts)
        #self.assertRaises(PluginError,
        #                  self.plugin.lvm_driver._restore_lvs_on_nodes, self.context, cluster, [node], True)

        # ----

        ms = VolMgrMockMS()
        ms.storage_profile = sp
        self.assertTrue(ms.is_ms())
        VolMgrMock._set_state_applied([ms])

        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value = [dict1_invalid] + other_dicts)
        #self.assertRaises(PluginError,
        #                  self.plugin.lvm_driver._restore_lvs_on_nodes, self.context, cluster, [ms], True)
#        self.assertRaises(PluginError,
#                          self.plugin.lvm_driver._restore_lvs_on_nodes, cluster, [ms], True)

        self.plugin.lvm_driver._get_from_lv_metadata = MagicMock(return_value = [dict1] + other_dicts)
        tasks = self.plugin.lvm_driver._restore_lvs_on_nodes([ms], True, False, "snapshot")

        # We do not want a task to restore Grub here.
        # That is made at _restore_ms_non_modeled_ks_snapshot_tasks for MS !
        self.assertEqual(2, len(tasks))

    def __mock_node_with_non_root_vg(self):

        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')
        fs = VolMgrMockFS(item_id='fs1', size='10G', mount_point='/')

        vg.file_systems.append(fs)
        sp.volume_groups.append(vg)
        node.storage_profile = sp

        items = [node, sp, vg, fs]
        VolMgrMock._set_state_initial(items)

        return node, sp, vg, fs

    def __mock_node_with_root_vg(self):
        node, sp, vg, fs = self.__mock_node_with_non_root_vg()
        sp.view_root_vg = vg.volume_group_name
        return node, sp, vg, fs

    def test_validate_names_rootvg_positive_case(self):
        node, sp, vg, fs = self.__mock_node_with_root_vg()
        errors = self.plugin.lvm_driver._validate_names_rootvg(node, vg)
        VolMgrMock.assert_validation_errors(self, [], errors)

    def test_validate_names_rootvg_fs_item_id_has_too_many_characters(self):

        node, sp, vg, fs = self.__mock_node_with_root_vg()

        # assign an FS id with too many characters i.e. > 50
        # an FS id cannot exceed 50 characters and is validated for
        fs.item_id = 'x' * 51

        errors = self.plugin.lvm_driver._validate_names_rootvg(node, vg)

        expected = [
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from '
                              'volume-group "{0}" and file-system "{1}", exceeds 50 '
                              'characters.'.format(vg.item_id, fs.item_id)
            )
        ]
        VolMgrMock.assert_validation_errors(self, expected, errors)


    def test_validate_names_rootvg_bad_vg_item_id(self):

        node, sp, vg, fs = self.__mock_node_with_root_vg()

        # bad volume group id with leading underscores and forbidden characters
        vg.item_id = '_vg$id'

        errors = self.plugin.lvm_driver._validate_names_rootvg(node, vg)

        expected = [
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", cannot start with '
                              'an underscore.'.format(vg.item_id, fs.item_id)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", has disallowed '
                              'characters.'.format(vg.item_id, fs.item_id)
            )
        ]
        VolMgrMock.assert_validation_errors(self, expected, errors)

    def test_validate_names_rootvg_bad_vg_name(self):

        node, sp, vg, fs = self.__mock_node_with_non_root_vg()

        # a volume group name cannot exceed 50 characters and is validated for
        vg.volume_group_name = '_${0}'.format('g'*50)

        errors = self.plugin.lvm_driver._validate_names_rootvg(node, vg)

        expected = [
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM volume_group_name "{0}" exceeds 50 '
                              'characters.'.format(vg.volume_group_name)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM volume_group_name "{0}" has disallowed '
                              'characters.'.format(vg.volume_group_name)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM volume_group_name "{0}" cannot start with '
                              'an underscore.'.format(vg.volume_group_name)
            )
        ]
        VolMgrMock.assert_validation_errors(self, expected, errors)

    def test_validate_names_rootvg_banned_fs_and_vg_string_sequences(self):

        node, sp, vg, fs = self.__mock_node_with_root_vg()

        vg.item_id='_mlog_tdata_tmeta'
        fs.item_id='_mimage_rimage'

        errors = self.plugin.lvm_driver._validate_names(node)
        expected = [
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", contains prohibited '
                              '"_mlog".'.format(vg.item_id, fs.item_id)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", contains prohibited '
                              '"_mimage".'.format(vg.item_id, fs.item_id)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", contains prohibited '
                              '"_rimage".'.format(vg.item_id, fs.item_id)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", contains prohibited '
                              '"_tdata".'.format(vg.item_id, fs.item_id)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", contains prohibited '
                              '"_tmeta".'.format(vg.item_id, fs.item_id)
            ),
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM LV name "{0}_{1}", derived from volume-group '
                              '"{0}" and file-system "{1}", cannot start with an '
                              'underscore.'.format(vg.item_id, fs.item_id)
            )
        ]
        VolMgrMock.assert_validation_errors(self, expected, errors)

    def test_validate_names_nonrootvg_positive_test(self):

        node, sp, vg, fs = self.__mock_node_with_non_root_vg()

        # the max characters for volume_group_name is 106 characters
        # the offset is the item ids with some addition characters
        offset = '/{0}_{1}'.format(vg.item_id, fs.item_id)
        vg.volume_group_name = '{0}'.format('x' * (106 - len(offset)))

        errors = self.plugin.lvm_driver._validate_names(node)
        VolMgrMock.assert_validation_errors(self, [], errors)

    def test_validate_names_nonrootvg_vg_name_too_long(self):

        node, sp, vg, fs = self.__mock_node_with_non_root_vg()

        # the max characters for volume_group_name is 118 characters
        # the offset is the item ids with some addition characters
        offset = '/{0}_{1}'.format(vg.item_id, fs.item_id)
        vg.volume_group_name = '{0}'.format('x' * (119 - len(offset)))

        errors = self.plugin.lvm_driver._validate_names(node)
        expected = [
            ValidationError(
                item_path=vg.get_vpath(),
                error_message='LVM FS device name "{0}/{1}_{2}", which is '
                              'derived from VG group name "{0}", item_id '
                              '"{1}" & FS item_id "{2}" exceeds 118 '
                              'characters.'.format(
                    vg.volume_group_name,
                    vg.item_id,
                    fs.item_id
                )
            )
        ]
        VolMgrMock.assert_validation_errors(self, expected, errors)

    def test_validation_of_names(self):
        node = MagicMock()
        node.get_vpath = lambda : '/some/node'

        test_fs = MagicMock()
        test_vg = MagicMock()
        test_vg.get_vpath = lambda : '/some/vg'
        test_fs.get_vpath = lambda : '/some/fs'
        test_vg.volume_group_name = 'vg_group_name'
        test_fs.item_id = 'home'
        test_vg.file_systems = [test_fs]
        node.storage_profile.volume_groups = [test_vg]

        # volume_group_name max length
        test_vg.item_id = 'vg_id'
        test_vg.volume_group_name = 'vg_group_name_{0}'.format('x'*(118-25))
        self.assertEqual(
            [],
            self.plugin.lvm_driver._validate_names(node)
            )
        test_vg.volume_group_name = 'vg_group_name_{0}'.format('x'*(119-25))
        self.assertEqual(
            [ValidationError(
                item_path='/some/vg',
                error_message='LVM FS device name '\
                '"vg_group_name_{0}/vg_id_home", which is derived from '\
                'VG group name "vg_group_name_{0}", '\
                'item_id "vg_id" & FS item_id "home" exceeds 118 characters.'.format(
                    'x'*(119-25)
                    )
                )],
            self.plugin.lvm_driver._validate_names(node)
            )

        # bad chars
        test_fs.item_id = '$home'
        test_vg.item_id = '$vg_id'
        test_vg.volume_group_name = '$vg_group_name'
        self.assertEqual(
            set([
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM VG item_id "$vg_id" contains disallowed characters.'
                    ),
                ValidationError(
                    item_path='/some/fs',
                    error_message='LVM FS item_id "$home" contains disallowed characters.'
                    ),
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM volume_group_name "$vg_group_name" contains disallowed characters.'
                    )
                ]),
            set(self.plugin.lvm_driver._validate_names(node))
            )


        # forbidden names
        test_fs.item_id = '_mimage_rimage'
        test_vg.item_id = '_mlog_tdata_tmeta'
        test_vg.volume_group_name = 'vg_group_name'
        self.assertEqual(
            set([
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM LV name "{0}_{1}", derived from volume-group '\
                    '"{0}" and file-system "{1}", contains prohibited "_mlog".'.format(
                        test_vg.item_id, test_fs.item_id
                        )
                    ),
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM LV name "{0}_{1}", derived from volume-group '\
                    '"{0}" and file-system "{1}", contains prohibited "_mimage".'.format(
                        test_vg.item_id, test_fs.item_id
                        )
                    ),
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM LV name "{0}_{1}", derived from volume-group '\
                    '"{0}" and file-system "{1}", contains prohibited "_rimage".'.format(
                        test_vg.item_id, test_fs.item_id
                        )
                    ),
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM LV name "{0}_{1}", derived from volume-group '\
                    '"{0}" and file-system "{1}", contains prohibited "_tdata".'.format(
                        test_vg.item_id, test_fs.item_id
                        )
                    ),
                ValidationError(
                    item_path='/some/vg',
                    error_message='LVM LV name "{0}_{1}", derived from volume-group '\
                    '"{0}" and file-system "{1}", contains prohibited "_tmeta".'.format(
                        test_vg.item_id, test_fs.item_id
                        )
                    ),
                ]),
            set(self.plugin.lvm_driver._validate_names(node))
            )

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_no_lvm_tasks_for_snap_changes(self):

        disk = VolMgrMockDisk(item_id='d1',
                              name='hd1',
                              size='10G',
                              uuid='123',
                              bootable='false')

        system = VolMgrMockSystem(item_id='s1')
        system.disks.append(disk)

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.system = system

        pd = VolMgrMockPD(item_id='pd1',
                          device_name=disk.name)

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo',
                          snap_size='50')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')
        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.volume_groups.append(vg)

        node.storage_profile = sp

        all_items = [disk, system, node, sp, pd, fs, vg]
        VolMgrMock._set_state_applied(all_items)

        # Nothing new or updated - so no tasks expected
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual([], tasks)

        # -----
        VolMgrMock._set_state_updated([fs])

        # FS state = updated but properties haven't changed.

        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual([], tasks)

        # ----

        # A change to 'snap_size' should not result in any tasks
        fs.snap_size = '80'

        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual([], tasks)

        # ----
        # But a change to 'size' should result in tasks
        fs.size = '20G'

        # One OrderedTaskList expected for the 3 tasks when fs.size is changed
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual(1, len(tasks))

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_extend_vg(self):

        disk1 = VolMgrMockDisk(item_id='d1',
                               name='hd1',
                               size='10G',
                               uuid='789',
                               bootable='false')
        disk2 = VolMgrMockDisk(item_id='d2',
                               name='hd2',
                               size='10G',
                               uuid='456',
                               bootable='false')
        disk3 = VolMgrMockDisk(item_id='d3',
                               name='hd3',
                               size='10G',
                               uuid='123',
                               bootable='false')

        system = VolMgrMockSystem(item_id='s1')
        system.disks.extend([disk1, disk2, disk3])

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.system = system

        pd1 = VolMgrMockPD(item_id='pd1', device_name=disk1.name)
        pd2 = VolMgrMockPD(item_id='pd2', device_name=disk2.name)
        pd3 = VolMgrMockPD(item_id='pd3', device_name=disk3.name)

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo',
                          snap_size='50',
                          snap_external='false')

        # One disk
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='app_vg')
        vg.physical_devices.append(pd1)
        vg.file_systems.append(fs)

        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        sp.volume_groups.append(vg)

        node.storage_profile = sp

        VolMgrMock._set_state_initial([disk1, disk2, disk3, pd1, pd2, pd3])
        VolMgrMock._set_state_applied([node, system, sp, vg, fs])

        # Two tasks, one for vg with specific message, following one purge task
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg, Mock)
        self.assertEqual(2, len(tasks))
        self.assertEqual('Configure LVM VG: "vg1" for FS: "fs1" by extending'\
                         ' with new disk(s): "hd1" on node "mn1"',
                         tasks[1].task_list[0].description)

        # Three disks
        vg.physical_devices.extend([pd2, pd3])

        # One task for fs expected with specific message and a purge task
        # per disk
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual(4, len(tasks))
        self.assertEqual('Configure LVM VG: "vg1" for FS: "fs1" by extending'\
                         ' with new disk(s): "hd1", "hd2" and "hd3" on node "mn1"',
                         tasks[3].task_list[0].description)
        sorted_pvs = ['$::disk_123_dev', '$::disk_456_dev', '$::disk_789_dev']
        self.assertEqual(sorted_pvs, tasks[3].task_list[0].kwargs['pv'])

    @patch('volmgr_plugin.drivers.lvm.LvmDriver._gen_resize_disk_tasks_for_vg')
    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_update_disk_uuid_and_or_size(self, resize):

        disk1 = VolMgrMockDisk(item_id='d1',
                               name='hd1',
                               size='10G',
                               uuid='ATA_VBOX_HARRDISK_VB1234-5699',
                               bootable='true')

        disk2 = VolMgrMockDisk(item_id='d2',
                               name='hd2',
                               size='10G',
                               uuid='ATA_VBOX_HARRDISK_VB6666-5555',
                               bootable='true')

        resize.return_value = []

        system = VolMgrMockSystem(item_id='s1')
        system.disks = [disk1, disk2]

        node = VolMgrMockMS()
        node.system = system

        pd1 = VolMgrMockPD(item_id='pd1', device_name=disk1.name)
        pd2 = VolMgrMockPD(item_id='pd2', device_name=disk2.name)

        fs1 = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/home',
                          snap_size='50',
                          snap_external='false')

        # One disk
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='root_vg')
        vg.physical_devices.append(pd1)
        vg.physical_devices.append(pd2)
        vg.file_systems.append(fs1)

        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        sp.volume_groups.append(vg)

        node.storage_profile = sp

        VolMgrMock._set_state_applied([disk1, disk2, pd1, pd2, node, system, sp, vg, fs1])

        disk1.uuid='ATA_VBOX_HARRDISK_VB1234-5677'
        fs1.mount_point=''
        VolMgrMock._set_state_updated([disk1, fs1])

        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual(2, len(tasks[0].task_list))
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs1", '
                 'VG: "vg1" with uuid(s): "ATA_VBOX_HARRDISK_VB1234-5677"',
                          tasks[0].task_list[0].description)
        self.assertEqual('Unmount \'ext4\' file system on node "ms1" - FS: '
                          '"fs1", VG: "vg1", mount point: "/home"',
                          tasks[0].task_list[1].description)

        fs2 = VolMgrMockFS(item_id='fs2',
                          size='10G',
                          mount_point='/app1',
                          snap_size='50',
                          snap_external='false')

        VolMgrMock._set_state_initial([fs2])
        vg.file_systems.append(fs2)
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs1", VG: '
                          '"vg1" with uuid(s): "ATA_VBOX_HARRDISK_VB1234-5677"',
                                             tasks[0].task_list[0].description)
        self.assertEqual('Unmount \'ext4\' file system on node "ms1" - FS: '
                                      '"fs1", VG: "vg1", mount point: "/home"',
                                             tasks[0].task_list[1].description)
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs2", VG: '
                                    '"vg1"', tasks[1].task_list[0].description)
        self.assertEqual('Create LVM mount directory on node "ms1" - FS: "fs2", '
                                               'VG: "vg1", mount point: "/app1"',
                                               tasks[1].task_list[1].description)
        self.assertEqual('Mount \'ext4\' file system on node "ms1" - FS: "fs2", '
            'VG: "vg1", mount point: "/app1"', tasks[1].task_list[2].description)

        self.assertEqual(2, len(tasks[0].task_list))
        self.assertEqual(3, len(tasks[1].task_list))

        disk1.size='12G'
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs1", VG: '
                     '"vg1" with uuid(s): "ATA_VBOX_HARRDISK_VB1234-5677" and '
                     'disk size(s): "12G"', tasks[0].task_list[0].description)

        # Reset uuid so only size is updated
        disk1.uuid='ATA_VBOX_HARRDISK_VB1234-5699'
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs1", VG: '
           '"vg1" with disk size(s): "12G"', tasks[0].task_list[0].description)

    @patch('volmgr_plugin.drivers.lvm.LvmDriver._gen_resize_disk_tasks_for_vg')
    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_update_disk_and_extend_pds(self, resize):
        disk1 = VolMgrMockDisk(item_id='d1',
                               name='hd1',
                               size='10G',
                               uuid='789',
                               bootable='false')
        disk2 = VolMgrMockDisk(item_id='d2',
                               name='hd2',
                               size='10G',
                               uuid='456',
                               bootable='false')
        disk3 = VolMgrMockDisk(item_id='d3',
                               name='hd3',
                               size='10G',
                               uuid='123',
                               bootable='false')

        resize.return_value = []

        system = VolMgrMockSystem(item_id='s1')
        system.disks.extend([disk1, disk2, disk3])

        node = VolMgrMockMS()
        node.system = system

        pd1 = VolMgrMockPD(item_id='pd1', device_name=disk1.name)
        pd2 = VolMgrMockPD(item_id='pd2', device_name=disk2.name)
        pd3 = VolMgrMockPD(item_id='pd3', device_name=disk3.name)

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo',
                          snap_size='50',
                          snap_external='false')

        fs2 = VolMgrMockFS(item_id='fs2',
                          size='10G',
                          mount_point='/bar',
                          snap_size='50',
                          snap_external='false')

        # One disk
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='app_vg')
        vg.physical_devices.append(pd1)
        vg.file_systems.append(fs)

        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        sp.volume_groups.append(vg)

        node.storage_profile = sp

        VolMgrMock._set_state_initial([disk2, disk3, pd2, pd3, fs2])
        VolMgrMock._set_state_applied([node, system, sp, vg, fs, pd1])
        VolMgrMock._set_state_updated([disk1])
        # add pd and update the uuid of a disk
        vg.file_systems.append(fs2)
        disk1.uuid='555'
        disk1.applied_properties['uuid'] = '554'
        vg.physical_devices.append(pd2)
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual(3, len(tasks))
        #one task for extending fs1
        self.assertEqual(1, len(tasks[1].task_list))
        #3 tasks for new fs2
        self.assertEqual(3, len(tasks[2].task_list))
        self.assertEqual('Configure LVM VG: "vg1" for FS: "fs1" by extending '\
                         'with new disk(s): "hd2" and by updating disk(s) '\
                         'with uuid(s): "555" on node "ms1"',
                         tasks[1].task_list[0].description)
        self.assertEqual('Configure LVM VG: "vg1" for FS: "fs2" by extending '\
                         'with new disk(s): "hd2" on node "ms1"',
                         tasks[2].task_list[0].description)

        #update size of disk also
        disk1.applied_properties['size'] = '10G'
        disk1.size='20G'
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual('Configure LVM VG: "vg1" for FS: "fs1" by extending'\
                          ' with new disk(s): "hd2" and by updating disk(s) '\
                  'with uuid(s): "555" and disk size(s): "20G" on node "ms1"',
                         tasks[1].task_list[0].description)

        # remove update of the uuid
        disk1.applied_properties['uuid'] = '555'
        disk1.uuid = '555'
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual('Configure LVM VG: "vg1" for FS: "fs1" by extending'\
                         ' with new disk(s): "hd2" and by updating disk(s) '\
                         'with disk size(s): "20G" on node "ms1"',
                         tasks[1].task_list[0].description)
        VolMgrMock._set_state_applied([disk1, disk2, pd2, fs2])
        disk1.applied_properties['size'] = '20G'
        disk1.size='30G'
        disk2.applied_properties['uuid'] = '456'
        disk2.uuid='30061111222233'
        disk2.applied_properties['size'] = '10G'
        disk2.size='20G'
        VolMgrMock._set_state_updated([disk1, disk2])
        tasks = self.plugin.lvm_driver.gen_tasks_for_volume_group(node, vg,
                                                                  Mock)
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs1", VG: '\
                      '"vg1" with uuid(s): "30061111222233" and disk size(s): '\
                      '"30G" and "20G"', tasks[0].task_list[0].description)
        self.assertEqual('Configure LVM volume on node "ms1" - FS: "fs2", VG: '\
                      '"vg1" with uuid(s): "30061111222233" and disk size(s): '\
                      '"30G" and "20G"', tasks[1].task_list[0].description)

    def test__setting_disk_part_property(self):

        disk1 = VolMgrMockDisk(item_id='d1',
                               name='hd1',
                               size='10G',
                               uuid='789',
                               bootable='false')
        disk2 = VolMgrMockDisk(item_id='d2',
                               name='hd2',
                               size='10G',
                               uuid='456',
                               bootable='false')

        system = VolMgrMockSystem(item_id='s1')
        system.disks.extend([disk1, disk2])

        pd1 = VolMgrMockPD(item_id='pd1',
                           device_name=disk1.name)
        pd2 = VolMgrMockPD(item_id='pd2',
                           device_name=disk2.name)

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          snap_size='50',
                          mount_point='/foo')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='root_vg')
        vg.file_systems.append(fs)

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.view_root_vg = vg.volume_group_name

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.storage_profile = sp
        node.system = system

        all_items = [disk1, disk2, system, pd1, pd2, fs, vg, sp, node]
        VolMgrMock._set_state_initial(all_items)
        VolMgrMock._set_state_applied([disk1])

        # --- when disk not in initial state, disk_part is unchanged
        # rootvg with vg/pd/disk initial
        vg.physical_devices.append(pd1)
        disk1.disk_part = 'foobar'

        self.plugin.lvm_driver._set_partition_flag(node, vg)
        self.assertEquals('foobar', disk1.disk_part)

        # --- disks from initial install on root vg
        # rootvg initial, 2nd pd initial, 2nd disk initial
        VolMgrMock._set_state_initial([disk1])
        vg.physical_devices.append(pd2)
        disk1.disk_part = disk2.disk_part = 'false'

        self.plugin.lvm_driver._set_partition_flag(node, vg)
        self.assertEquals('true', disk1.disk_part)
        self.assertEquals('true', disk2.disk_part)

        # --- disks from root VG extension
        # rootvg not initial, pd1/disk1 applied, pd2/disk2 initial
        VolMgrMock._set_state_applied([disk1, pd1, vg])
        disk2.disk_part = 'true'

        self.plugin.lvm_driver._set_partition_flag(node, vg)
        self.assertEquals('true', disk1.disk_part)
        self.assertEquals('false', disk2.disk_part)

        # --- disks from non-root VG extension
        # appvg not initial, pd1/disk1 applied, pd2/disk2 initial
        vg.volume_group_name = 'appvg'
        disk2.disk_part = 'true'

        self.plugin.lvm_driver._set_partition_flag(node, vg)
        self.assertEquals('false', disk2.disk_part)

        # --- disks from initial install on non-rootvg
        # appvg initial, 2nd pd initial, 2nd disk initial
        VolMgrMock._set_state_initial([disk1, pd1, vg])
        disk1.disk_part = 'true'
        disk2.disk_part = 'true'

        self.plugin.lvm_driver._set_partition_flag(node, vg)
        self.assertEquals('false', disk1.disk_part)
        self.assertEquals('false', disk2.disk_part)

    def test__gen_task_for_pv_clearout(self):

        disk = VolMgrMockDisk(item_id='d1',
                              name='hd1',
                              size='10G',
                              uuid='789',
                              bootable='false')

        system = VolMgrMockSystem(item_id='s1')
        system.disks.append(disk)

        pd = VolMgrMockPD(item_id='pd1',
                          device_name=disk.name)

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='root_vg')
        vg.physical_devices.append(pd)

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.view_root_vg = vg.volume_group_name

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.storage_profile = sp
        node.system = system

        all_items = [disk, system, pd, vg, sp, node]
        VolMgrMock._set_state_initial(all_items)

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertEqual(None, task)

        # any_vg with vg/pd/disk initial, disk_part is true
        vg.volume_group_name = "any_vg"
        disk.disk_part = "true"

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertEqual(None, task)

        # any_vg with vg/disk initial, disk_part false, pd not initial
        disk.disk_part = "false"
        VolMgrMock._set_state_applied([pd])

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertEqual(None, task)

        # any_vg with vg/pd initial, disk_part false, disk not initial
        VolMgrMock._set_state_initial([vg, pd])
        VolMgrMock._set_state_applied([disk])

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertEqual(None, task)

        # --- Initial install, disks for non-root VG
        task_description = 'Purge existing partitions and volume groups'\
                           ' from disk "hd1" on node "mn1"'

        # any_vg with vg/pd/disk initial, disk_part false
        disk.disk_part = "false"
        VolMgrMock._set_state_initial([disk])

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertNotEqual(None, task)
        self.assertEqual(task_description, task.description)

        # --- VG extension, purge task generated

        # any_vg not initial, pd/disk initial
        vg.is_initial = lambda: False
        disk.disk_part = "false"

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertNotEqual(None, task)
        self.assertEqual(task_description, task.description)

        # rootvg not initial, pd/disk initial
        vg.volume_group_name = "root_vg"
        VolMgrMock._set_state_applied([vg])
        disk.disk_part = "false"

        task = self.plugin.lvm_driver.\
            _gen_task_for_pv_clearout(node, vg, pd, 'root_vg')
        self.assertNotEqual(None, task)
        self.assertEqual(task_description, task.description)

    def test_gen_tasks_for_modeled_snapshot(self):

        disk1 = VolMgrMockDisk(
            item_id="disk1",
            name="hd0",
            size="1G",
            uuid="abcd-xxxx",
            bootable="false"
        )

        disk2 = VolMgrMockDisk(
            item_id="disk2",
            name="hd1",
            size="1G",
            uuid="wxyz-aaaa",
            bootable="false"
        )

        system1 = VolMgrMockSystem(item_id='sys1')

        pd1 = VolMgrMockPD(item_id='pd1',device_name='hd0')
        pd2 = VolMgrMockPD(item_id='pd2',device_name='hd1')

        fs1 = VolMgrMockFS(
            item_id='abc',
            mount_point="/abc",
            size='200M',
            snap_size='5'
        )

        fs2 = VolMgrMockFS(
            item_id='xyz',
            mount_point="/wxy",
            size='200M',
            snap_size='5'
        )

        vg1 = VolMgrMockVG(item_id='vg1',volume_group_name='grp_1')
        vg2 = VolMgrMockVG(item_id='vg2',volume_group_name='grp_2')

        sp1 = VolMgrMockStorageProfile(item_id='sp1',volume_driver='lvm')

        node1 = VolMgrMockNode(item_id="node1",hostname="node1")

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

        system1.query = VolMgrMock.mock_query({
            'disk' : disks,
            'disk-base' :disks,
        })

        node1.system = system1
        node1.storage_profile = sp1

        nodes = [node1]

        items = [sp1, system1]
        items += (nodes + disks + physical_devices
                  + volume_groups + file_systems)

        VolMgrMock._set_state_applied(items)

        context = VolMgrMockContext()

        snapshot = context # reference self to mimic a snapshot

        context.snapshot_action = lambda: 'remove'
        context.snapshot_model = snapshot
        context.snapshot_name = lambda: 'snapshot'

        context.query = VolMgrMock.mock_query({
            'disk' : disks,
            'disk-base': disks,
            'snapshot-base': [snapshot],
            'node': nodes,
            'file-system': file_systems,
            'physical-device': physical_devices,
            'volume-group': volume_groups,
            'storage-profile': [sp1],
            'system': [system1],
        })

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(
                context, context.snapshot_model, nodes)

        self.assertEquals(3, len(tasks))

        # Change the type of the two disks to lun
        disk1.item_type_id="fake-disk"
        disk2.item_type_id="fake-disk"

        # regenerate all mock query functions
        context.query = VolMgrMock.mock_query({
            'fake-disk' : disks,
            'disk-base': disks,
            'snapshot-base': [snapshot],
            'node': nodes,
            'file-system': file_systems,
            'physical-device': physical_devices,
            'volume-group': volume_groups,
            'storage-profile': [sp1],
            'system': [system1],
        })

        system1.query = VolMgrMock.mock_query({
            'fake-disk' : disks,
            'disk-base' :disks,
        })

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(
                context, context.snapshot_model, nodes)

        self.assertEquals(0, len(tasks))

    def test_validate_profile_globally(self):
        disk1 = Mock(uuid='uuid1',
                     item_type_id='disk',
                     item_id='d1')
        disk1.name = 'hd1'

        disk2 = Mock(uuid='uuid2',
                     item_type_id='disk',
                     item_id='d2')
        disk2.name = 'hd2'

        sys1 = Mock(disks=[disk1, disk2],
                    item_type_id='system',
                    item_id='s1')

        disk3 = Mock(uuid='uuid3',
                     item_type_id='disk',
                     item_id='d1')
        disk3.name = 'hd1'

        disk4 = Mock(uuid='uuid4',
                     item_type_id='disk',
                     item_id='d2')
        disk4.name = 'hd2'

        sys2 = Mock(disks=[disk3, disk4],
                    item_type_id='system',
                    item_id='s2')

        pd1 = Mock(device_name='hd1',
                   item_type_id='physical-device',
                   item_id='pd1',
                   get_vpath=lambda: '/d/e/f')
        pd2 = Mock(device_name='hd2',
                   item_type_id='physical-device',
                   item_id='pd2',
                   get_vpath=lambda: '/g/h/i')

        pd3 = Mock(device_name='hd1',
                   item_type_id='physical-device',
                   item_id='pd3',
                   get_vpath=lambda: '/D/E/F')
        pd4 = Mock(device_name='hd2',
                   item_type_id='physical-device',
                   item_id='pd4',
                   get_vpath=lambda: '/G/H/I')

        vg1 = Mock(physical_devices=[pd1, pd2],
                   item_type_id='volume-group',
                   item_id='vg1',
                   get_vpath=lambda: '/j/k/l')
        vg2 = Mock(physical_devices=[pd3, pd4],
                   item_type_id='volume-group',
                   item_id='vg2',
                   get_vpath=lambda: '/J/K/L')

        source_profile = Mock(get_vpath='/A/B/C',
                              volume_driver='lvm')

        profile1 = Mock(volume_groups=[vg1],
                        item_type_id='storage-profile',
                        item_id='sp1',
                        volume_driver='lvm',
                        get_vpath=lambda: '/m/n/o',
                        get_source=lambda: source_profile)
        profile2 = Mock(volume_groups=[vg2],
                        item_type_id='storage-profile',
                        item_id='sp1',
                        volume_driver='lvm',
                        get_vpath=lambda: '/M/N/O',
                        get_source=lambda: source_profile)

        node1 = Mock(hostname='mn1',
                     item_type_id='node',
                     item_id='n1',
                     system=sys1,
                     storage_profile=profile1)
        node2 = Mock(hostname='mn2',
                     item_id='n2',
                     item_type_id='node',
                     system=sys2,
                     storage_profile=profile2)
        nodes = [node1, node2]

        for item in [sys1, sys2,
                     disk1, disk2, disk3, disk4,
                     pd1, pd2, pd3, pd4,
                     vg1, vg2,
                     profile1, profile2,
                     node1, node2]:
            item.is_initial = lambda: True
            item.is_updated = lambda: False
            item.is_for_removal = lambda: False

        for item in [sys1, sys2, disk1, disk2, disk3, disk4]:
            item.get_vpath=lambda: '/a/b/c'

        errors = self.plugin.lvm_driver.validate_profile_globally(source_profile, nodes)

        self.assertEquals([], errors)

        # ---

        # Duplicate the UUIDs
        disk3.uuid = disk1.uuid

        errors = self.plugin.lvm_driver.validate_profile_globally(source_profile, nodes)

        msg = 'Disk UUID "%s" must be globally unique when used by a LVM storage-profile' % disk1.uuid
        expected_error1 = ValidationError(error_message=msg,
                                          item_path='/d/e/f')
        expected_error2 = ValidationError(error_message=msg,
                                          item_path='/D/E/F')

        self.assertEquals([expected_error1, expected_error2], errors)

        # ---

        # Change the "type" of the storage-profile
        source_profile.volume_driver = profile2.volume_driver = 'not-lvm'

        errors = self.plugin.lvm_driver.validate_profile_globally(source_profile, nodes)
        self.assertEquals([], errors)

        profile1.volume_driver = profile2.volume_driver = 'lvm'

        # ---

        # Duplicate the UUIDs but cater for the KGB environment
        disk3.uuid = disk1.uuid = 'kgb'

        errors = self.plugin.lvm_driver.validate_profile_globally(profile1, nodes)
        self.assertEquals([], errors)

    def test_for_litpcds_9308(self):
        (node, fs1, _, _) = self._make_snap_item_and_lvs_data()
        node.storage_profile.volume_groups[0].file_systems = [fs1]

        self.assertFalse(node.is_ms())
        fs1.is_applied = lambda: False
        fs1.is_updated = lambda: True
        fs1.item_id = 'root'
        fs1.mount_point = '/'
        fs1.snap_size = '1'
        fs1.size = '16G'
        fs1.snap_external = 'false'

        fs1.get_node = MagicMock(return_value=node)
        fs1.parent = MagicMock()
        fs1.parent.parent=node.storage_profile.volume_groups[0]

        tasks = self.plugin.lvm_driver._create_snapshot_tasks(self.context, node)
        try:
            self.assertEquals(2, len(tasks))
        except:
            for task in tasks:
                print "Task: %s  Description: %s" % (task, task.description)
            raise

    def test_for_litpcds_9397(self):
        self.setup_model()
        self._create_dataset_primary_disk()

        qItems = [self.snapshot, self.cluster1,
                 self.node1, self.node2,
                 self.system1, self.system2,
                 self.sp1] + \
                 [vg for vg in self.sp1.volume_groups] + \
                 [fs for fs in vg.file_systems] + \
                 [pd for pd in vg.physical_devices] + \
                 [vg for vg in self.node1.storage_profile.volume_groups] + \
                 [fs for fs in vg.file_systems] + \
                 [pd for pd in vg.physical_devices] + \
                 [vg for vg in self.node2.storage_profile.volume_groups] + \
                 [fs for fs in vg.file_systems] + \
                 [pd for pd in vg.physical_devices]

        for qItem in qItems:
            qItem._set_state('Applied')

        the_node1_fs = None
        for vg in self.node1.storage_profile.volume_groups:
            for fs in vg.file_systems:
                the_node1_fs = fs    # Just grab the 1st FS
                break
            break

        the_node2_fs = None
        for vg in self.node2.storage_profile.volume_groups:
            for fs in vg.file_systems:
                the_node2_fs = fs
                break
            break

        self.assertTrue(the_node1_fs.item_id, the_node2_fs.item_id)

        # Only do an update on one node; the other node should remain 'Applied'
        rsp = self.model_manager.update_item(the_node1_fs.get_vpath(), snap_external='true')
        self.assertFalse(isinstance(rsp, list), rsp)

        self.assertTrue(the_node1_fs.get_state(), 'Updated')
        self.assertTrue(the_node2_fs.get_state(), 'Applied')

    def test__validate_lvm_uuids_litpcds_11291_capitalization(self):
        # Make sure disk uuids are unique within a single storage profile
        #  - ensure that cap

        sys1 = VolMgrMockSystem(item_id='s1')
        disk1 = VolMgrMockDisk(
            item_id='d1',
            name='hd0', size='10G', uuid='abcxyz123', bootable=False)

        sys2 = VolMgrMockSystem(item_id='s2')
        disk2 = VolMgrMockDisk(
            item_id='d0',
            name='hd0', size='5G', uuid=disk1.uuid, bootable=False)

        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        fs1 = VolMgrMockFS(item_id='root', mount_point='/', size='4G')
        fs2 = VolMgrMockFS(item_id='swap', mount_point='swap', size='2G')

        pd1 = VolMgrMockPD(item_id='pd0', device_name='hd0')

        cluster = VolMgrMockCluster(item_id='c1')

        node1 = VolMgrMockNode(item_id="n1", hostname="mn1")
        node2 = VolMgrMockNode(item_id="n2", hostname="mn2")

        disks = [disk1, disk2]
        nodes = [node1, node2]
        systems = [sys1, sys2]
        filesystems = [fs1, fs2]

        # 2. Add relationships between the model items
        # each cluster requires at least 1 node, this is a vcs cluster

        sp.volume_groups.append(vg)
        vg.file_systems.extend(filesystems)
        vg.physical_devices.append(pd1)

        for idx, node in enumerate(nodes):
            node.system = systems[idx]
            node.system.disks.append(disks[idx])
            node.storage_profile = sp

        cluster.nodes.extend(nodes)

        # 3. Set appropriate states for every model item
        items =[cluster, vg, sp, pd1]
        for ilist in (items, disks, nodes, systems, filesystems):
            VolMgrMock._set_state_initial(ilist)

        msg = ('Disk UUID "%s" must be globally unique ' +
               'when used by a LVM storage-profile') % disk1.uuid

        expected = [
            ValidationError(
                item_path=pd1.get_vpath(),
                error_message=msg
            )
        ]

        # this validation should already be in place
        errors = self.plugin.lvm_driver._validate_lvm_uuids(sp, nodes)
        VolMgrMock.log_trace('>> LITPCDS-11291 : Case 1 non-unique uuid',
                             expected, errors)
        VolMgrMock.assert_validation_errors(self, expected, errors)

        # this should uncover the missing validation for capitalization
        disk1.uuid = disk1.uuid.upper()
        errors = self.plugin.lvm_driver._validate_lvm_uuids(sp, nodes)
        VolMgrMock.log_trace('>> LITPCDS-11291 : Case 1 capitalization',
                             expected, errors)
        VolMgrMock.assert_validation_errors(self, expected, errors)

    def test_should_not_snapshot_lun_only_storage_profile(self):
        size = 40 * 10 ** 9
        disk_name = 'sda'
        system = VolMgrMockSystem('sys1')

        def system_query_side_effect(item_type_id, name):
            data = defaultdict(lambda: defaultdict(list))
            # just placeholder instead of disk
            data['disk-base'].update({disk_name: [disk_name]})
            return data[item_type_id][name]

        system.query = MagicMock(side_effect=system_query_side_effect)

        sp = VolMgrMockStorageProfile('my_sp', 'lvm')
        node = VolMgrMockNode('my_node', 'mn1')
        vg = VolMgrMockVG('my_vg', 'my_vg')
        fs = VolMgrMockFS('my_fs', size, '/')
        fs._set_state_xxx([fs], 'applied')
        pd = VolMgrMockPD('my_pd', disk_name)
        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)
        sp.volume_groups.append(vg)
        node.system = system
        node.storage_profile = sp
        fs.get_node = MagicMock(return_value=node)
        fs.parent = MagicMock()
        fs.parent.parent = vg
        self.assertFalse(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, vg))
        # named snapshots always created
        self.assertTrue(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, vg,
                                                                                        name='foo'))

    def test_should_snapshot_disk_only_storage_profile(self):
        size = 40 * 10 ** 9
        system = VolMgrMockSystem('sys1')
        system.query = MagicMock(return_value=[])
        sp = VolMgrMockStorageProfile('my_sp', 'lvm')
        node = VolMgrMockNode('my_node', 'mn1')
        vg = VolMgrMockVG('my_vg', 'my_vg')
        fs = VolMgrMockFS('my_fs', size, '/')
        fs._set_state_xxx([fs], 'applied')
        pd = VolMgrMockPD('my_pd', 'sda')
        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)
        sp.volume_groups.append(vg)
        node.system = system
        node.storage_profile = sp
        fs.get_node = MagicMock(return_value=node)
        fs.parent = MagicMock()
        fs.parent.parent = vg
        self.assertTrue(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, vg))
        # named snapshots always created
        self.assertTrue(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, vg, name='foo'))

    def test_should_snapshot_disk_only_vg_of_mixed_storage(self):
        size = 40 * 10 ** 9
        system = VolMgrMockSystem('sys1')

        def system_query_side_effect(item_type_id, name):
            data = defaultdict(lambda: defaultdict(list))
            data['disk-base'].update({'sda': [disk], 'sdb': [lun_disk]})
            data['disk'].update({'sda': [disk]})
            return data[item_type_id][name]

        system.query = MagicMock(side_effect=system_query_side_effect)

        sp = VolMgrMockStorageProfile('my_sp', 'vxvm')
        node = VolMgrMockNode('my_node', 'mn1')
        vg = VolMgrMockVG('my_vg', 'my_vg')
        lun_vg = VolMgrMockVG('lun_my_vg', 'lun_my_vg')
        fs = VolMgrMockFS('my_fs', size, '/')
        lun_fs = VolMgrMockFS('lun_my_fs', size, '/swap/')
        pd = VolMgrMockPD('my_pd', 'sda')
        lun_pd = VolMgrMockPD('lun_my_pd', 'sdb')
        disk = VolMgrMockDisk('my_disk', 'sda', size, 'uuid1', False, False)
        lun_disk = VolMgrMockDisk('lun_my_disk', 'sdb', size, 'uuid2', False, 'lun-disk')

        vg.get_ms = lambda: None
        vg.get_node = lambda: node

        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)

        lun_vg.file_systems.append(lun_fs)
        lun_vg.physical_devices.append(lun_pd)

        node.system = system
        node.storage_profile = sp

        sp.volume_groups.extend([vg, lun_vg])

        fs.get_node = MagicMock(return_value=node)
        fs.parent = MagicMock()
        fs.parent.parent = vg

        lun_fs.get_node = MagicMock(return_value=node)
        lun_fs.parent = MagicMock()
        lun_fs.parent.parent = lun_vg
        fs._set_state_xxx([fs, lun_fs], 'applied')

        self.assertTrue(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, vg))
        self.assertFalse(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, lun_vg))
        # named snapshots always created
        self.assertTrue(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, vg, name='foo'))
        self.assertTrue(self.plugin.lvm_driver._vg_snap_operation_allowed_in_san_aspect(node, lun_vg, name='foo'))

    def create_storage_profile_for_10830(self, mount_point, vg_name, sp_id):
        fs = VolMgrMockFS(item_id=sp_id +'-fs1',
                          snap_size='100',
                          size='10G',
                          mount_point=mount_point,
                          snap_external='false')
        pd = VolMgrMockPD(sp_id + '-pd1', 'sda')
        vg = VolMgrMockVG(sp_id + '-vg1', vg_name)
        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)
        sp = VolMgrMockStorageProfile(sp_id, 'lvm')
        sp.volume_groups.append(vg)

        all_items = [fs, pd, vg, sp]
        VolMgrMock._set_state_applied(all_items)

        return sp

    def create_node_for_10830(self, storage_profile, item_type, host, system_id):

        if item_type == 'node':
            node = VolMgrMockNode('n1', host)
        else:
            node = VolMgrMockMS()

        sys = VolMgrMockSystem(system_id)
        dsk = VolMgrMockDisk(storage_profile.item_id + '-dsk1',
                             storage_profile.volume_groups[0].physical_devices[0].device_name,
                             '50G',
                             system_id + '-uuid',
                             False,
                             False)
        sys.disks.append(dsk)

        node.storage_profile = storage_profile
        node.system = sys

        all_items = [node, sys, dsk]
        VolMgrMock._set_state_applied(all_items)

        return node

    def assert_correct_tasks(self, tasks, expected):

        callbacks = {'restore-fs':                       '_restore_ss_rpc_task',
                     'restore-grub':                     '_base_rpc_task',
                     'ping-and-restore-grub':            '_ping_and_restore_grub_cb',
                     'restart-cluster-nodes':            '_restart_node',
                     'wait-for-node':                    '_wait_for_node_up',
                     'check-snaps-valid':                 '_check_valid_ss_rpc_task',
                     'check-snaps-exist':                '_check_restore_nodes_reachable_and_snaps_exist_cb',
                     'combo-restart-and-wait-for-nodes': '_wait_for_nodes_up',
                     'update-grub':                      '_base_rpc_task',
                     'base':                             '_base_rpc_task',
                     'noop_grub_task':                   '_noop_grub_task_cb'}


        not_found = []

        for expected_task in expected:
            expected_cb_key = expected_task['cb_key']
            expected_cb_name = callbacks[expected_cb_key]

            expected_model_item = expected_task['model-item']
            expected_model_item_path = expected_model_item.get_vpath()

            found = False
            for task in tasks:
                if task.callback.__name__ == expected_cb_name and \
                   task.model_item.get_vpath() == expected_model_item_path:
                    found = True
                    break
            if not found:
                not_found.append((expected_cb_key, expected_cb_name, expected_model_item_path))

        if not_found:
            print "Missing task:"
            for (key, cb, item) in not_found:
                print "Task: callback-key: %s, callback: %s model-item: %s" % (key, cb, item)

            print "Actually found:"
            for task in tasks:
                print "Task: callback: %s model-item: %s" % (task.callback.__name__, task.model_item.get_vpath())

            raise Exception("Error - expected/actual task mismatch")

    def test_litpcds_10830(self):

        sp1 = self.create_storage_profile_for_10830('/db', 'the_mn_vg', 'sp1')
        sp2 = self.create_storage_profile_for_10830('/data', 'the_ms_vg', 'sp2')

        node = self.create_node_for_10830(sp1, 'node', 'mn1', 's1')
        ms = self.create_node_for_10830(sp2, 'ms', 'ms1', 's2')

        cluster = VolMgrMockCluster(item_id='c1', cluster_type='sfha')
        cluster.nodes.append(node)

        snap = VolMgrMock(item_type_id='snapshot-base', item_id='snapshot')
        snap.rebooted_clusters = ''

        node.get_cluster = lambda: cluster

        all_items = [cluster, snap]
        VolMgrMock._set_state_applied(all_items)

        def _mock_context_query(query_item_type, **kwargs):
            if 'snapshot-base' == query_item_type:
                return [snap]
            elif 'cluster' == query_item_type:
                return [cluster]
            elif 'ms' == query_item_type:
                return [ms]
            else:
                return []

        context = VolMgrMockContext()
        context.snapshot_action = lambda: 'restore'
        context.query = _mock_context_query
        snapshot_model = context

        the_mn_fs = node.storage_profile.volume_groups[0].file_systems[0]
        the_ms_fs = ms.storage_profile.volume_groups[0].file_systems[0]

        # ---

        # Just one MN, the Happy Path

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       cluster.nodes)

        expected_mn_hp = [{'cb_key': 'check-snaps-exist',     'model-item': snap},
                          {'cb_key': 'check-snaps-valid',     'model-item': snap},
                          {'cb_key': 'restore-fs',            'model-item': node.system},
                          {'cb_key': 'restore-grub',          'model-item': node.system}]

        self.assert_correct_tasks(tasks, expected_mn_hp)

        # ---

        # Make mn.fs look like is wasn't snapped, so shouldn't be restored

        the_mn_fs.snap_size = 0
        the_mn_fs.current_snap_size = 0

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       cluster.nodes)

        self.assertEquals(0, len(tasks), tasks)

        # Revert previous changes
        the_mn_fs.snap_size = 100
        the_ms_fs.snap_size = 100
        the_mn_fs.current_snap_size = 100

        # ---

        # Now happy path test with the MS included

        all_nodes = cluster.nodes + [ms]
        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       all_nodes)

        expected = expected_mn_hp + \
                   [{'cb_key': 'restore-fs', 'model-item': ms.system}]

        self.assert_correct_tasks(tasks, expected)

        # ---

        # Now make ms.fs look like is wasn't snapped, so shouldn't be restored

        the_ms_fs.snap_size = 0

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       all_nodes)

        self.assert_correct_tasks(tasks, expected_mn_hp)

        # Revert previous change
        the_ms_fs.snap_size = 100

        # ----

        # MN present but nothing to restore, just the MS FS

        the_mn_fs.snap_size = 0
        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       all_nodes)

        expected_ms_hp = [{'cb_key': 'check-snaps-exist',     'model-item': snap},
                          {'cb_key': 'check-snaps-valid',     'model-item': snap},
                          {'cb_key': 'restore-fs',            'model-item': ms.system}]

        self.assert_correct_tasks(tasks, expected_ms_hp)

        the_mn_fs.snap_size = 100

        # ---

        # Back to just the MN

        # Change the Restore action-forced to True
        context.is_snapshot_action_forced = lambda: True

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       cluster.nodes)
        expected = [{'cb_key': 'check-snaps-valid',                'model-item': snap},
                    {'cb_key': 'restore-fs',                       'model-item': node.system},
                    {'cb_key': 'ping-and-restore-grub',            'model-item': node.system}]

        self.assert_correct_tasks(tasks, expected)

        # ---

        # Now make mn.fs look like is wasn't snapped, so shouldn't be restored
        the_mn_fs.snap_size = 0

        tasks = self.plugin.lvm_driver.gen_tasks_for_modeled_snapshot(context, snapshot_model,
                                                                       cluster.nodes)

        the_mn_fs.snap_size = 100

    def test_volume_only_changed_on_properties(self):
        disk = VolMgrMockDisk(item_id='d1',
                              name='hd1',
                              size='10G',
                              uuid='123',
                              bootable='false')

        disks = [disk]

        fs = VolMgrMockFS(item_id='fs1',
                          snap_size='100',
                          size='10G',
                          mount_point='/foo',
                          snap_external='false')
        pd = VolMgrMockPD('pd1', 'sda')
        vg = VolMgrMockVG('vg1', 'vg1')
        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)
        sp = VolMgrMockStorageProfile('sp1', 'lvm')
        sp.volume_groups.append(vg)

        VolMgrMock._set_state_initial([fs, pd, vg, sp, disk])

        all_items = [fs, sp]
        VolMgrMock._set_state_applied(all_items)

        # pd and vg are initial
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        self.assertFalse(result)

        VolMgrMock._set_state_applied([pd])
        # vg is still initial
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        self.assertFalse(result)

        VolMgrMock._set_state_updated([pd, vg])
        # pd and vg are updated
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        self.assertFalse(result)

        VolMgrMock._set_state_applied([pd])
        # vg is still updated
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        self.assertFalse(result)
        VolMgrMock._set_state_applied([vg])

        fs.size = '16M'
        VolMgrMock._set_state_updated([fs])
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        self.assertFalse(result)

        VolMgrMock._set_state_applied([fs])

        fs.mount_point = 'other'
        VolMgrMock._set_state_updated([fs])
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        self.assertTrue(result)

        fs.size = '99M'
        VolMgrMock._set_state_updated([fs])
        result = self.plugin.lvm_driver._volume_only_changed_on_properties([pd], vg, fs, disks, ['mount_point'])
        # size is not in the expected updated properties
        self.assertFalse(False)

    def test_prevent_reuse_mount_points(self):

        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')
        fs_a = VolMgrMockFS(item_id='fs_a', size='10G', mount_point='/A')
        fs_b = VolMgrMockFS(item_id='fs_b', size='10G', mount_point='/B')

        vg.file_systems.extend([fs_a, fs_b])
        sp.volume_groups.append(vg)
        node.storage_profile = sp

        items = [node, sp, vg, fs_a, fs_b]
        VolMgrMock._set_state_applied(items)

        self.context.query = VolMgrMock.mock_query({
            'file-system': [fs_a, fs_b],
        })

        ################################################################
        # CASE1; Pure mount points swap
        ################################################################
        fs_a.mount_point = '/B'
        fs_b.mount_point = '/A'
        VolMgrMock._set_state_updated([fs_a, fs_b])

        msg_a = ('Cannot change mount point "%s" from '
                 'file system "%s" to "%s".' %
                 (fs_a.applied_properties['mount_point'],
                  fs_a.item_id, fs_b.item_id))

        msg_b = ('Cannot change mount point "%s" from '
                 'file system "%s" to "%s".' %
                 (fs_b.applied_properties['mount_point'],
                  fs_b.item_id, fs_a.item_id))

        error_a = ValidationError(item_path=fs_a.get_vpath(),
                                 error_message=msg_a)
        error_b = ValidationError(item_path=fs_b.get_vpath(),
                                 error_message=msg_b)

        errors = self.plugin._validate_fs_mount_point_not_reused(sp)

        self.assertEqual(2, len(errors))
        self.assertTrue(error_a in errors)
        self.assertTrue(error_b in errors)

        ################################################################
        # CASE2; Non set mount points involved ( None )
        ################################################################
        fs_a.mount_point = '/A'
        fs_b.mount_point = '/B'
        VolMgrMock._set_state_applied([fs_a, fs_b])

        fs_a.mount_point = None
        fs_b.mount_point = '/A'
        VolMgrMock._set_state_updated([fs_a, fs_b])

        msg_a = ('Cannot change mount point "%s" from '
                 'file system "%s" to "%s".' %
                 (fs_a.applied_properties['mount_point'],
                  fs_a.item_id, fs_b.item_id))

        error_a = ValidationError(item_path=fs_a.get_vpath(),
                                 error_message=msg_a)

        errors = self.plugin._validate_fs_mount_point_not_reused(sp)
        self.assertTrue(error_a in errors)

        ################################################################
        # CASE3; Two missing mount points to valid mount points
        # Not reuse case. just testing that validation does not catch
        # this valid case
        ################################################################

        fs_a.mount_point = None
        fs_b.mount_point = None
        VolMgrMock._set_state_applied([fs_a, fs_b])

        fs_a.mount_point = '/A'
        fs_b.mount_point = '/B'
        VolMgrMock._set_state_updated([fs_a, fs_b])

        errors = self.plugin._validate_fs_mount_point_not_reused(sp)
        self.assertEqual([], errors)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_resize_disk_tasks_for_vg(self):

        disk = VolMgrMockDisk(item_id='d1',
                              name='hd1',
                              size='10G',
                              uuid='123',
                              bootable='false')

        system = VolMgrMockSystem(item_id='s1')
        system.disks.append(disk)

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')
        node.system = system

        pd = VolMgrMockPD(item_id='pd1',
                          device_name=disk.name)

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo',
                          snap_size='50')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')
        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        sp.volume_groups.append(vg)

        node.storage_profile = sp

        all_items = [disk, system, node, sp, pd, fs, vg]
        VolMgrMock._set_state_applied(all_items)
        VolMgrMock._set_state_updated([disk])
        disk.size = '12G'

        tasks = self.plugin.lvm_driver._gen_resize_disk_tasks_for_vg(node, vg,
                                                                     Mock)
        self.assertEqual(1, len(tasks))
        self.assertEqual((['mn1'], 'lv', 'pvresize'), tasks[0].args)
        self.assertEqual({'disk_fact_name': 'disk_123_dev'}, tasks[0].kwargs)


    def _assert_expected_config_task(self, target_task,
                                    node, model_item, description,
                                    call_type, call_id, **kwargs):

        def _assert_equal_config_tasks(expected, target):
            self.assertTrue(expected.isinstanceof(MockConfigTask))
            self.assertTrue(target.isinstanceof(MockConfigTask))
            self.assertEqual(expected.node, target.node)
            self.assertEqual(expected.model_item, target.model_item)
            self.assertEqual(expected.description, target.description)
            self.assertEqual(expected.call_type, target.call_type)
            self.assertEqual(expected.call_id, target.call_id)
            self.assertEqual(expected.persist, target.persist)
            self.assertEqual(expected.replaces, target.replaces)
            self.assertEqual(expected.kwargs, target.kwargs)

        persist = kwargs.pop('persist', True)
        replaces = kwargs.pop('replaces', set())
        expected_task = MockConfigTask(node,
                                       model_item,
                                       description,
                                       call_type,
                                       call_id,
                                       **kwargs)

        expected_task.persist = persist
        expected_task.replaces = replaces

        _assert_equal_config_tasks(expected_task, target_task)

    def _check_umount_dir_task(self, node, vg, fs, task, replaced_tasks=set()):

        description =\
                 'Unmount \'{0}\' file system on node "{1}" - FS: "{2}"'\
                 ', VG: "{3}", mount point: "{4}"'.format(fs.type,
                                                          node.hostname,
                                                          fs.item_id,
                                                          vg.item_id,
                                    fs.applied_properties['mount_point'])

        full_fs_device_name = LvmDriver.gen_full_fs_dev_name(node, vg, fs)
        self._assert_expected_config_task(task,
                                         node, fs, description,
                                         'mount',
                                         fs.applied_properties['mount_point'],
                                         fstype=fs.type,
                                         device=full_fs_device_name,
                                         ensure='absent',
                                         persist=False,
                                         replaces=replaced_tasks)

    def _check_create_dir_task(self, node, vg, fs, task, replaced_tasks=set()):

        description =\
             'Create LVM mount directory on node "{0}" - FS:'\
             ' "{1}", VG: "{2}", mount point: "{3}"'.format(node.hostname,
                                                            fs.item_id,
                                                            vg.item_id,
                                                            fs.mount_point)

        self._assert_expected_config_task(task,
                                         node, fs, description,
                                         'volmgr::create_mount_path',
                                         fs.mount_point,
                                         mount_point=fs.mount_point,
                                         replaces=replaced_tasks)

    def _check_mount_dir_task(self, node, vg, fs, task, replaced_tasks=set()):

        description = 'Mount \'{0}\' file system on node "{1}" - FS: "{2}",'\
           ' VG: "{3}", mount point: "{4}"'.format(fs.type,
                                                   node.hostname,
                                                   fs.item_id,
                                                   vg.item_id,
                                                   fs.mount_point)

        fs_device_name = LvmDriver.gen_fs_device_name(node, vg, fs)
        full_fs_device_name = LvmDriver.gen_full_fs_dev_name(node, vg, fs)
        task_kwargs = {'fstype': fs.type,
                       'device': full_fs_device_name,
                       'ensure': 'mounted',
                       'require': [{'type': 'Lvm::Volume',
                                    'value': fs_device_name}],
                       'options': fs.mount_options if fs.mount_options else 'defaults,x-systemd.device-timeout=300',
                       'atboot': "true",
                       'pass': fs.fsck_pass if fs.fsck_pass else '2'}

        self._assert_expected_config_task(task,
                                         node, fs, description,
                                         'mount',
                                         fs.mount_point,
                                         replaces=replaced_tasks,
                                         **task_kwargs)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_mount_volume_none_mount_point (self):

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point=None)
        VolMgrMock._set_state_initial([node, vg, fs])

        task = self.plugin.lvm_driver._gen_task_mount_volume(node, vg, fs)
        self.assertEquals(task, None)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_task_create_dir_initial_state(self):

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo')
        VolMgrMock._set_state_initial([node, vg, fs])

        task = self.plugin.lvm_driver._gen_task_create_dir(node, vg, fs)
        self._check_create_dir_task(node, vg, fs, task)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_updated_mount_point_tasks_mount_point_has_been_replaced(self):

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo_new')
        fs.applied_properties['mount_point'] = '/foo'

        VolMgrMock._set_state_applied([node, vg])
        VolMgrMock._set_state_updated([fs])

        tasks = self.plugin.lvm_driver._gen_updated_mount_point_tasks(node, vg, fs)
        replaced_tasks = self.plugin.lvm_driver._gen_replaced_tasks(fs)
        self.assertEqual(3, len(tasks))
        self._check_umount_dir_task(node, vg, fs, tasks[0])
        self._check_create_dir_task(node, vg, fs, tasks[1], replaced_tasks)
        self._check_mount_dir_task(node, vg, fs, tasks[2])

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_updated_mount_point_tasks_mount_point_has_been_removed(self):

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G')
        fs.applied_properties['mount_point'] = '/foo'

        VolMgrMock._set_state_applied([node, vg])
        VolMgrMock._set_state_updated([fs])

        tasks = self.plugin.lvm_driver._gen_updated_mount_point_tasks(node, vg, fs)
        replaced_tasks = self.plugin.lvm_driver._gen_replaced_tasks(fs)
        self.assertEqual(1, len(tasks))
        self._check_umount_dir_task(node, vg, fs, tasks[0], replaced_tasks)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_updated_mount_point_tasks_mount_point_has_been_added(self):

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo')

        VolMgrMock._set_state_applied([node, vg])
        VolMgrMock._set_state_updated([fs])

        tasks = self.plugin.lvm_driver._gen_updated_mount_point_tasks(node, vg, fs)
        self.assertEqual(2, len(tasks))
        self._check_create_dir_task(node, vg, fs, tasks[0])
        self._check_mount_dir_task(node, vg, fs, tasks[1])

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_updated_mount_point_tasks_mount_options_has_been_updated(self):

        node = VolMgrMockNode(item_id='n1',
                              hostname='mn1')

        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='app_vg')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/foo')

        VolMgrMock._set_state_applied([node, vg, fs])
        VolMgrMock._set_state_updated([fs])
        fs.mount_options='blaa'

        tasks = self.plugin.lvm_driver._gen_updated_mount_point_tasks(node, vg, fs)
        self.assertEqual(1, len(tasks))
        self._check_mount_dir_task(node, vg, fs, tasks[0])

    def test_gen_grub_tasks(self):
        fs1 = VolMgrMockFS(
            item_id='fs1', size='10G', mount_point='/', snap_size='20')
        fs2 = VolMgrMockFS(
            item_id='fs2', size='14G', mount_point='/home', snap_size='50')
        fs3 = VolMgrMockFS(
            item_id='fs3', size='2G', type='swap', snap_size='50')
        fs4 = VolMgrMockFS(
            item_id='fs4', size='10G', mount_point='/opt', snap_size='20')
        fs5 = VolMgrMockFS(
            item_id='fs5', size='20G', mount_point='/var', snap_size='40')
        fs6 = VolMgrMockFS(
            item_id='fs6', size='20G', mount_point='/var/lib', snap_size='40')

        vg1 = VolMgrMockVG(item_id="vg1", volume_group_name="vg_root")
        vg1.file_systems.extend([fs1, fs2, fs3])

        vg2 = VolMgrMockVG(item_id="vg2", volume_group_name="app_vg_1")
        vg2.file_systems.extend([fs4, fs5])

        vg3 = VolMgrMockVG(item_id="vg3", volume_group_name="app_vg_2")
        vg3.file_systems.extend([fs6])

        volume_groups = [vg1, vg2, vg3]

        sp = VolMgrMockStorageProfile(item_id="sp1", volume_driver="lvm")
        sp.volume_groups.extend(volume_groups)

        cluster = VolMgrMockCluster(item_id="c1", item_type_id="vcs-cluster")
        node1 = VolMgrMockNode(item_id="node1", hostname="node1")

        cluster.nodes = [node1]
        node1.cluster_for_get = cluster
        node1.storage_profile = sp

        all_items = [fs1, fs2, fs3, fs4, fs5, fs6, vg1, vg2, vg3, cluster,
                     node1]

        VolMgrMock._set_state_initial(all_items)
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        self.assertEqual([], tasks)

        cluster.grub_lv_enable = "false"
        VolMgrMock._set_state_initial(all_items)
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        self.assertEqual([], tasks)

        cluster.grub_lv_enable = "true"
        expected_lvs = ["rd.lvm.lv=vg_root/vg1_fs1", "rd.lvm.lv=vg_root/vg1_fs2", "rd.lvm.lv=vg_root/vg1_fs3",\
                        "rd.lvm.lv=app_vg_1/vg2_fs4", "rd.lvm.lv=app_vg_1/vg2_fs5", "rd.lvm.lv=app_vg_2/vg3_fs6"]
        expected_tasks = [{'cb_key': 'update-grub', 'model-item': sp}]
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        lvs = tasks[0].kwargs['lv_names'].split()
        self.assert_correct_tasks(tasks, expected_tasks)
        self.assertEqual(set(expected_lvs), set(lvs))

        cluster.grub_lv_enable = "false"
        VolMgrMock._set_state_updated([cluster])
        expected_tasks = [{'cb_key': 'noop_grub_task', 'model-item': cluster}]
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        self.assert_correct_tasks(tasks, expected_tasks)

        cluster.grub_lv_enable = "true"
        VolMgrMock._set_state_applied(all_items)
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        self.assertEqual([], tasks)

        cluster.grub_lv_enable = "false"
        VolMgrMock._set_state_updated([cluster])
        expected_lvs = ["rd.lvm.lv=vg_root/vg1_fs1", "rd.lvm.lv=vg_root/vg1_fs3"]
        expected_tasks = [{'cb_key': 'update-grub', 'model-item': sp}]
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        lvs = tasks[0].kwargs['lv_names'].split()
        self.assert_correct_tasks(tasks, expected_tasks)
        self.assertEqual(set(expected_lvs), set(lvs))

        cluster.grub_lv_enable = "true"
        VolMgrMock._set_state_for_removal([fs3, fs5])
        expected_lvs = ["rd.lvm.lv=vg_root/vg1_fs1", "rd.lvm.lv=vg_root/vg1_fs2", \
                        "rd.lvm.lv=app_vg_1/vg2_fs4", "rd.lvm.lv=app_vg_2/vg3_fs6"]
        expected_tasks = [{'cb_key': 'update-grub', 'model-item': sp}]
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        lvs = tasks[0].kwargs['lv_names'].split()
        self.assert_correct_tasks(tasks, expected_tasks)
        self.assertEqual(set(expected_lvs), set(lvs))

        cluster.grub_lv_enable = "false"
        VolMgrMock._set_state_for_removal([fs2, fs6])
        expected_lvs = ["rd.lvm.lv=vg_root/vg1_fs1", "rd.lvm.lv=vg_root/vg1_fs3"]
        expected_tasks = [{'cb_key': 'update-grub', 'model-item': sp}]
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        lvs = tasks[0].kwargs['lv_names'].split()
        self.assert_correct_tasks(tasks, expected_tasks)
        self.assertEqual(set(expected_lvs), set(lvs))

        VolMgrMock._set_state_applied(all_items)
        VolMgrMock._set_state_for_removal([fs3,fs5])
        tasks = self.plugin.lvm_driver.gen_grub_tasks(node1, volume_groups)
        self.assertEqual([], tasks)

    def test_set_partition_flag(self):
        vg = VolMgrMockVG(item_id='vg1',
                          volume_group_name='root')
        disk1 = VolMgrMockDisk(item_id='disk1',
                               bootable='false',
                               name='disk1',
                               size='40G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        disk2 = VolMgrMockDisk(item_id='disk2',
                               bootable='false',
                               name='disk1',
                               size='100G',
                               uuid='ATA_VBOX_HARDDISK_VB24150799-28e77305')

        disk1.is_initial = lambda: True
        disk2.is_initial = lambda: True

        VolMgrMockDisk.set_properties(disk1)
        ms = MagicMock()
        ms.system.storage_profile.volume_groups = [vg]
        ms.system.storage_profile.volume_driver = 'lvm'
        pd = VolMgrMockPD(disk1.name, disk1.name)
        pd2 = VolMgrMockPD(disk2.name, disk2.name)
        pd.is_for_removal = lambda: False
        pd2.is_for_removal = lambda: False
        ms.system.disks = [disk1, disk2]
        ms.system.storage_profile.volume_groups[0].physical_devices = [pd,pd2]
        ms.system.storage_profile.volume_groups[0].physical_devices[0].device_name = disk1.name
        ms.system.storage_profile.volume_groups[0].physical_devices[1].device_name = disk2.name
        self.plugin.lvm_driver._set_partition_flag(ms, vg)
        self.assertEquals('false', disk1.disk_part)
        self.assertEquals('false', disk2.disk_part)
        pd.is_for_removal = lambda: True
        disk1.bootable = 'false'
        self.plugin.lvm_driver._set_partition_flag(ms, vg)
        self.assertEquals('false', disk1.disk_part)

    @patch('volmgr_plugin.drivers.lvm.ConfigTask', new=MockConfigTask)
    def test_gen_task_mount_clean(self):
        cluster = VolMgrMockVCSCluster(item_id='c1',
                                      cluster_type='vcs')
        node = VolMgrMockNode(item_id='mn1',
                              hostname='mn1')
        node.get_cluster = lambda: cluster
        node.upgrade = Mock(os_reinstall='false')

        cluster.nodes.append(node)

        os_profile = VolMgrMockOS(item_id="os", version='rhel6')
        node.os = os_profile

        vg = VolMgrMockVG(item_id='vg2',
                          volume_group_name='vg_app')

        fs = VolMgrMockFS(item_id='fs1',
                          size='10G',
                          mount_point='/mnt/fs1',
                          snap_size='50')

        sp = VolMgrMockStorageProfile(item_id='sp1',
                                      volume_driver='lvm')
        node.storage_profile = sp
        sp.volume_groups.append(vg)
        vg.file_systems.append(fs)

        all_items = [cluster, node, os_profile, sp, vg, fs]
        VolMgrMock._set_state_initial(all_items)

        tasks = self.plugin.lvm_driver._gen_initial_mount_point_tasks(
                                                              node, vg, fs)
        self.assertEquals(2, len(tasks))

        # ----

        os_profile.version = 'rhel7'
        cluster.cluster_type = 'sfha'

        tasks = self.plugin.lvm_driver._gen_initial_mount_point_tasks(
                                                              node, vg, fs)
        self.assertEquals(2, len(tasks))

        # ----

        for vgname in ('vg_root', 'neo4j_vg'):
            vg.volume_group_name = vgname
            tasks = self.plugin.lvm_driver._gen_initial_mount_point_tasks(
                                                                  node, vg, fs)
            self.assertEquals(2, len(tasks))

        vg.volume_group_name = 'vg_app'

        # ----

        node.upgrade.os_reinstall = 'true'

        tasks = self.plugin.lvm_driver._gen_initial_mount_point_tasks(
                                                                node, vg, fs)
        self.assertEquals(3, len(tasks))

        cbtask = tasks[2]
        expected = [{'cb_key': 'base', 'model-item': fs}]
        self.assert_correct_tasks([cbtask], expected)

        expected_args = ([node.hostname], 'vrts', 'clean_packages_dir')
        self.assertEquals(cbtask.args, expected_args)
        expected_kwargs = {'destination_dir': '{0}/*'.format(fs.mount_point)}
        self.assertEquals(cbtask.kwargs, expected_kwargs)

    def test_mount_options_has_changed(self):

        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='lvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')
        fs_a = VolMgrMockFS(item_id='fs_a', size='10G', mount_point='/A')
        fs_b = VolMgrMockFS(item_id='fs_b', size='10G', mount_point='/B')

        vg.file_systems.extend([fs_a, fs_b])
        sp.volume_groups.append(vg)
        node.storage_profile = sp

        items = [node, sp, vg, fs_a, fs_b]
        VolMgrMock._set_state_applied(items)

        self.assertFalse(VolMgrUtils._mount_options_has_changed(fs_a))
        VolMgrMock._set_state_updated([fs_b])
        fs_b.mount_options = 'blaaa'
        self.assertTrue(VolMgrUtils._mount_options_has_changed(fs_b))

