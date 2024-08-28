##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

import json
import unittest
from collections import defaultdict

from litp.core.plugin_manager import PluginManager
from litp.core.model_manager import ModelManager
from litp.core.plugin_context_api import PluginApiContext
from litp.core.validators import ValidationError
from litp.extensions.core_extension import CoreExtension

from litp.core.execution_manager import (
    CallbackExecutionException,
    PluginError
)

from litp.core.constants import BASE_RPC_NO_ANSWER
from volmgr_extension.volmgr_extension import VolMgrExtension
from volmgr_plugin.volmgr_plugin import VolMgrPlugin
from volmgr_plugin.volmgr_utils import VolMgrUtils

from volmgr_plugin.drivers.vxvm import (
    VxvmDriver,
    VxvmSnapshotReport,
    DgNotImportedError
)

from volmgr_plugin.drivers.vxvm_snapshot_data_mgr import (
    VxvmSnapshotDataMgr,
    VolumeGroupData,
    FileSystemData
)


from litp.core.rpc_commands import (
    RpcExecutionException,
    RpcCommandProcessorBase,
    RpcCommandOutputProcessor
)

from mock import MagicMock, patch, Mock, call

from .mock_vol_items import (
    VolMgrMock,
    VolMgrMockContext,
    VolMgrMockVCSCluster,
    VolMgrMockNode,
    VolMgrMockSystem,
    VolMgrMockStorageProfile,
    VolMgrMockDisk,
    VolMgrMockOtherDisk,
    VolMgrMockPD,
    VolMgrMockVG,
    VolMgrMockFS,
    MockCallbackTask
)

rpc_result_ok = {
    'node': {
        'errors': '',
        'data': {
            'status': 0,
            'err': '',
            'out': 'test_output'
        }
    }
}

rpc_result_error = {
    'node': {
        'errors': 'Error msg',
        'data': {
            'status': 1,
            'err': 'Err msg',
            'out': 'test_output'
        }
    }
}

rpc_result_error2 = {
    'node': {
        'errors': '',
        'data': {
            'status': 1,
            'err': 'Err msg',
            'out': 'test_output'
        }
    }
}


def mock_execute_rpc_and_process_result_err(
        nodes, agent, action, action_kwargs=None, timeout=None, retries=0):
    if action == "add_disk_to_group":
        return rpc_result_error
    else:
        return rpc_result_ok


def mock_execute_rpc_and_process_result_err2(
        nodes, agent, action, action_kwargs=None, timeout=None, retries=0):
    if action == "add_disk_to_group":
        return rpc_result_error2
    else:
        return rpc_result_ok


def mock_execute_rpc_and_process_result_exec(
        nodes, agent, action, action_kwargs=None, timeout=None, retries=0):
    if action == "add_disk_to_group":
        raise RpcExecutionException("test_exc")
    else:
        return rpc_result_ok

class TestVxvmVolMgrDriver(unittest.TestCase):

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

    def test_get_initial_node(self):

        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')

        cluster.nodes = ['node1', 'node2', 'node3']
        node = self.plugin.vxvm_driver._get_cluster_node(cluster)
        self.assertEqual('node1', node)

    def test_is_openstack_env(self):

        context = VolMgrMockContext()

        self.assertFalse(self.plugin.vxvm_driver._is_openstack_env(context))

        context.config_manager.global_properties[0].key = 'enm_deployment_type'
        context.config_manager.global_properties[0].value = 'vLITP_ENM_On_Rack_Servers'

        self.assertTrue(self.plugin.vxvm_driver._is_openstack_env(context))

    def test_create_vg_task(self):

        # 1. Mock Up LITP Model
        # create mock LITP model items
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='hd0',
                              size='10G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        context = VolMgrMockContext()

        sys = VolMgrMockSystem(item_id='s1')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')

        # create relationships between model items
        sys.disks.append(disk)
        node.system = sys
        vg.physical_devices.append(pd)

        cluster.storage_profile = [sp]
        cluster.nodes = [node]

        # set model item states
        initial_items = [sp, vg, cluster]
        applied_items = [node, sys, disk, pd]

        VolMgrMock._set_state_initial(initial_items)
        VolMgrMock._set_state_applied(applied_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.initialize_vg_tasks(
            node, vg, cluster, context
        )

        # 3. Assert Code Outputs
        expected = [
            'Clear SCSI-3 registration keys from disk "hd0" on node "mn1"',
            'Setup VxVM disk "hd0" on node "mn1"',
            'Create VxVM disk group "vg_root" on node "mn1"'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

        # in some cases we will have occurrences of the same message
        expected = ['_vx_init_rpc_task']
        VolMgrMock.assert_task_call_types(self, expected, tasks)

        expected = [
            {'disk_name': disk.get_vpath(), 'timeout': 299},
            {'disk_name': disk.get_vpath(), 'disk_group': 'vg_root', 'timeout': 299},
            {'disk_names': [disk.get_vpath()], 'disk_group': 'vg_root', 'timeout': 299}
        ]
        VolMgrMock.assert_task_kwargs(self, expected, tasks)

    def test_create_vg_task_openstack_cloud_deploy(self):

        # 1. Mock Up LITP Model
        # create mock LITP model items
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='hd0',
                              size='10G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        context = VolMgrMockContext()

        context.config_manager.global_properties[0].key = 'enm_deployment_type'
        context.config_manager.global_properties[0].value = 'vLITP_ENM_On_Rack_Servers'

        sys = VolMgrMockSystem(item_id='s1')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')

        # create relationships between model items
        sys.disks.append(disk)
        node.system = sys
        vg.physical_devices.append(pd)

        cluster.storage_profile = [sp]
        cluster.nodes = [node]

        # set model item states
        initial_items = [sp, vg, cluster]
        applied_items = [node, sys, disk, pd]

        VolMgrMock._set_state_initial(initial_items)
        VolMgrMock._set_state_applied(applied_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.initialize_vg_tasks(
            node, vg, cluster, context
        )

        # 3. Assert Code Outputs
        expected = [
            'Setup VxVM disk "hd0" on node "mn1"',
            'Create VxVM disk group "vg_root" on node "mn1"'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks, equal=True)

        # in some cases we will have occurrences of the same message
        expected = ['_vx_init_rpc_task']
        VolMgrMock.assert_task_call_types(self, expected, tasks)

        expected = [
            {'disk_name': disk.get_vpath(), 'disk_group': 'vg_root', 'timeout': 299},
            {'disk_names': [disk.get_vpath()], 'disk_group': 'vg_root', 'timeout': 299}
        ]
        VolMgrMock.assert_task_kwargs(self, expected, tasks, equal=True)

    def test_resize_disk_group_tasks(self):

        # 1. Mock Up LITP Model
        # create mock LITP model items
        node1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        node2 = VolMgrMockNode(item_id='n2', hostname='mn2')
        disk1 = VolMgrMockDisk(item_id='d1',
                              bootable='false',
                              name='lun_0',
                              size='10G',
                              uuid='30000000fc85c928'
                              )
        disk2 = VolMgrMockDisk(item_id='d2',
                              bootable='false',
                              name='lun_0',
                              size='10G',
                              uuid='30000000fc85c928'
                              )
        disk3 = VolMgrMockDisk(item_id='d3',
                              bootable='false',
                              name='lun_1',
                              size='10G',
                              uuid='3000000021c06390'
                              )
        disk4 = VolMgrMockDisk(item_id='d4',
                              bootable='false',
                              name='lun_1',
                              size='10G',
                              uuid='3000000021c06390'
                              )
        disk1.applied_properties['size']='5G'
        disk2.applied_properties['size']='5G'
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')

        sys1 = VolMgrMockSystem(item_id='s1')
        sys2 = VolMgrMockSystem(item_id='s2')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        pd1 = VolMgrMockPD(item_id='pd1', device_name='lun_0')
        pd2 = VolMgrMockPD(item_id='pd2', device_name='lun_1')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='storeg')

        # create relationships between model items
        sys1.disks.append(disk1)
        sys2.disks.append(disk2)
        sys1.disks.append(disk3)
        sys2.disks.append(disk4)
        node1.system = sys1
        node2.system = sys2
        vg.physical_devices.append(pd1)

        cluster.storage_profile = [sp]
        cluster.nodes = [node1, node2]

        # set model item states
        updated_items = [disk1,disk2]
        applied_items = [node1, node2, sys1, sys2, pd1, pd2, sp, vg, cluster]

        VolMgrMock._set_state_updated(updated_items)
        VolMgrMock._set_state_applied(applied_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.resize_disk_group_tasks(
            vg, cluster
        )

        # 3. Assert Code Outputs
        expected = [
            'Resize VxVM disk on volume group "storeg" - Disk: "lun_0"'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

        # in some cases we will have occurrences of the same message
        expected = ['_base_with_imported_dg']
        VolMgrMock.assert_task_call_types(self, expected, tasks)

        expected = [
                {'disk_name': 'wxvm_disk_30000000fc85c928_dev', 'disk_group': 'storeg', 'timeout': 299, 'ignore_unreachable': True, 'force_flag': 'True'}
        ]
        VolMgrMock.assert_task_kwargs(self, expected, tasks)
        disk1.applied_properties['size']='10240M'
        disk2.applied_properties['size']='10240M'
        tasks = self.plugin.vxvm_driver.resize_disk_group_tasks(
            vg, cluster
        )
        self.assertEqual(0, len(tasks))
        disk1.applied_properties['size']='10241M'
        disk2.applied_properties['size']='10241M'
        tasks = self.plugin.vxvm_driver.resize_disk_group_tasks(
            vg, cluster
        )
        self.assertEqual(0, len(tasks))
        disk1.applied_properties['size']='10239M'
        disk2.applied_properties['size']='10239M'
        tasks = self.plugin.vxvm_driver.resize_disk_group_tasks(
            vg, cluster
        )
        self.assertEqual(1, len(tasks))
        self.assertEqual(2, len(tasks[0].model_items))
        disks = self.plugin.vxvm_driver._get_all_updated_disks_from_cluster_node(cluster, pd1)
        i = 1
        for disk in disks:
            self.assertEqual("d%d" % i, disk.item_id)
            i+=1
        vg.physical_devices.append(pd2)
        updated_items = [disk1, disk2, disk3, disk4]
        VolMgrMock._set_state_updated(updated_items)
        disk3.applied_properties['size']='10239M'
        disk4.applied_properties['size']='10239M'
        tasks = self.plugin.vxvm_driver.resize_disk_group_tasks(
            vg, cluster
        )
        expected = [
            'Resize VxVM disk on volume group "storeg" - Disk: "lun_0"',
            'Resize VxVM disk on volume group "storeg" - Disk: "lun_1"'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

        # in some cases we will have occurrences of the same message
        expected = ['_base_with_imported_dg', '_base_with_imported_dg']
        VolMgrMock.assert_task_call_types(self, expected, tasks)

        expected = [
                {'disk_name': 'wxvm_disk_30000000fc85c928_dev', 'disk_group': 'storeg', 'timeout': 299, 'ignore_unreachable': True, 'force_flag': 'False'},
                {'disk_name': 'wxvm_disk_3000000021c06390_dev', 'disk_group': 'storeg', 'timeout': 299, 'ignore_unreachable': True, 'force_flag': 'False'}
        ]
        VolMgrMock.assert_task_kwargs(self, expected, tasks)

    def _test_create_fs_task(self, intended_snap_size):

        if intended_snap_size > 100 or intended_snap_size < 0:
            raise Exception

        # 1. Mock Up LITP Model

        # mock model items for testing
        node = VolMgrMockNode(item_id='node1', hostname='mn1')
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')

        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        fs = VolMgrMockFS(item_id='fs1', size='1G', mount_point='/mnt',
                          snap_size=intended_snap_size)

        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')

        # add relationships between the model items
        vg.file_systems.append(fs)
        vg.physical_devices.append(pd)
        sp.volume_groups.append(vg)
        cluster.storage_profile.append(sp)

        # set model item states
        initial_items = [cluster, node, sp, vg, fs, pd]
        VolMgrMock._set_state_initial(initial_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.initialize_volume_tasks(
            node, vg.item_id, fs
        )

        # 3. Assert Code Outputs
        expected = [
            'Create VxVM volume on node "mn1" - VOL: "fs1", VG: "vg1"',
            'Create VxFS file system on node "mn1" - FS: "fs1", VG: "vg1"',
            ('Prepare VxVM volume for snapshot on node "mn1" '
             '- VOL: "fs1", VG: "vg1"')
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def test_create_snapshot_of_varying_sizes(self):
        self._test_create_fs_task(100)
        self._test_create_fs_task(75)
        self._test_create_fs_task(50)
        self._test_create_fs_task(25)
        self._test_create_fs_task(0)

    def test_effective_fs_size(self):
        fs1 = VolMgrMockFS("mock", "2G", "/mnt", snap_size=100)
        fs2 = VolMgrMockFS("mock", "2G", "/mnt", snap_size=50)
        fs3 = VolMgrMockFS("mock", "2G", "/mnt", snap_size=0)
        fs4 = VolMgrMockFS("mock", "66G", "/mnt", snap_size=100)
        self.assertEqual(4117, VxvmDriver._effective_fs_size(fs1))
        self.assertEqual(3093, VxvmDriver._effective_fs_size(fs2))
        self.assertEqual(2069, VxvmDriver._effective_fs_size(fs3))
        self.assertEqual(135844, VxvmDriver._effective_fs_size(fs4))

    def __mock_cluster_disk_factory(self, number_of_disks):
        """
        Cluster mock up of LITP model introduced for story LITPCDS-4331

        Arguments: number_of_disks - integer number of disks can be [0, 1, 2]
        Returns  : mock objects, namely, a node, cluster, and volume_group
        """
        if number_of_disks > 2 or number_of_disks < 0:
            raise Exception

        last_item = number_of_disks

        # 1. Mock model items for testing
        disk_pool = [
            VolMgrMockDisk('hd1', 'hd1', size='10G', uuid='1', bootable=False),
            VolMgrMockDisk('hd2', 'hd2', size='5G', uuid='2', bootable=False)
        ]

        physical_device_pool = [
            VolMgrMockPD(item_id='pd1', device_name='hd1'),
            VolMgrMockPD(item_id='pd2', device_name='hd2')
        ]

        node = VolMgrMockNode('node1', 'node1')
        sys = VolMgrMockSystem(item_id='s1')
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        sp = VolMgrMockStorageProfile(item_id='sp', volume_driver='vxvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        fs = VolMgrMockFS(item_id='fs1', size='10G', mount_point='/mnt')

        # 2. Add relationships between the model items
        # each cluster requires at least 1 node, this is a vcs cluster
        cluster.nodes.append(node)
        cluster.storage_profile.append(sp)
        node.system = sys
        node.system.disks = disk_pool[0:last_item]
        sp.volume_groups.append(vg)
        vg.file_systems.append(fs)
        vg.physical_devices = physical_device_pool[0:last_item]

        # 3. Mock any queries and additional LITP functionality
        def mock_query(item_type, **kwargs):
            if item_type == 'storage-profile':
                volume_driver = kwargs['volume_driver']
                return [csp for csp in cluster.storage_profile
                        if csp.volume_driver == volume_driver]
            else:
                return []

        cluster.query = mock_query

        # 4. Set appropriate states for every model item
        model_items = [node, sys, cluster, sp, vg, fs]
        model_items.extend(disk_pool)
        model_items.extend(physical_device_pool)

        VolMgrMock._set_state_initial(model_items)

        return node, vg, cluster

    def __get_validate_vg_size_against_disks_error_message(
            self, disk_size, cluster_name, vg_total, fs_total, ss_total):

        return (
            "The total size ({disk_size} MB) of the disk(s) connected to "
            "cluster '{cluster_name}' is not large enough for volume group "
            "total requirement ({vg_total} MB), containing file systems "
            "{fs_total} MB and snapshots {ss_total} MB"
        ).format(
            disk_size=disk_size,
            cluster_name=cluster_name,
            vg_total=vg_total,
            fs_total=fs_total,
            ss_total=ss_total
        )


    def test_validate_for_removal_vg(self):

        # 1. Mock Up LITP Model
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()
        for sp in self.context.query('storage-profile'):
            for group in sp.volume_groups:
                if group.volume_group_name == "data-vg":
                    group.is_for_removal = lambda : True
            # 2. Test Code Functionality
            error = self.plugin.vxvm_driver.validate_for_removal_vg(sp, self.context)
            # 3. Assert Code Outputs
            message = 'Cannot remove volume-group "data-vg" because a snapshot exists.'
            expected = ValidationError(
                item_path="/volume-group/vg2", error_message=message
            )
            self.assertEqual(len(error), 1)
            self.assertEqual(expected, error[0])

    def test_validate_vg_size_against_disks_with_one_disk(self):

        # 1. Mock Up LITP Model
        node, volume_group, cluster = \
            self.__mock_cluster_disk_factory(number_of_disks=1)

        # 2. Test Code Functionality
        error = self.plugin.vxvm_driver._validate_vg_size_against_disks(
            cluster, node, volume_group
        )

        # 3. Assert Code Outputs
        message = self.__get_validate_vg_size_against_disks_error_message(
            disk_size='10240', cluster_name='c1',
            vg_total='20583', fs_total='10240', ss_total='10343'
        )
        expected = ValidationError(
            item_path=volume_group.get_vpath(), error_message=message
        )
        self.assertEqual(expected, error)

    def test_validate_vg_size_against_disks_with_multiple_disks(self):

        # 1. Mock Up LITP Model
        node, volume_group, cluster = \
            self.__mock_cluster_disk_factory(number_of_disks=2)

        # 2. Test Code Functionality
        error = self.plugin.vxvm_driver._validate_vg_size_against_disks(
            cluster, node, volume_group
        )

        # 3. Assert Code Outputs
        message = self.__get_validate_vg_size_against_disks_error_message(
            disk_size='15360', cluster_name='c1',
            vg_total='20583', fs_total='10240', ss_total='10343'
        )
        expected = ValidationError(
            item_path=volume_group.get_vpath(), error_message=message
        )
        self.assertEqual(expected, error)

    def test_validate_disk_sizes_with_no_disks(self):

        # 1. Mock Up LITP Model
        node, volume_group, cluster = \
            self.__mock_cluster_disk_factory(number_of_disks=0)

        # 2. Test Code Functionality
        errors = self.plugin.vxvm_driver.validate_disk_sizes(cluster)

        # 3. Assert Code Outputs
        self.assertEqual(0, len(errors))

    def test_validate_disk_sizes_with_one_disk(self):

        # 1. Mock Up LITP Model
        node, volume_group, cluster = \
            self.__mock_cluster_disk_factory(number_of_disks=1)

        # 2. Test Code Functionality
        errors = self.plugin.vxvm_driver.validate_disk_sizes(cluster)

        # 3. Assert Code Outputs
        self.assertEqual(1, len(errors))

        message = self.__get_validate_vg_size_against_disks_error_message(
            disk_size='10240', cluster_name='c1',
            vg_total='20583', fs_total='10240', ss_total='10343'
        )
        expected = ValidationError(
            item_path=volume_group.get_vpath(), error_message=message
        )
        self.assertTrue(expected in errors)

    def test_validate_disk_sizes_with_multiple_disks(self):

        # 1. Mock Up LITP Model
        node, volume_group, cluster = \
            self.__mock_cluster_disk_factory(number_of_disks=2)

        # 2. Test Code Functionality
        errors = self.plugin.vxvm_driver.validate_disk_sizes(cluster)

        # 3. Assert Code Outputs
        self.assertEqual(1, len(errors))

        message = self.__get_validate_vg_size_against_disks_error_message(
            disk_size='15360', cluster_name='c1',
            vg_total='20583', fs_total='10240', ss_total='10343'
        )
        expected = ValidationError(
            item_path=volume_group.get_vpath(), error_message=message
        )
        self.assertTrue(expected in errors)

    def _get_validate_pd_to_sp_mapping_error_message(self, pd, sp1, sp2):
        return ValidationError(
            item_path=pd.get_vpath(),
            error_message='The physical device "{pd}" is included in '
                          'multiple storage profiles "{sp1} and {sp2}"'.format(
                pd=pd.device_name, sp1=sp1.item_id, sp2=sp2.item_id
            )
        )

    def test_validate_pd_to_storage_profile_mapping(self):

        # 1. Mock model items for testing

        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')

        # we have two disks
        disk1 = VolMgrMockDisk(
            'hd1', 'hd1', size='10G', uuid='1234', bootable=False
        )
        disk2 = VolMgrMockDisk(
            'hd2', 'hd2', size='5G', uuid='5678', bootable=False
        )
        disks = [disk1, disk2]

        # and two physical devices
        pd1 = VolMgrMockPD(item_id='pd1', device_name='hd1')
        pd2 = VolMgrMockPD(item_id='pd2', device_name='hd2')
        physical_devices = [pd1, pd2]

        n1 = VolMgrMockNode('node1', 'node1')
        n2 = VolMgrMockNode('node2', 'node2')
        nodes = [n1, n2]

        s1 = VolMgrMockSystem(item_id='s1')
        s2 = VolMgrMockSystem(item_id='s2')
        systems = [s1, s2]

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        sp2 = VolMgrMockStorageProfile(item_id='sp2', volume_driver='vxvm')
        storage_profiles = [sp1, sp2]

        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        vg2 = VolMgrMockVG(item_id='vg2', volume_group_name='vg2')
        volume_groups = [vg1, vg2]

        fs1 = VolMgrMockFS(item_id='fs1', size='10G', mount_point='/fs1_mnt')
        fs2 = VolMgrMockFS(item_id='fs1', size='10G', mount_point='/fs2_mnt')
        file_systems = [fs1, fs2]

        # sdd relationships between the model items
        # each cluster requires at least 1 node, this is a vcs cluster
        cluster.nodes.extend(nodes)
        cluster.storage_profile.extend(storage_profiles)
        s1.disks.extend(disks)
        s2.disks.extend(disks)
        n1.system = s1
        n2.system = s2
        vg1.file_systems.append(fs1)
        vg2.file_systems.append(fs2)
        sp1.volume_groups.append(vg1)
        sp2.volume_groups.append(vg2)
        vg1.physical_devices.extend(physical_devices)
        vg2.physical_devices.extend(physical_devices)

        # mock any queries and additional LITP functionality
        def mock_query(item_type, **kwargs):
            if item_type == 'storage-profile':
                volume_driver = kwargs['volume_driver']
                return [csp for csp in cluster.storage_profile
                        if csp.volume_driver == volume_driver]
            elif item_type == 'physical-device':
                return physical_devices
            else:
                return []

        api_context = VolMgrMockContext()
        api_context.query = mock_query

        # set appropriate states for every model item
        items = [cluster]
        items.extend(nodes)
        items.extend(systems)
        items.extend(disks)
        items.extend(physical_devices)
        items.extend(storage_profiles)
        items.extend(volume_groups)
        items.extend(file_systems)

        VolMgrMock._set_state_initial(items)

        # 2. Run LITP Code
        errors = self.plugin.vxvm_driver.validate_pd_to_sp_mapping(api_context)

        # 3. Assert Outputs
        expected = [
            self._get_validate_pd_to_sp_mapping_error_message(pd1, sp1, sp2),
            self._get_validate_pd_to_sp_mapping_error_message(pd2, sp1, sp2)
        ]
        VolMgrMock.assert_validation_errors(self, expected, errors)

    def test_create_full_vg(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='hd0',
                              size='50G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sys = VolMgrMockSystem(item_id='s1')
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')
        sp = VolMgrMockStorageProfile(item_id='sp', volume_driver='vxvm')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        fs = VolMgrMockFS(item_id='fs1', size='10G',
                          snap_size=10, mount_point='/fs1')

        # add relationships between the model items
        sys.disks.append(disk)
        node.system = sys
        sp.volume_groups.append(vg)
        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)
        cluster.storage_profile.append(sp)
        cluster.nodes.append(node)

        context = VolMgrMockContext()

        # set appropriate states for every model item
        initial_items = [sp, vg, fs]
        applied_items = [pd, node, sys, cluster, disk]
        VolMgrMock._set_state_initial(initial_items)
        VolMgrMock._set_state_applied(applied_items)

        # mock any queries and additional LITP functionality
        dg_hostname = {
            'vg1': (node, {'snaps': ['L_fs1_']})
        }
        self.plugin.vxvm_driver.get_dg_hostname = \
            MagicMock(return_value=dg_hostname)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.gen_tasks_for_storage_profile(
            context, cluster, sp
        )

        # 3. Assert Code Outputs
        expected = [
            'Clear SCSI-3 registration keys from disk "hd0" on node "mn1"',
            'Setup VxVM disk "hd0" on node "mn1"',
            'Create VxVM disk group "vg1" on node "mn1"',
            'Create VxVM volume on node "mn1" - VOL: "fs1", VG: "vg1"',
            'Create VxFS file system on node "mn1" - FS: "fs1", VG: "vg1"',
            ('Prepare VxVM volume for snapshot on node '
             '"mn1" - VOL: "fs1", VG: "vg1"')
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def test_add_new_disks_tasks(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sys = VolMgrMockSystem(item_id='s1')
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        context = VolMgrMockContext()

        new_disks = [
            VolMgrMockDisk(item_id='d1',
                           bootable='true',
                           name='hd0',
                           size='50G',
                           uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304'),
            VolMgrMockDisk(item_id='d2',
                           bootable='false',
                           name='hd1',
                           size='50G',
                           uuid='ATA_VBOX_HARDDISK_VB24150799-28e77305')
        ]

        new_pds = [
            VolMgrMockPD(item_id='pd1', device_name='hd0'),
            VolMgrMockPD(item_id='pd2', device_name='hd1')
        ]

        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        # add relationships between the model items
        node.system = sys
        sys.disks.extend(new_disks)
        vg.physical_devices.extend(new_pds)
        sp.volume_groups.append(vg)
        cluster.storage_profile.append(sp)
        cluster.nodes.append(node)

        # set appropriate states for every model item
        # everything should be applied except for the new physical devices
        # and new disks
        initial_items = []
        initial_items.extend(new_disks)
        initial_items.extend(new_pds)
        applied_items = [node, sys, cluster, vg, sp]
        applied_items.extend(new_disks)

        VolMgrMock._set_state_initial(initial_items)
        VolMgrMock._set_state_applied(applied_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver._add_new_disks_tasks(
            node, vg, new_pds, cluster, context
        )

        # 3. Assert Code Outputs
        expected = [
            'Clear SCSI-3 registration keys from disk "hd0" on node "mn1"',
            'Setup VxVM disk "hd0" on node "mn1"',
            'Clear SCSI-3 registration keys from disk "hd1" on node "mn1"',
            'Setup VxVM disk "hd1" on node "mn1"',
            'Extend VxVM disk group "vg_root" on node "mn1" with new disk "hd0"',
            'Extend VxVM disk group "vg_root" on node "mn1" with new disk "hd1"',
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def test_add_new_disks_tasks_openstack_cloud_deploy(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sys = VolMgrMockSystem(item_id='s1')
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        context = VolMgrMockContext()

        context.config_manager.global_properties[0].key = 'enm_deployment_type'
        context.config_manager.global_properties[0].value = 'vLITP_ENM_On_Rack_Servers'

        new_disks = [
            VolMgrMockDisk(item_id='d1',
                           bootable='true',
                           name='hd0',
                           size='50G',
                           uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304'),
            VolMgrMockDisk(item_id='d2',
                           bootable='false',
                           name='hd1',
                           size='50G',
                           uuid='ATA_VBOX_HARDDISK_VB24150799-28e77305')
        ]

        new_pds = [
            VolMgrMockPD(item_id='pd1', device_name='hd0'),
            VolMgrMockPD(item_id='pd2', device_name='hd1')
        ]

        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg_root')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        # add relationships between the model items
        node.system = sys
        sys.disks.extend(new_disks)
        vg.physical_devices.extend(new_pds)
        sp.volume_groups.append(vg)
        cluster.storage_profile.append(sp)
        cluster.nodes.append(node)

        # set appropriate states for every model item
        # everything should be applied except for the new physical devices
        # and new disks
        initial_items = []
        initial_items.extend(new_disks)
        initial_items.extend(new_pds)
        applied_items = [node, sys, cluster, vg, sp]
        applied_items.extend(new_disks)

        VolMgrMock._set_state_initial(initial_items)
        VolMgrMock._set_state_applied(applied_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver._add_new_disks_tasks(
            node, vg, new_pds, cluster, context
        )

        # 3. Assert Code Outputs
        expected = [
            'Setup VxVM disk "hd0" on node "mn1"',
            'Setup VxVM disk "hd1" on node "mn1"',
            'Extend VxVM disk group "vg_root" on node "mn1" with new disk "hd0"',
            'Extend VxVM disk group "vg_root" on node "mn1" with new disk "hd1"',
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks, equal=True)

    def test_add_disk_to_group_task(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='hd0',
                              size='50G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        pd_pool = [
            VolMgrMockPD(item_id="pd1", device_name="hd1"),
            VolMgrMockPD(item_id="pd2", device_name="hd2")
        ]
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='my_vg')

        cluster = VolMgrMockVCSCluster(item_id='/my/clus', cluster_type='sfha')

        sp.volume_groups.append(sp)
        cluster.storage_profile.append(sp)

        # 2. Test Code Functionality
        task = self.plugin.vxvm_driver._add_disk_to_group_task(
            node, vg, disk, cluster.item_id, pd_pool[0]
        )
        # 3. Assert Code Outputs
        expected = \
            'Extend VxVM disk group "my_vg" on node "mn1" with new disk "hd0"'
        self.assertEqual(expected, task.description)

    def test_create_vxfs_file_system(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        disk = VolMgrMockDisk(item_id='d1',
                              bootable='true',
                              name='hd0',
                              size='50G',
                              uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304')

        sys = VolMgrMockSystem(item_id='s1')
        sys.disks.append(disk)

        fs = VolMgrMockFS(item_id='fs1', size='10G',
                          snap_size='10', mount_point='/fs1')
        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        sp = VolMgrMockStorageProfile(item_id='sp', volume_driver='vxvm')
        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        context = VolMgrMockContext()

        # add relationships between the model items
        node.system = sys
        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)
        sp.volume_groups.append(vg)
        cluster.storage_profile.append(sp)
        cluster.nodes.append(node)

        # mock any queries and additional LITP functionality
        dg_hostname = {
            "vg1": (node, {'snaps': ['L_fs1_']})
        }
        self.plugin.vxvm_driver.get_dg_hostname = \
            MagicMock(return_value=dg_hostname)

        # set appropriate states for every model item
        initial_items = [sp, vg, fs]
        applied_items = [cluster, node, sys, disk, pd]

        VolMgrMock._set_state_initial(initial_items)
        VolMgrMock._set_state_applied(applied_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.gen_tasks_for_storage_profile(
            context, cluster, sp
        )

        # 3. Assert Code Outputs
        expected = [
            'Create VxVM volume on node "mn1" - VOL: "fs1", VG: "vg1"',
            'Create VxFS file system on node "mn1" - FS: "fs1", VG: "vg1"',
            ('Prepare VxVM volume for snapshot on node "mn1"'
             ' - VOL: "fs1", VG: "vg1"')
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def test_is_service_for_redeploy(self):
        #test 1
        service1 = Mock(standby="0", node_list="n1")
        service1.is_initial.return_value = True
        service1.applied_properties_determinable = True
        service1.applied_properties = {'standby': "0", "node_list": "n1"}
        self.assertEqual(False, self.plugin.vxvm_driver.is_service_for_redeploy(service1))

        #test 2
        service2 = Mock(standby="0", node_list="n1")
        service2.is_for_removal.return_value = True
        service2.is_initial.return_value = False
        service2.applied_properties_determinable = True
        service2.applied_properties = {'standby': "0", "node_list": "n1"}
        self.assertEqual(False, self.plugin.vxvm_driver.is_service_for_redeploy(service2))

        #test3
        service3 = Mock(standby="0", node_list="n1")
        service3.is_for_removal.return_value = False
        service3.is_initial.return_value = False
        service3.applied_properties_determinable = True
        service3.applied_properties = {'standby': "0", "node_list": "n1"}
        self.assertEqual(False, self.plugin.vxvm_driver.is_service_for_redeploy(service3))

        #test 4
        service4 = Mock(standby="0", node_list="n1")
        service4.is_for_removal.return_value = False
        service4.is_initial.return_value = False
        service4.applied_properties_determinable = False
        service4.applied_properties = {'standby': "0", "node_list": "n1"}
        self.assertEqual(True, self.plugin.vxvm_driver.is_service_for_redeploy(service4))

        #test 5 Exapnsion
        service5 = Mock(standby="0", node_list="n1,n2,n3")
        service5.is_for_removal.return_value = False
        service5.is_initial.return_value = False
        service5.applied_properties_determinable = True
        service5.applied_properties = {'active': "2", 'standby': "0", "node_list": "n1,n2"}
        self.assertEqual(False, self.plugin.vxvm_driver.is_service_for_redeploy(service5))

        #test 6 Migration
        service6 = Mock(standby="0", node_list="n3,n4")
        service6.is_for_removal.return_value = False
        service6.is_initial.return_value = False
        service6.applied_properties_determinable = True
        service6.applied_properties = {'active': "2", 'standby': "0", "node_list": "n1,n2"}
        self.assertEqual(True, self.plugin.vxvm_driver.is_service_for_redeploy(service6))

    def test_remove_vxvm_volume_group(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        node = VolMgrMockNode(item_id="n1", hostname="mn1")
        sys = VolMgrMockSystem(item_id='s1')

        pd = VolMgrMockPD(item_id='pd1', device_name='hd0')
        disk = VolMgrMockDisk(
            item_id='d1',
            bootable='true',
            name='hd0',
            size='50G',
            uuid='ATA_VBOX_HARDDISK_VB24150799-28e77304'
        )

        sp = VolMgrMockStorageProfile(item_id="sp", volume_driver="vxvm")
        vg = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        fs = VolMgrMockFS(item_id='fs1', size='10G', mount_point='/fs1')

        cluster = VolMgrMockVCSCluster(
            item_id="c1", cluster_type="sfha")
        context = VolMgrMockContext()

        # add relationships between the model items
        node.system = sys
        sys.disks.append(disk)

        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)
        sp.volume_groups.append(vg)

        cluster.storage_profile.append(sp)
        cluster.nodes.append(node)

        # mock any queries and additional LITP functionality
        dg_hostname_w_snap = {
            'vg1': (node, {'snaps': ['L_fs1_']})
        }
        dg_hostname = {
            'vg1': (node, {'snaps': []})
        }
        self.plugin.vxvm_driver.get_dg_hostname = \
            MagicMock(return_value=dg_hostname)

        node.get_cluster = lambda: cluster
        context.snapshot_action = lambda: 'create'

        # set appropriate states for every model item
        # everything should be applied and you want to remove the vg
        applied_items = [cluster, node, sys, pd, disk, sp, fs]
        for_removal_items = [vg]

        VolMgrMock._set_state_applied(applied_items)
        VolMgrMock._set_state_for_removal(for_removal_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.gen_tasks_for_storage_profile(
            context, cluster, sp
        )

        # 3. Assert Code Outputs
        expected = [
            'Stop VxVM volumes from disk group "vg1" on node "mn1"',
            'Destroy VxVM disk group "vg1" on node "mn1"',
            'Unsetup VxVM disk "hd0" on node "mn1"'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def generate_mock_snapshot_model_for_data_manager(self):
        n1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        n2 = VolMgrMockNode(item_id='n2', hostname='mn2')
        n3 = VolMgrMockNode(item_id='n3', hostname='mn3')

        nodes = [n1, n2, n3]

        fs1 = VolMgrMockFS(item_id='fs1', size='1G', mount_point='/foo')
        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='data-vg')

        fs2 = VolMgrMockFS(item_id='fs2', size='1G', mount_point='/bar')
        vg2 = VolMgrMockVG(item_id='vg2', volume_group_name='db-vg')

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        file_systems = [fs1, fs2]
        volume_groups = [vg1, vg2]
        storage_profiles = [sp1]

        vg1.file_systems = [fs1]
        vg2.file_systems = [fs2]

        sp1.volume_groups = volume_groups

        c1 = VolMgrMockVCSCluster(item_id='c1')
        c1.nodes = nodes
        c1.storage_profile = storage_profiles

        clusters = [c1]

        items = nodes + file_systems + volume_groups \
            + storage_profiles + clusters

        VolMgrMock._set_state_applied(items)

        # set up model items
        snapshot_model = VolMgrMockContext()

        def mock_query(item_type, **properties):

            look_up = {
                'node': nodes,
                'file-system': file_systems,
                'volume-group': volume_groups,
                'storage-profile': storage_profiles,
                'vcs-cluster': clusters,
                'snapshot-base': ['snap1']
            }

            return look_up.get(item_type, [])

        snapshot_model.query = mock_query
        c1.query = mock_query

        return snapshot_model

    def test_check_restores_in_progress(self):
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()

        data_manager = VxvmSnapshotDataMgr(driver=self.plugin.vxvm_driver, context=self.context)
        self.plugin.vxvm_driver.check_restores_in_progress(data_manager)

    def test_remove_snapshot(self):
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()
        data_mgr = VxvmSnapshotDataMgr(driver=self.plugin.vxvm_driver,
                                       context=self.context)
        volume_groups = self.context.query('volume-group')
        file_systems = self.context.query('file-system')
        vgs_for_filter = [vg.get_vpath() for vg in volume_groups]
        fs_for_filter = [fs.item_id for fs in file_systems]
        data_mgr.set_filters(vg_vpaths=vgs_for_filter, fs_ids=fs_for_filter)

        # Expecting error as no active node has been set
        self.assertRaises(CallbackExecutionException,
                          self.plugin.vxvm_driver.remove_snapshot,
                          self.context, data_mgr)

        # Set active node, so expecting it to not fail
        vg = data_mgr.get_all_volume_groups()[0]
        data_mgr.set_active_node(vg, 'the_active_node')
        self.plugin.vxvm_driver.remove_snapshot(self.context, data_mgr)

    def test_check_snapshots_exist(self):
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()
        data_mgr = VxvmSnapshotDataMgr(driver=self.plugin.vxvm_driver,
                                       context=self.context)
        vg = data_mgr.get_all_volume_groups()[0]
        data_mgr.set_active_node(vg, 'the_active_node')

        # Snapshot missing, so will raise an error

        self.assertRaises(CallbackExecutionException,
                          self.plugin.vxvm_driver.check_snapshots_exist,
                          data_mgr)

    def test_check_snapshots_valid(self):
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()
        data_mgr = VxvmSnapshotDataMgr(driver=self.plugin.vxvm_driver,
                                       context=self.context)
        vg = data_mgr.get_all_volume_groups()[0]
        data_mgr.set_active_node(vg, 'the_active_node')
        self.plugin.vxvm_driver.check_snapshots_valid(data_mgr)

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test__remove_snapshot_tasks(self):
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()
        tasks = self.plugin.vxvm_driver._remove_snapshot_tasks(self.context)
        expected = ['Check that an active node exists for each VxVM volume group on cluster(s) "c1"',
                    'Check VxVM snapshots are currently not being restored',
                    'Remove VxVM deployment snapshot']
        for t in tasks:
            # Need to do fuzzy matching here because remove snapshot message is incomplete
            self.assertTrue(exp in t.description for exp in expected)

    def test_check_restore_completed(self):
        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:
            # Working as expected
            output = { "n1" : 'on' }
            errors = { "errors" : ["this", "is", "an", "err"] }
            mock_rpc.return_value = (output, errors)
            mock_rpc.return_value = (output, errors)
            self.assertRaises(PluginError,
                              self.plugin.vxvm_driver.check_restore_completed,
                              VolMgrMockContext(), "n1", "vg", "vg")

    def test_check_disk_groups_active_nodes(self):

        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = \
            lambda : self.generate_mock_snapshot_model_for_data_manager()

        self.data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=self.context)

        clusters = self.data_manager.get_clusters()
        c1 = clusters[0]

        # Case 1. cluster is reachable
        for host in c1.get_hostnames():
            self.data_manager._set_node_reachable(c1, host)

        # avoid checking imports
        self.data_manager.get_not_imported_volume_groups = \
            lambda x: []

        # this should succeed
        self.plugin.vxvm_driver.check_disk_groups_active_nodes(
            self.data_manager)

        # Case 2. cluster is not reachable
        for host in c1.get_hostnames():
            self.data_manager._set_node_unreachable(c1, host)

        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.check_disk_groups_active_nodes,
            self.data_manager
        )

        try:
            self.plugin.vxvm_driver.check_disk_groups_active_nodes(
                self.data_manager)

        except CallbackExecutionException as actual:

            nodes = VolMgrUtils.format_list(
                c1.get_hostnames(), quotes_char='double')

            error = 'Cluster "%s" node(s) %s did not respond' % (c1.id, nodes)

            expected = CallbackExecutionException(error)

            self.assertEquals(str(expected), str(actual))

    @patch('volmgr_plugin.drivers.vxvm.VxvmDriver.check_dcoregionsz')
    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test_create_snapshot(self, check_dcoregionsz):

        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = \
            lambda : self.generate_mock_snapshot_model_for_data_manager()
        self.data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=self.context)

        vg = self.data_manager.get_all_volume_groups()[0]
        fs = vg.file_systems[0]

        self.data_manager.set_filters(vg_vpaths=[vg.id], fs_ids=[fs.id])

        # 1. where _create_fs_snapshot returns OK
        with patch.object(VxvmDriver, '_create_fs_snapshot') as mock_rpc:

            # assume the rpc call returns OK
            mock_rpc.return_value = True

            # 1.1 positive case, get_active node returns an active node
            # i.e. the happy path

            self.data_manager.set_active_node(vg, 'the_active_node')

            self.plugin.vxvm_driver.create_snapshot(
                self.context, self.data_manager)

            # 1.2 negative case, get_active_node returns None
            self.data_manager.set_active_node(vg, None)

            try:
                self.plugin.vxvm_driver.create_snapshot(
                    self.context, self.data_manager
                )
            except CallbackExecutionException as actual:
                error_msg = 'No active node for disk group "%s"' % vg.name
                expected = CallbackExecutionException(error_msg)
                self.assertEquals(str(expected), str(actual))

            # 1.3 negative case, file system is not present
            self.data_manager.set_active_node(vg, 'the_active_node')

        # 2. where _create_fs_snapshot raises a CallbackExecutionException

        with patch.object(VxvmDriver, '_create_fs_snapshot') as mock_rpc:

            # an exception has been raised
            mock_rpc.side_effect = CallbackExecutionException("mock raise")

            # 1.1 positive case, get_active node returns an active node
            # i.e. the happy path

            self.data_manager.set_active_node(vg, 'the_active_node')

            self.assertRaises(
                CallbackExecutionException,
                self.plugin.vxvm_driver.create_snapshot,
                self.context,
                self.data_manager
            )

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test__create_snapshot_tasks(self):
        # context = VolMgrMockContext()
        # context.snapshot_model = lambda: VolMgrMockContext()
        context = self.generate_mock_snapshot_model_for_data_manager()
        context.snapshot_model = \
            lambda : self.generate_mock_snapshot_model_for_data_manager()

        tasks = self.plugin.vxvm_driver._create_snapshot_tasks(context)
        expected = ['Create VxVM deployment snapshot "L_fs1_" for cluster "c1", volume group "data-vg"',
                    'Create VxVM deployment snapshot "L_fs2_" for cluster "c1", volume group "db-vg"']
        for exp, task in zip(expected, tasks):
            self.assertEqual(exp, task.description)

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test_restore_snapshot(self):

        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = \
            lambda : self.generate_mock_snapshot_model_for_data_manager()

        self.data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=self.context)

        vg = self.data_manager.get_all_volume_groups()[0]
        fs = vg.file_systems[0]

        self.data_manager.set_filters(vg_vpaths=[vg.id], fs_ids=[fs.id])

        def set_file_system_present(vg, fs, snapshot_name):
            vg_data = self.data_manager.data['volume_groups'][vg.id]
            vg_data['file_systems'][fs.id]['present'] = snapshot_name

        # 1. where _restore_fs_snapshot returns OK
        with patch.object(VxvmDriver, '_restore_fs_snapshot') as mock_rpc:

            # assume the rpc call returns OK
            mock_rpc.return_value = True

            # 1.1 positive case, get_active node returns an active node
            # i.e. the happy path

            self.data_manager.set_active_node(vg, 'the_active_node')

            set_file_system_present(vg, fs, 'L_snap_')

            self.plugin.vxvm_driver.restore_snapshot(
                self.context, self.data_manager)

            # 1.2 negative case, get_active_node returns None
            self.data_manager.set_active_node(vg, None)

            try:
                self.plugin.vxvm_driver.restore_snapshot(
                    self.context, self.data_manager
                )
            except CallbackExecutionException as actual:
                error_msg = 'No active node for disk group "%s"' % vg.name
                expected = CallbackExecutionException(error_msg)
                self.assertEquals(str(expected), str(actual))

            # 1.3 negative case, file system is not present
            self.data_manager.set_active_node(vg, 'the_active_node')

            try:
                self.plugin.vxvm_driver.restore_snapshot(
                    self.context, self.data_manager)
            except CallbackExecutionException as actual:
                error_msg = 'No snapshot present for file system "%s"' % fs.id
                expected = CallbackExecutionException(error_msg)
                self.assertEquals(str(expected), str(actual))

        # 2. where _restore_fs_snapshot raises a CallbackExecutionException

        with patch.object(VxvmDriver, '_restore_fs_snapshot') as mock_rpc:

            # an exception has been raised
            mock_rpc.side_effect = CallbackExecutionException("mock raise")

            # 1.1 positive case, get_active node returns an active node
            # i.e. the happy path

            self.data_manager.set_active_node(vg, 'the_active_node')
            set_file_system_present(vg, fs, 'L_some_snap_name_')

            self.assertRaises(
                CallbackExecutionException,
                self.plugin.vxvm_driver.restore_snapshot,
                self.context,
                self.data_manager
            )

        # LITPCDS-12803 - Test modified task description
        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()
        tasks = self.plugin.vxvm_driver._restore_snapshot_tasks(self.context)
        expected = ['Check that all nodes are reachable and an active node exists for each VxVM volume group on cluster(s) "c1"',
                    'Check VxVM snapshots are present on cluster(s) "c1"',
                    'Check VxVM snapshots are valid on cluster(s) "c1"',
                    'Restore VxVM deployment snapshot "L_fs1_" for cluster ' + \
                    '"c1", volume group "data-vg"',
                    'Restore VxVM deployment snapshot "L_fs2_" for cluster ' + \
                    '"c1", volume group "db-vg"']
        for t in tasks:
            self.assertTrue(exp in t.description for exp in expected)

    def test__restore_fs_snapshot(self):
        with patch.object(RpcCommandProcessorBase,
                         'execute_rpc_and_process_result') as mock_rpc:

            # Working as expected
            output = { "n1" : 'on' }
            errors = { "errors" : ["this", "is", "an", "err"] }

            mock_rpc.return_value = (output, errors)

            vg_data = {
                'vg_name' : 'my_vg',
                'active_node': '',
                'file_systems' : {},
                'cluster_id': '',
                'errors_list': []
            }

            vg = VolumeGroupData('/volume-group/vg1', vg_data)

            fs_data = {
                'snapshot_name' : '',
                'present' : '',
                'errors_list' : '',
                'valid' : '',
                'restore_in_progress': '',
                'snapshot_size': '',
                'snap_external': 'false'
            }

            fs = FileSystemData('fs1', fs_data)

            self.assertRaises(CallbackExecutionException,
                              self.plugin.vxvm_driver._restore_fs_snapshot,
                              VolMgrMockContext(), vg, fs)

    # def test_check_snapshots_exist(self):
    #     self.context = self.generate_mock_snapshot_model_for_data_manager()
    #     self.context.snapshot_model = lambda : self.generate_mock_snapshot_model_for_data_manager()

    #     self.data_manager = VxvmSnapshotDataMgr(context=self.context)
    #     self.plugin.vxvm_driver.check_snapshots_exist(self.data_manager)
    #     self.assertTrue(False)

    def test_VxvmSnapshotReport(self):
        snap_report = VxvmSnapshotReport()

        vg_data = {
                'vg_name' : 'my_vg',
                'active_node': '',
                'file_systems' : {},
                'cluster_id': '',
                'errors_list': []
            }

        vg = VolumeGroupData('/volume-group/vg1', vg_data)

        fs_data = {
            'snapshot_name' : '',
            'present' : '',
            'errors_list' : '',
            'valid' : '',
            'restore_in_progress': '',
            'snapshot_size': '',
            'snap_external': 'false'
            }

        fs = FileSystemData('fs1', fs_data)

        snap_report.add_snapshot(vg, fs)
        report_txt = snap_report.get_snapshots_txt()
        exp1 = 'Volume Group {0}, File-system {1}'.format(vg.name, fs.id)
        self.assertEquals(exp1, report_txt)

        # Test with two missing snapshots
        vg2_data = {
                'vg_name' : 'vg2',
                'active_node': '',
                'file_systems' : {},
                'cluster_id': '',
                'errors_list': []
            }

        vg2 = VolumeGroupData('/volume-group/vg2', vg2_data)

        fs2_data = {
            'snapshot_name' : '',
            'present' : '',
            'errors_list' : '',
            'valid' : '',
            'restore_in_progress': '',
            'snapshot_size': '',
            'snap_external': 'false'
            }

        fs2 = FileSystemData('fs2', fs2_data)

        snap_report.add_snapshot(vg2, fs2)
        report_txt = snap_report.get_snapshots_txt()
        exp2 = 'Volume Group {0}, File-system {1}'.format(vg2.name, fs2.id)
        exp = exp2 + ", " +exp1
        self.assertEquals(exp, report_txt)

        report_str = str(snap_report)
        exp_str = 'VxvmSnapshotReport({0})'.format(exp)
        self.assertEquals(exp_str, report_str)
        self.assertEquals(exp_str, repr(snap_report))

    def test_disk_fact_name(self):

        uuid = "EMC_093244123443-123233"
        disk = VolMgrMockDisk("hd1", "hd1", "10G", uuid, True)

        out = self.plugin.vxvm_driver.get_disk_fact_name(disk.uuid)
        self.assertEqual(out, "wxvm_disk_emc_093244123443_123233_dev")

        uuid2 = "093244123443123233"
        disk2 = VolMgrMockDisk("hd1", "hd1", "10G", uuid2, True)

        out = self.plugin.vxvm_driver.get_disk_fact_name(disk2.uuid)
        self.assertEqual(out, "wxvm_disk_093244123443123233_dev")

    def test_delete_vg(self):

        # 1. Mock Up LITP Model
        # mock model items for testing
        node = VolMgrMockNode(item_id='n1', hostname='mn1')
        sys = VolMgrMockSystem(item_id='s1')
        disk = VolMgrMockDisk(
            item_id='hd1', name='hd1', size='10G', uuid='123', bootable=False
        )
        context = VolMgrMockContext()
        cluster = VolMgrMockVCSCluster('c1')
        sp = VolMgrMockStorageProfile('sp2', 'vxvm')
        vg = VolMgrMockVG('vg1', 'vg1')
        fs = VolMgrMockFS('fs1', '10G', '100')
        pd = VolMgrMockPD('hd1', 'hd1')

        # add relationships between the model items
        sys.disks.append(disk)
        node.system = sys
        cluster.nodes.append(node)
        vg.physical_devices.append(pd)
        vg.file_systems.append(fs)
        cluster.storage_profile.append(sp)
        sp.volume_groups.append(vg)
        context.snapshot_action = lambda: 'remove'

        # mock any queries and additional LITP functionality
        node.get_cluster = MagicMock()
        node.get_cluster.vpath.return_value = cluster

        get_dg_result_w_snap = {
            'vg1': (node, {'snaps': ["L_fs1_"]})
        }
        get_dg_result = {
            'vg1': (node, {'snaps': []})
        }

        self.plugin.vxvm_driver._get_cluster_node = \
            MagicMock(return_value=node)

        self.plugin.vxvm_driver._get_disk_from_cluster_node = \
            MagicMock(return_value=disk)

        self.plugin.vxvm_driver.get_dg_hostname = \
            MagicMock(return_value=get_dg_result)

        self.plugin.vxvm_driver._get_existing_snapshots = \
            MagicMock(return_value="0")

        # set appropriate states for every model item
        applied_items = [cluster, node, sys, disk, sp, fs, pd]
        for_removal_items = [vg]

        VolMgrMock._set_state_applied(applied_items)
        VolMgrMock._set_state_for_removal(for_removal_items)

        # 2. Test Code Functionality
        tasks = self.plugin.vxvm_driver.gen_tasks_for_storage_profile(
            context, cluster, sp
        )

        # 3. Assert Code Outputs
        expected = [
            'Stop VxVM volumes from disk group "vg1" on node "mn1"',
            'Destroy VxVM disk group "vg1" on node "mn1"',
            'Unsetup VxVM disk "hd1" on node "mn1"'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def test_condense_name(self):
        self.assertEqual(
            "_b73f8942", VxvmDriver._condense_name("vcs_vxvm_ut", 10)
        )
        self.assertEqual(
            "vcs_vxvm_ut", VxvmDriver._condense_name("vcs_vxvm_ut", 20)
        )
        self.assertEqual(
            "vcs_vxvm_ut", VxvmDriver._condense_name("vcs_vxvm_ut")
        )

    def __mock_cluster_with_fencing_disk(self):
        # mock model items for testing
        cluster = VolMgrMockVCSCluster(
            item_id='c1', cluster_type='sfha'
        )

        nodes = [
            VolMgrMockNode(item_id='node1', hostname='mn1'),
            VolMgrMockNode(item_id='node2', hostname='mn2'),
            VolMgrMockNode(item_id='node3', hostname='mn3'),
        ]

        fencing_disk = VolMgrMockDisk(
            item_id='dsk1',
            name='dsk1',
            size='10G',
            uuid='myuuid123',
            bootable=False
        )

        # add relationships between the model items
        cluster.nodes.extend(nodes)
        cluster.fencing_disks.append(fencing_disk)

        return cluster, nodes, fencing_disk

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test_gen_tasks_for_fencing_disks_case_1_disk_is_initial(self):

        cluster, nodes, fencing_disk = self.__mock_cluster_with_fencing_disk()

        # set appropriate states for every model item
        initial_items = [cluster, fencing_disk]
        initial_items.extend(nodes)

        VolMgrMock._set_state_initial(initial_items)

        tasks = self.plugin.vxvm_driver.gen_tasks_for_fencing_disks(cluster)

        self.assertEqual(1, len(tasks))
        self.assertEqual(
            'Setup VxVM coordinator disk group "vxfencoorddg_c1"',
            tasks[0].description
        )

    def test_gen_tasks_for_fencing_disks_case_2_disk_is_applied(self):

        cluster, nodes, fencing_disk = self.__mock_cluster_with_fencing_disk()

        applied_items = [cluster, fencing_disk]
        applied_items.extend(nodes)

        VolMgrMock._set_state_applied(applied_items)

        tasks = self.plugin.vxvm_driver.gen_tasks_for_fencing_disks(cluster)
        VolMgrMock.assert_task_descriptions(self, [], tasks)

    def test_gen_task_for_fencing_disks_case_3_no_disks(self):

        cluster, nodes, fencing_disk = self.__mock_cluster_with_fencing_disk()

        cluster.fencing_disks = []

        initial_items = [cluster]
        initial_items.extend(nodes)

        VolMgrMock._set_state_initial(initial_items)

        tasks = self.plugin.vxvm_driver.gen_tasks_for_fencing_disks(cluster)
        VolMgrMock.assert_task_descriptions(self, [], tasks)

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask')
    def test_gen_tasks_for_fencing_disks_model_items(self, callback_patch):

        model_items_mock = Mock(['add'])
        callback_task = Mock(model_items=model_items_mock)
        callback_patch.return_value = callback_task

        # mock model items for testing
        nodes = [
            VolMgrMockNode(item_id='n1', hostname='mn1'),
            VolMgrMockNode(item_id='n2', hostname='mn2')
        ]

        fencing_disks = [
            VolMgrMockDisk(
                item_id='fdsk1',
                name='fdsk1',
                size='10G',
                uuid='30000000fc85c926',
                bootable=False
            ),
            VolMgrMockDisk(
                item_id='fdsk2',
                name='fdsk2',
                size='10G',
                uuid='30000000fc85c927',
                bootable=False
            ),
            VolMgrMockDisk(
                item_id='fdsk3',
                name='fdsk3',
                size='10G',
                uuid='30000000fc85c928',
                bootable=False
            )
        ]

        cluster = VolMgrMockVCSCluster(item_id='cluster1', cluster_type='sfha')

        # add relationships between the model items
        cluster.nodes.extend(nodes)
        cluster.fencing_disks = fencing_disks

        # set appropriate states for every model item
        initial_items = [cluster]
        initial_items.extend(nodes)
        initial_items.extend(fencing_disks)

        VolMgrMock._set_state_initial(initial_items)

        rpc_callbacks = {
            'vx_disk_group_setup': Mock()
        }
        vxvm_driver = VxvmDriver(rpc_callbacks)
        vxvm_driver.get_vx_fencing_disk_group_name = Mock(
            return_value='fen_dg_name'
        )

        tasks = vxvm_driver.gen_tasks_for_fencing_disks(cluster)

        self.assertEqual(tasks, [callback_task])
        self.assertEqual(
            callback_patch.call_args_list,
            [
                call(
                    cluster.fencing_disks,
                    'Setup VxVM coordinator disk group "fen_dg_name"',
                    rpc_callbacks['vx_disk_group_setup'],
                    'fen_dg_name',
                    ['mn1'],
                    'cluster1'
                )
            ]
        )
        called_fencing_disks = [call(disk) for disk in fencing_disks]
        self.assertEqual(
            model_items_mock.add.call_args_list,
            called_fencing_disks
        )

    def test_setup_vx_disk(self):
        # mock up context
        context = VolMgrMockContext()
        context.rpc_command = MagicMock(return_value=rpc_result_ok)

        self.plugin.vxvm_driver._check_disk_for_dg = MagicMock()
        self.plugin.vxvm_driver._setup_vx_disk(
            context, ["disk_name"], ["node"], ["disk_group_name"]
        )

        # test that execution completes with exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._setup_vx_disk,
            context, ["disk_name"], ["node"], ["disk_group_name"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._setup_vx_disk,
            context, ["disk_name"], ["node"], ["disk_group_name"]
        )

        self.context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exc")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._setup_vx_disk,
            context, ["disk_name"], ["node"], ["disk_group_name"]
        )

    def test_check_dcoregionsz_rpc_exception(self):

        vg_data = {
            'vg_name': 'my_vg',
            'active_node': 'db-1',
            'file_systems': {},
            'cluster_id': '',
            'errors_list': []
        }

        vg = VolumeGroupData('/volume-group/vg1', vg_data)

        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:

            mock_rpc.side_effect = RpcExecutionException("test_exc")


            self.assertRaises(CallbackExecutionException,
                              self.plugin.vxvm_driver.check_dcoregionsz,
                              VolMgrMockContext(), vg, 'db-1')

    def test_check_dcoregionsz_empty_result_no_error(self):

        vg_data = {
            'vg_name': 'my_vg',
            'active_node': 'db-1',
            'file_systems': {},
            'cluster_id': '',
            'errors_list': []
        }

        vg = VolumeGroupData('/volume-group/vg1', vg_data)

        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:


            output = None
            errors = None

            mock_rpc.return_value = (output, errors)

            self.assertRaises(CallbackExecutionException,
                              self.plugin.vxvm_driver.check_dcoregionsz,
                              VolMgrMockContext(), vg, 'db-1')

    def test_check_dcoregionsz_error(self):

        vg_data = {
            'vg_name': 'my_vg',
            'active_node': 'db-1',
            'file_systems': {},
            'cluster_id': '',
            'errors_list': []
        }

        vg = VolumeGroupData('/volume-group/vg1', vg_data)

        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:


            output = None
            errors = {"err": "VxVM vxprint ERROR V-5-1-582 Disk group postgres_vg: No such disk group"}

            mock_rpc.return_value = (output, errors)

            self.assertRaises(CallbackExecutionException,
                              self.plugin.vxvm_driver.check_dcoregionsz,
                              VolMgrMockContext(), vg, 'db-1')

    def test_check_dcoregionsz(self):

        vg_data = {
            'vg_name': 'my_vg',
            'active_node': 'db-1',
            'file_systems': {},
            'cluster_id': '',
            'errors_list': []
        }

        vg = VolumeGroupData('/volume-group/vg1', vg_data)
        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:


            output = {"db-1": 2048}
            errors = None

            mock_rpc.return_value = (output, errors)
            actual = self.plugin.vxvm_driver.check_dcoregionsz(VolMgrMockContext(), vg, 'db-1')
            self.assertEquals(2048, actual)

    def test_create_snapshot_check_dcoregionsz_rpc_error(self):

        self.context = self.generate_mock_snapshot_model_for_data_manager()
        self.context.snapshot_model = \
            lambda: self.generate_mock_snapshot_model_for_data_manager()
        self.data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=self.context)

        vg = self.data_manager.get_all_volume_groups()[0]
        fs = vg.file_systems[0]

        self.data_manager.set_filters(vg_vpaths=[vg.id], fs_ids=[fs.id])

        # 1. where _check_decoregionsz returns RPC Exception
        with patch.object(RpcCommandOutputProcessor,
                          'execute_rpc_and_process_result') as mock_rpc:
            mock_rpc.side_effect = CallbackExecutionException("mock raise")

            self.assertRaises(
                CallbackExecutionException,
                self.plugin.vxvm_driver.create_snapshot,
                self.context,
                self.data_manager
            )

    def test_initialise_vx_disk_group(self):

        context = VolMgrMockContext()

        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.plugin.vxvm_driver._initialise_vx_disk_group(
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

        # test that execution completes without exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._initialise_vx_disk_group,
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._initialise_vx_disk_group,
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exc")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._initialise_vx_disk_group,
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

        # test errors on add_disk_to_group
        context.rpc_command = MagicMock(
            side_effect=mock_execute_rpc_and_process_result_err
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._initialise_vx_disk_group,
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=mock_execute_rpc_and_process_result_err2
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._initialise_vx_disk_group,
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

        self.context.rpc_command = MagicMock(
            side_effect=mock_execute_rpc_and_process_result_exec
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._initialise_vx_disk_group,
            context, "disk_group_name", ["disk1", "disk2"], ["node"]
        )

    def test_add_coordinator_to_disk_group(self):

        context = VolMgrMockContext()

        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.plugin.vxvm_driver._add_coordinator_to_disk_group(
            context, ["disk_name"], ["node"]
        )
        # test that execution completes without exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._add_coordinator_to_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._add_coordinator_to_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exec")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._add_coordinator_to_disk_group,
            context, ["disk_name"], ["node"]
        )

    def test_remove_coordinator_from_disk_group(self):

        context = VolMgrMockContext()

        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.plugin.vxvm_driver._remove_coordinator_from_disk_group(
            context, ["disk_name"], ["node"]
        )

        # test that execution completes without exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._remove_coordinator_from_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._remove_coordinator_from_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exec")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._remove_coordinator_from_disk_group,
            context, ["disk_name"], ["node"]
        )

    def test_destroy_disk_group(self):

        context = VolMgrMockContext()

        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.plugin.vxvm_driver._destroy_disk_group(
            context, ["disk_name"], ["node"]
        )

        # test that execution completes without exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._destroy_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._destroy_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exec")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver._destroy_disk_group,
            context, ["disk_name"], ["node"]
        )

    @patch('volmgr_plugin.drivers.vxvm.run_rpc_command')
    def test_run_vxdisk_list(self, patch_run_rpc_command):

        vcs_vxvm_driver = VxvmDriver(None)
        patch_run_rpc_command.return_value = rpc_result_ok

        vcs_vxvm_driver._run_vxdisk_list("disk_name", ["node"])
        # test that execution completes without exceptions
        self.assertTrue(1)

        patch_run_rpc_command.return_value = rpc_result_error
        self.assertRaises(
            CallbackExecutionException,
            vcs_vxvm_driver._run_vxdisk_list,
            "disk_name", ["node"]
        )

        patch_run_rpc_command.side_effect = RpcExecutionException("test_exec")
        self.assertRaises(
            CallbackExecutionException,
            vcs_vxvm_driver._run_vxdisk_list,
            "disk_name", ["node"]
        )

    def test_deport_disk_group(self):

        context = VolMgrMockContext()

        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.plugin.vxvm_driver.deport_disk_group(
            context, ["disk_name"], ["node"]
        )

        # test that execution completes without exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)

        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.deport_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.deport_disk_group,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exec")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.deport_disk_group,
            context, ["disk_name"], ["node"]
        )

    def test_import_disk_group_t_flag(self):

        context = VolMgrMockContext()

        context.rpc_command = MagicMock(return_value=rpc_result_ok)
        self.plugin.vxvm_driver.import_disk_group_t_flag(
            context, ["disk_name"], ["node"]
        )

        # test that execution completes without exceptions
        self.assertTrue(1)

        context.rpc_command = MagicMock(return_value=rpc_result_error)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.import_disk_group_t_flag,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(return_value=rpc_result_error2)
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.import_disk_group_t_flag,
            context, ["disk_name"], ["node"]
        )

        context.rpc_command = MagicMock(
            side_effect=RpcExecutionException("test_exec")
        )
        self.assertRaises(
            CallbackExecutionException,
            self.plugin.vxvm_driver.import_disk_group_t_flag,
            context, ["disk_name"], ["node"]
        )

    def test_vx_disk_group_setup(self):

        node1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        node2 = VolMgrMockNode(item_id='n2', hostname='mn2')

        nodes = [node1, node2]

        test_rpc_result = {
            node1.hostname: {
                "errors": "",
                "data": {"status": 0, "err": "", "out": "test_output"}
            },
            node2.hostname: {
                "errors": "",
                "data": {"status": 0, "err": "", "out": "test_output"}
            }
        }

        # set up model items
        context = VolMgrMockContext()

        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')

        fencing_disk_1 = VolMgrMockDisk(
            item_id='fdsk1', name='fdsk1',
            size='10G', uuid='test_uuid1', bootable=False
        )

        fencing_disk_2 = VolMgrMockDisk(
            item_id='fdsk2', name='fdsk2',
            size='10G', uuid='test_uuid2', bootable=False
        )

        fencing_disks = [fencing_disk_1, fencing_disk_2]

        # setup relationships between model items
        cluster.nodes.extend(nodes)
        cluster.fencing_disks = fencing_disks

        # set states of each of the model items
        applied_items = [cluster, fencing_disk_1, node1, node2]
        initial_items = [fencing_disk_2]

        VolMgrMock._set_state_applied(applied_items)
        VolMgrMock._set_state_initial(initial_items)

        # set up any other actions
        context.rpc_command = MagicMock(return_value=test_rpc_result)
        context.query = MagicMock(return_value=[cluster])

        self.plugin.vxvm_driver._check_disk_for_dg = MagicMock()
        VxvmDriver.get_disk_fact_name = Mock()

        # Test LITP Code
        hostnames = [node.hostname for node in nodes]
        self.plugin.vxvm_driver.vx_disk_group_setup(
            context, "disk_group_name", hostnames, cluster.cluster_id
        )

        called_fencing_disks = [call(disk.uuid) for disk in fencing_disks]

        self.assertEqual(
            VxvmDriver.get_disk_fact_name.call_args_list,
            called_fencing_disks
        )

    def test_vg_metadata(self):

        context = VolMgrMockContext()

        n1 = VolMgrMockNode(item_id='n1', hostname='n1')
        n2 = VolMgrMockNode(item_id='n2', hostname='n2')

        nodes = [n1, n2]

        VolMgrMock._set_state_applied(nodes)

        with patch.object(
                RpcCommandOutputProcessor,
                'execute_rpc_and_process_result'
        ) as mock_rpc:
            # dg imported in n2, no snapshots
            mock_rpc.return_value = (
                {'n1': '{}', 'n2': '{"storeg":[]}'}, {}
            )
            self.assertEquals(
                {'storeg': (n2, [])},
                self.plugin.vxvm_driver._vg_metadata(
                    context, nodes, ['storeg'], False
                )
            )

            one_snap = ["L_vol0_"]
            two_snap = ["L_vol1_", "L_vol0_"]

            # dg imported in n1, with snapshots
            mock_rpc.return_value = (
                {'n1': '{"storeg": %s}' % json.dumps(two_snap), 'n2': '{}'}, {}
            )
            self.assertEquals(
                {'storeg': (n1, two_snap)},
                self.plugin.vxvm_driver._vg_metadata(
                    context, nodes, ['storeg'], False
                )
            )
            # other dg imported
            mock_rpc.return_value = (
                {
                    'n1': '{"storeg2": %s}' % json.dumps(two_snap),
                    'n2': '{}'
                }, {}
            )
            self.assertEquals(
                {},
                self.plugin.vxvm_driver._vg_metadata(
                    context, nodes, ['storeg'], False
                )
            )
            # dgs in both nodes
            mock_rpc.return_value = (
                {
                    'n1': '{"storeg2":%s}' % json.dumps(two_snap),
                    'n2': '{"storeg":[]}'
                }, {}
            )
            self.assertEquals(
                {'storeg': (n2, []), 'storeg2': (n1, two_snap)},
                self.plugin.vxvm_driver._vg_metadata(
                    context, nodes, ['storeg', 'storeg2'], False
                )
            )
            # one dg in both nodes and another one not requested
            # dgs in both nodes
            mock_rpc.return_value = (
                {
                    'n1': '{"storeg2":%s}' % json.dumps(two_snap),
                    'n2': '{"storeg":[],"storeg2":%s,"storeg3":%s}' %
                          (json.dumps(two_snap), json.dumps(one_snap))},
                {})
            self.assertRaises(
                Exception,
                self.plugin.vxvm_driver._vg_metadata,
                context, nodes, ['storeg', 'storeg2'], False
            )
            # nothing nowhere
            mock_rpc.return_value = (
                {'n1': '{}', 'n2': '{}'}, {}
            )
            self.assertEquals(
                {},
                self.plugin.vxvm_driver._vg_metadata(
                    context, nodes, ['storeg'], False
                )
            )
            # error
            mock_rpc.return_value = (
                {
                    'n1': 'u fail because ure ugly',
                    'n2': 'and u fail because of something else'
                },
                {'n1': ['lol wut']}
            )
            self.assertRaises(
                PluginError,
                self.plugin.vxvm_driver._vg_metadata,
                context, nodes, 'storeg', False
            )
            # with unreachable nodes
            mock_rpc.return_value = (
                {'n1': '{}', 'n2': '{}'},
                {
                    'n1': ["{0} n1".format(BASE_RPC_NO_ANSWER)],
                    'n2': ["{0} n2".format(BASE_RPC_NO_ANSWER)]
                }
            )
            self.assertEquals(
                {},
                self.plugin.vxvm_driver._vg_metadata(
                    context, nodes, ['storeg'], True
                )
            )

    def test_dg_hostname_no_snapshots(self):

        context = VolMgrMockContext()

        cluster = VolMgrMockVCSCluster(item_id='c1')

        n1 = VolMgrMockNode(item_id='n1', hostname='n1')
        vg = VolMgrMockVG(item_id='dg', volume_group_name='dg')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        sp.volume_groups.append(vg)
        cluster.nodes.append(n1)
        context.query = MagicMock(return_value=cluster.nodes)

        applied_items = [cluster, n1, sp, vg]
        VolMgrMock._set_state_applied(applied_items)

        volume_group_names = [v.volume_group_name for v in sp.volume_groups]
        expected = {'dg': (n1, [])}

        def mock_query(item_type):
            if item_type == 'node':
                return cluster.nodes
            else:
                return []

        cluster.query = mock_query

        self.plugin.vxvm_driver._vg_metadata = MagicMock(
            return_value=expected
        )
        self.plugin.vxvm_driver.import_disk_group = MagicMock()

        self.assertEquals(
            expected,
            self.plugin.vxvm_driver.get_dg_hostname(
                context, cluster, volume_group_names
            )
        )

    def test_dg_hostname_unreachable_nodes(self):

        context = VolMgrMockContext()
        cluster = VolMgrMockVCSCluster(item_id='c1')

        n1 = VolMgrMockNode(item_id='n1', hostname='n1')
        n2 = VolMgrMockNode(item_id='n2', hostname='n2')
        vg = VolMgrMockVG(item_id='dg', volume_group_name='dg')
        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        nodes = [n1, n2]

        cluster.nodes.extend(nodes)
        sp.volume_groups.append(vg)

        def mock_query(item_type):
            if item_type == 'node':
                return cluster.nodes
            else:
                return []

        cluster.query = mock_query

        applied_items = [cluster, n1, n2]
        VolMgrMock._set_state_applied(applied_items)

        volume_group_names = [v.volume_group_name for v in sp.volume_groups]

        self.plugin.vxvm_driver.unreachable_nodes = ['n1']
        self.plugin.vxvm_driver._vg_metadata = MagicMock(
            return_value=(None, [])
        )
        self.plugin.vxvm_driver.import_disk_group = MagicMock()
        self.assertRaises(
            DgNotImportedError,
            self.plugin.vxvm_driver.get_dg_hostname,
            context, cluster, volume_group_names
        )

    def _generate_storage_model_12621(self, disk):

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')

        fs1 = VolMgrMockFS(
            item_id='fs1',
            size='1G',
            mount_point='/mnt',
            snap_size='100',
            snap_external='false'
        )

        pd1 = VolMgrMockPD(item_id='pd1', device_name='hda')

        node1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        sys1 = VolMgrMockSystem(item_id='s1')

        c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')

        # mock item relationships
        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)

        # will have to set the get_cluster() method on volume group
        vg1.get_cluster = lambda: c1

        sp1.volume_groups.append(vg1)

        sys1.disks.append(disk)

        node1.system = sys1

        c1.nodes.append(node1)
        c1.storage_profile.append(sp1)

        items = [disk, sp1, vg1, fs1, pd1, node1, sys1, c1]

        VolMgrMock._set_state_applied(items)

        # set up queries
        #
        # _vg_snap_operation_allowed_in_san_aspect() queries the system about
        # the device name of the physical device

        vg1.query = VolMgrMock.mock_query({
            'file-system': vg1.file_systems,
            'physical-device': vg1.physical_devices,
        })

        # pass a look-up dictionary, keyed on item_type_ids to the generator
        sys1.query = VolMgrMock.mock_query({
            'disk-base': sys1.disks,
            disk.item_type_id: sys1.disks
        })

        context = VolMgrMockContext()
        context.snapshot_model = lambda: c1

        c1.query = VolMgrMock.mock_query({
            # whichever type of disk this is
            disk.item_type_id: [disk],
            'node': [node1],
            'vcs-cluster': [c1],
            'system': [sys1],
            'storage-profile': [sp1],
            'volume-group': [vg1],
            'file-system': [fs1],
            'physical-device': [pd1],
        })

        return context

    def _generate_nas_backed_storage_model_12621(self):

        disk = VolMgrMockDisk(
            item_id='disk1',
            name='hda',
            size='10G',
            uuid='xxx123',
            bootable='false'
        )

        return self._generate_storage_model_12621(disk)

    def _generate_san_backed_storage_model_12621(self):

        disk = VolMgrMockOtherDisk(
            item_id='disk1',
            name='hda',
            size='10G',
            uuid='xxx123',
            bootable='false'
        )

        return self._generate_storage_model_12621(disk)

    def test__should_snapshot_fs(self):

        nas_backed_context = self._generate_nas_backed_storage_model_12621()

        nas_snapshot_model = nas_backed_context.snapshot_model()

        vgs = nas_snapshot_model.query('volume-group', item_id='vg1')
        self.assertEquals(1, len(vgs))
        vg1 = vgs[0]

        fss = vg1.query('file-system', item_id='fs1')
        self.assertEquals(1, len(fss))
        fs1 = fss[0]

        # Case 1: happy path, nas everything applied
        self.assertTrue(
            self.plugin.vxvm_driver._should_snapshot_fs(fs1, vg1))

        # Case 2: fs.snap_external = 'true'
        fs1.snap_external = 'true'
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        fs1.snap_external = 'false'

        # Case 3: fs.snap_size = '0'
        fs1.current_snap_size = '0'
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        fs1.current_snap_size = '100'

        # Case 4: file system states other than initial
        #         should actually enter the model
        VolMgrMock._set_state_initial([fs1])
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_applied([fs1])
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_updated([fs1])
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_for_removal([fs1])
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_applied([fs1])

        # Case 5: The file system is on a SAN, we do not support this in LITP
        san_backed_context = self._generate_san_backed_storage_model_12621()
        san_snapshot_model = san_backed_context.snapshot_model()

        vgs = san_snapshot_model.query('volume-group', item_id='vg1')
        self.assertEquals(1, len(vgs))
        vg1 = vgs[0]

        fss = vg1.query('file-system', item_id='fs1')
        self.assertEquals(1, len(fss))
        fs1 = fss[0]

        self.assertFalse(
            self.plugin.vxvm_driver._should_snapshot_fs(fs1, vg1))

    def test__can_delete_or_restore_snapshot(self):

        nas_backed_context = self._generate_nas_backed_storage_model_12621()

        nas_snapshot_model = nas_backed_context.snapshot_model()

        vgs = nas_snapshot_model.query('volume-group', item_id='vg1')
        self.assertEquals(1, len(vgs))
        vg1 = vgs[0]

        fss = vg1.query('file-system', item_id='fs1')
        self.assertEquals(1, len(fss))
        fs1 = fss[0]

        # Case 1: happy path, nas everything applied
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))

        # Case 2: fs.item_id  = None, for some weird reason
        fs_item_id = fs1.item_id
        fs1.item_id = None
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        fs1.item_id = fs_item_id

        # Case 3: fs.snap_external = 'true'
        fs1.snap_external = 'true'
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        fs1.snap_external = 'false'

        # Case 4: fs.snap_size = '0'
        fs1.current_snap_size = '0'
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        fs1.current_snap_size = '100'

        # Case 5: volume_group_name = None, for some weird reason
        vg1.volume_group_name = None
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        vg1.volume_group_name = 'vg1'

        # Case 6: file system states other than initial
        #         should actually enter the model
        VolMgrMock._set_state_initial([fs1])
        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_applied([fs1])
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_updated([fs1])
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_for_removal([fs1])
        self.assertTrue(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))
        VolMgrMock._set_state_applied([fs1])

        # Case 7: The file system is on a SAN, we do not support this in LITP
        san_backed_context = self._generate_san_backed_storage_model_12621()
        san_snapshot_model = san_backed_context.snapshot_model()

        vgs = san_snapshot_model.query('volume-group', item_id='vg1')
        self.assertEquals(1, len(vgs))
        vg1 = vgs[0]

        fss = vg1.query('file-system', item_id='fs1')
        self.assertEquals(1, len(fss))
        fs1 = fss[0]

        self.assertFalse(
            self.plugin.vxvm_driver._can_delete_or_restore_snapshot(vg1, fs1))

    def test_is_fs_in_snap_list(self):
        fs = VolMgrMockFS(item_id='fs1', size='1G',
                          mount_point='/mnt', snap_external='true')
        snap_list = {'snaps': [], 'co': [], 'cv' : []}
        tag = ''
        boolean, name = self.plugin.vxvm_driver.is_fs_in_snap_list(fs,
                                                                   snap_list,
                                                                   tag)
        self.assertFalse(boolean)

    # TODO BMD - What is this testing?
    def test_should_not_snapshot_lun_only_storage_profile(self):

        cluster = VolMgrMockVCSCluster(item_id='cluster1', cluster_type='sfha')

        disk_name = 'sda'
        size = 40 * 10 ** 9
        disk =  VolMgrMockDisk('my_disk', disk_name, size, 'uuid1', False,
                               'lun-disk')

        def node_side_effect(item_type_id, name):
            data = defaultdict(lambda: defaultdict(list))
            data['disk-base'][disk_name] = [disk_name]
            return data[item_type_id][name]

        node = VolMgrMockNode('my_node', 'mn1')
        cluster.nodes.append(node)
        node.system = MagicMock()
        node.system.query = MagicMock(side_effect=node_side_effect)
        sp = VolMgrMockStorageProfile('my_sp', 'vxvm')
        vg = VolMgrMockVG('my_vg', 'my_vg')
        fs = VolMgrMockFS('my_fs', size, '/mnt/joy', size)
        pd = VolMgrMockPD('my_pd', disk_name)
        vg.file_systems = [fs]
        vg.physical_devices = [pd]
        sp.volume_groups = [vg]

        vg.get_ms = vg.get_node = lambda: None
        vg.get_cluster = lambda: cluster

        applied_items = [node, vg, fs, pd, vg, sp]
        VolMgrMock._set_state_applied(applied_items)

        self.assertFalse(self.plugin.vxvm_driver._should_snapshot_fs(fs, vg))
        # named snapshot is an exception, should snapshot
        self.assertTrue(self.plugin.vxvm_driver._should_snapshot_fs(fs, vg,
                                                                    name='foobar'))

    def test_should_snapshot_disk_only_storage_profile(self):
        cluster = VolMgrMockVCSCluster(item_id='cluster1', cluster_type='sfha')
        size = 40 * 10 ** 9
        name = 'sda'
        pd = VolMgrMockPD('my_pd', name)
        disk = VolMgrMockDisk('my_disk', name, size, 'uuid1', False, 'disk')
        node = VolMgrMockNode('my_node', 'mn1')
        cluster.nodes.append(node)
        node.system = MagicMock()
        node.system.query = MagicMock(return_value=[disk])
        sp = VolMgrMockStorageProfile('my_sp', 'vxvm')
        vg = VolMgrMockVG('my_vg', 'my_vg')
        fs = VolMgrMockFS('my_fs', size, '/mnt/joy', size)
        vg.file_systems = [fs]
        vg.physical_devices = [pd]
        sp.volume_groups = [vg]
        fs.get_node = MagicMock(return_value=None)
        fs.get_cluster = MagicMock(return_value=cluster)
        applied_items = [node, vg, fs, pd, vg, sp]
        VolMgrMock._set_state_applied(applied_items)
        self.assertTrue(self.plugin.vxvm_driver._should_snapshot_fs(fs, vg))
        # named snapshot always created
        self.assertTrue(self.plugin.vxvm_driver._should_snapshot_fs(fs, vg, name='foo'))

    def test_should_snapshot_disk_only_vg_of_mixed_storage(self):
        cluster = VolMgrMockVCSCluster(item_id='cluster1', cluster_type='sfha')
        size = 40 * 10 ** 9
        node = VolMgrMockNode('my_node', 'mn1')
        cluster.nodes.append(node)
        def system_query_side_effect(item_type_id, name):
            data = defaultdict(lambda: defaultdict(list))
            data['disk-base'].update({'sda': [disk], 'sdb': [lun_disk]})
            data['disk'].update({'sda': [disk]})
            return data[item_type_id][name]
        node.system = MagicMock()
        node.system.query = MagicMock(side_effect=system_query_side_effect)
        sp = VolMgrMockStorageProfile('my_sp', 'vxvm')
        vg = VolMgrMockVG('my_vg', 'my_vg')
        lun_vg = VolMgrMockVG('lun_my_vg', 'lun_my_vg')
        fs = VolMgrMockFS('my_fs', size, '/mnt/regular', size)
        lun_fs = VolMgrMockFS('lun_my_fs', size, '/mnt/network-based', size)
        pd = VolMgrMockPD('my_pd', 'sda')
        lun_pd = VolMgrMockPD('lun_my_pd', 'sdb')
        disk = VolMgrMockDisk('my_disk', 'sda', size, 'uuid1', False)
        lun_disk = VolMgrMockDisk('lun_my_disk', 'sdb', size, 'uuid2', False,
                                  'lun-disk')
        vg.file_systems = [fs]
        vg.physical_devices = [pd]
        lun_vg.file_systems = [lun_fs]
        lun_vg.physical_devices = [lun_pd]
        sp.volume_groups = [vg, lun_vg]

        vg.get_ms = vg.get_node = lambda: None
        vg.get_cluster = lambda: cluster
        lun_vg.get_ms = lun_vg.get_node = lambda: None
        lun_vg.get_cluster = lambda: cluster

        applied_items = [node, vg, fs, pd, vg, sp, lun_pd, lun_vg, lun_fs]
        VolMgrMock._set_state_applied(applied_items)

        self.assertTrue(self.plugin.vxvm_driver._should_snapshot_fs(fs, vg))
        self.assertFalse(self.plugin.vxvm_driver._should_snapshot_fs(lun_fs, lun_vg))

        # named snapshot is an exception, should snapshot
        self.assertTrue(self.plugin.vxvm_driver._should_snapshot_fs(fs, vg, name='foo'))
        self.assertTrue(self.plugin.vxvm_driver._should_snapshot_fs(lun_fs, lun_vg,
                                                                    name='foo'))


    def test_torf_142717_remove_snapshot_plan(self):

        def _create_items(index, state):
            n = VolMgrMockNode(item_id='n%d' % index, hostname='mn%d' % index)
            fs = VolMgrMockFS(item_id='fs%d' % index, size='1G', snap_size='100',
                              mount_point='/shared%d' % index, type='vxfs')
            vg = VolMgrMockVG(item_id='vg%d' % index, volume_group_name='vxvg%d' % index)
            sp = VolMgrMockStorageProfile(item_id='sp%d' % index, volume_driver='vxvm')
            vg.file_systems = [fs]
            sp.volume_groups = [vg]

            if state == 'Initial':
                VolMgrMock._set_state_initial([n, fs, vg, sp])
            elif state == 'Applied':
                VolMgrMock._set_state_applied([n, fs, vg, sp])

            return (n, fs, vg, sp)

        c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        VolMgrMock._set_state_applied([c1])

        (n1, fs1, vg1, sp1) = _create_items(1, 'Applied')
        (n2, fs2, vg2, sp2) = _create_items(2, 'Initial')

        # The cluster now has Applied *and* Initial nodes & storage-profiles
        # ie cluster looks like it is about to be expanded.
        c1.nodes = [n1, n2]
        c1.storage_profile = [sp1, sp2]

        context = VolMgrMockContext()
        context.snapshot_model = lambda: context

        c1.query = VolMgrMock.mock_query({'node': [n1, n2],
                                          'storage-profile': [sp1, sp2]
                                          })

        snapshot = VolMgrMock('s1', 'snapshot')
        snapshot.active = 'true'

        context.query = VolMgrMock.mock_query({'vcs-cluster': [c1],
                                               'snapshot-base': [snapshot]})

        context.snapshot_action = lambda: 'create'
        create_tasks = self.plugin.create_snapshot_plan(context)

        # Just 1 Applied VG/FS, so just 1 task expected
        self.assertEqual(1, len(create_tasks))

        context.snapshot_action = lambda: 'remove'
        remove_tasks = self.plugin.create_snapshot_plan(context)

        # 4 tasks expected:
        #  check_active_nodes, check_restores_in_progress_cb
        #  remove_vxvm_snapshot_cb, check_lvm_restores_in_progress_cb
        self.assertEqual(4, len(remove_tasks))

        for taskset in (create_tasks, remove_tasks):

            the_check_active_nodes_task = taskset[0]
            data_set = the_check_active_nodes_task.kwargs['data_set']

            self.assertEqual(data_set['clusters'].keys(), [c1.item_id])
            self.assertEqual(data_set['clusters'][c1.item_id]['nodes'].keys(), [n1.hostname])
            self.assertEqual(data_set['volume_groups'].keys(), [vg1.get_vpath()])
            self.assertEqual(data_set['volume_groups'][vg1.get_vpath()]['file_systems'].keys(), [fs1.item_id])


class TestGetDiskGroupName(unittest.TestCase):

    def test_success(self):
        vcs_vxvm_driver = VxvmDriver(None)
        vxdisk_list_return = [
            'Device:disk_1', 'devicetag:disk_1', 'type:auto', 'hostid:',
            'disk:name=id=1406729508.8.mn2',
            'group:name=vxfencoorddg_1_30000_8c596709id=1406729513.10.mn2',
            'info:format=sliced,privoffset=1,pubslice=6,privslice=5',
            'flags:onlinereadyprivateautoconfig',
            ('pubpaths:block=/dev/vx/dmp/disk_1s6char=/dev/vx/rdmp/disk_1s6pri'
             'vpaths:block=/dev/vx/dmp/disk_1s5char=/dev/vx/rdmp/disk_1s5'),
            'guid:-', 'udid:FreeBSD%5FiSCSI%20Disk%5FDISKS%5F30000000EF27515F',
            'site:-', 'version:2.1', 'iosize:min=512(bytes)max=1024(blocks)',
            'public:slice=6offset=0len=83695072disk_offset=66080',
            'private:slice=5offset=1len=65535disk_offset=256',
            'update:time=1406820909seqno=0.19', 'ssb:actual_seqno=0.0',
            'headers:0248', 'configs:count=1len=51575', 'logs:count=1len=4096',
            'Definedregions:',
            'configpriv000017-000247[000231]:copy=01offset=000000enabled',
            'configpriv000249-051592[051344]:copy=01offset=000231enabled',
            'logpriv051593-055688[004096]:copy=01offset=000000enabled',
            "Multipathinginformation:'", 'numpaths:1', 'sdc\tstate=enabled'
        ]
        disk_group_name = vcs_vxvm_driver._get_disk_group_name_from_return(
            vxdisk_list_return
        )
        self.assertEqual(disk_group_name, 'vxfencoorddg_1_30000_8c596709')

    def test_success2(self):
        vcs_vxvm_driver = VxvmDriver(None)
        vxdisk_list_return = [
            'Device:disk_1', 'devicetag:disk_1', 'type:auto',
            'hostid:', 'disk:name=id=1406729508.8.mn2',
            'group:name=id=1406729513.10.mn2',
            'info:format=sliced,privoffset=1,pubslice=6,privslice=5',
            'flags:onlinereadyprivateautoconfig',
            ('pubpaths:block=/dev/vx/dmp/disk_1s6char=/dev/vx/rdmp/disk_1s6pri'
             'vpaths:block=/dev/vx/dmp/disk_1s5char=/dev/vx/rdmp/disk_1s5'),
            'guid:-', 'udid:FreeBSD%5FiSCSI%20Disk%5FDISKS%5F30000000EF27515F',
            'site:-', 'version:2.1', 'iosize:min=512(bytes)max=1024(blocks)',
            'public:slice=6offset=0len=83695072disk_offset=66080',
            'private:slice=5offset=1len=65535disk_offset=256',
            'update:time=1406820909seqno=0.19', 'ssb:actual_seqno=0.0',
            'headers:0248', 'configs:count=1len=51575', 'logs:count=1len=4096',
            'Definedregions:',
            'configpriv000017-000247[000231]:copy=01offset=000000enabled',
            'configpriv000249-051592[051344]:copy=01offset=000231enabled',
            'logpriv051593-055688[004096]:copy=01offset=000000enabled',
            "Multipathinginformation:'", 'numpaths:1', 'sdc\tstate=enabled'
        ]
        disk_group_name = vcs_vxvm_driver._get_disk_group_name_from_return(
            vxdisk_list_return
        )
        self.assertEqual(disk_group_name, '')


class TestCheckDisk(unittest.TestCase):

    def test_success_dg(self):
        vcs_vxvm_driver = VxvmDriver(None)
        vcs_vxvm_driver._run_vxdisk_list = Mock()
        vcs_vxvm_driver._run_vxdisk_list.return_value = (
            'Device:    disk_1\n'
            'devicetag: disk_1\n'
            'type:      auto\n'
            'hostid:    \n'
            'disk:      name= id=1406729508.8.mn2\n'
            'group:     name=vxfencoorddg_1_30000_8c596709'
            ' id=1406729513.10.mn2\n'
            'info:      format=sliced,privoffset=1,pubslice=6,privslice=5\n'
            'flags:     online ready private autoconfig\n'
            'pubpaths:  block=/dev/vx/dmp/disk_1s6 char=/dev/vx/rdmp/disk_1s6'
            'privpaths: block=/dev/vx/dmp/disk_1s5'
            ' char=/dev/vx/rdmp/disk_1s5\n'
            'guid:      -\n'
            'udid:      FreeBSD%5FiSCSI%20Disk%5FDISKS%5F30000000EF27515F\n'
            'site:      -\n'
            'version:   2.1\n'
            'iosize:    min=512 (bytes) max=1024 (blocks)\n'
            'public:    slice=6 offset=0 len=83695072 disk_offset=66080\n'
            'private:   slice=5 offset=1 len=65535 disk_offset=256\n'
            'update:    time=1406820909 seqno=0.19\n'
            'ssb:       actual_seqno=0.0\n'
            'headers:   0 248\n'
            'configs:   count=1 len=51575\n'
            'logs:      count=1 len=4096\n'
            'Defined regions:\n'
            'config   priv 000017-000247[000231]:'
            ' copy=01 offset=000000 enabled\n'
            'config   priv 000249-051592[051344]:'
            ' copy=01 offset=000231 enabled\n'
            'log      priv 051593-055688[004096]:'
            ' copy=01 offset=000000 enabled\n'
            'Multipathing information:\'\n'
            'numpaths:   1\n'
            'sdc            state=enabled'
        )

        vcs_vxvm_driver._get_disk_group_name_from_return = \
            Mock(return_value='vxfencoorddg_1_30000_8c596709')
        vcs_vxvm_driver.import_disk_group_t_flag = Mock()
        vcs_vxvm_driver._remove_coordinator_from_disk_group = Mock()
        vcs_vxvm_driver._destroy_disk_group = Mock()

        vcs_vxvm_driver._check_disk_for_dg(
            None, 'disk_1', None
        )
        self.assertEqual(vcs_vxvm_driver._run_vxdisk_list.call_count, 1)
        self.assertEqual(
            vcs_vxvm_driver._get_disk_group_name_from_return.call_count, 1
        )
        self.assertEqual(
            vcs_vxvm_driver.import_disk_group_t_flag.call_count, 1
        )
        self.assertEqual(
            vcs_vxvm_driver._remove_coordinator_from_disk_group.call_count, 1
        )
        self.assertEqual(vcs_vxvm_driver._destroy_disk_group.call_count, 1)

    def test_success_no_dg(self):
        vcs_vxvm_driver = VxvmDriver(None)
        vcs_vxvm_driver._run_vxdisk_list = Mock()
        vcs_vxvm_driver._run_vxdisk_list.return_value = (
            'Device:    disk_1\n'
            'devicetag: disk_1\n'
            'type:      auto\n'
            'hostid:    \n'
            'disk:      name= id=1406729508.8.mn2\n'
            'group:     name= id=1406729513.10.mn2\n'
            'info:      format=sliced,privoffset=1,pubslice=6,privslice=5\n'
            'flags:     online ready private autoconfig\n'
            'pubpaths:  block=/dev/vx/dmp/disk_1s6 char=/dev/vx/rdmp/disk_1s6'
            'privpaths: block=/dev/vx/dmp/disk_1s5'
            ' char=/dev/vx/rdmp/disk_1s5\n'
            'guid:      -\n'
            'udid:      FreeBSD%5FiSCSI%20Disk%5FDISKS%5F30000000EF27515F\n'
            'site:      -\n'
            'version:   2.1\n'
            'iosize:    min=512 (bytes) max=1024 (blocks)\n'
            'public:    slice=6 offset=0 len=83695072 disk_offset=66080\n'
            'private:   slice=5 offset=1 len=65535 disk_offset=256\n'
            'update:    time=1406820909 seqno=0.19\n'
            'ssb:       actual_seqno=0.0\n'
            'headers:   0 248\n'
            'configs:   count=1 len=51575\n'
            'logs:      count=1 len=4096\n'
            'Defined regions:\n'
            'config   priv 000017-000247[000231]:'
            ' copy=01 offset=000000 enabled\n'
            ' config   priv 000249-051592[051344]:'
            ' copy=01 offset=000231 enabled\n'
            'log      priv 051593-055688[004096]:'
            ' copy=01 offset=000000 enabled\n'
            'Multipathing information:\'\nnumpaths:   1\n'
            'sdc            state=enabled')

        vcs_vxvm_driver._get_disk_group_name_from_return = \
            Mock(return_value='')
        vcs_vxvm_driver.import_disk_group_t_flag = Mock()
        vcs_vxvm_driver._remove_coordinator_from_disk_group = Mock()
        vcs_vxvm_driver._destroy_disk_group = Mock()
        vcs_vxvm_driver._check_disk_for_dg(None, 'disk_1', None)

        self.assertEqual(vcs_vxvm_driver._run_vxdisk_list.call_count, 1)
        self.assertEqual(
            vcs_vxvm_driver._get_disk_group_name_from_return.call_count, 1
        )
        self.assertEqual(
            vcs_vxvm_driver.import_disk_group_t_flag.call_count, 0
        )
        self.assertEqual(
            vcs_vxvm_driver._remove_coordinator_from_disk_group.call_count, 0
        )
        self.assertEqual(vcs_vxvm_driver._destroy_disk_group.call_count, 0)


class TestSetupDiskRubyArguments(unittest.TestCase):

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask')
    @patch('volmgr_plugin.drivers.vxvm.RpcCommandProcessorBase')
    def test_success(self, base_rpc_command_processor_patch, callback_patch):
        # This test attempts to make sure that whenever the ruby
        # method "setup_disk" is called, that the same arguments are used
        # in each case.

        # Test for setup_disk for VxVM mount
        requires_mock = Mock(add=Mock())
        task_mock = Mock(requires=requires_mock)
        callback_patch.return_value = task_mock

        node = VolMgrMockNode(item_id='n1', hostname='mn1')

        disk = VolMgrMockDisk(
            item_id='disk_0',
            name='disk_0',
            size='10G',
            uuid='test_uuid1',
            bootable=False
        )
        disk.fact_disk_name = 'disk_0'
        disk.disk_group = 'disk_group_1'

        cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        cluster.nodes.extend([node])
        cluster.fencing_disks = [
            VolMgrMockDisk(
                item_id='fdisk_0',
                name='fdisk_0',
                size='10G',
                uuid='123',
                bootable=False
            )
        ]

        def mock_query(item_type):
            if item_type == 'node':
                return cluster.nodes
            else:
                return []

        cluster.query = mock_query

        sp = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        mock_base = Mock()
        rpc_callbacks = {'vx_init': mock_base}
        vcs_vxvm_driver = VxvmDriver(rpc_callbacks)

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        pd1 = VolMgrMockPD(item_id='pd1', device_name='disk_0')

        # Storage profile has one volume group
        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='disk_group_1')
        vg1.physical_devices.append(pd1)
        sp1.volume_groups.append(vg1)

        callback_return = vcs_vxvm_driver._setup_disk_task(
            node, disk, vg1, pd1, cluster
        )

        self.assertEqual(callback_return, task_mock)
        self.assertEqual(requires_mock.add.call_count, 1)


        self.assertEqual(
            callback_patch.call_args_list,
            [
                call(pd1, 'Setup VxVM disk "disk_0" on node "mn1"',
                     mock_base, ['mn1'], 'vxvm', 'setup_disk',
                     disk_group=disk.disk_group, timeout=299, disk_name=disk.get_vpath())
            ]
        )

        # Test for setup_disk for split-brain
        execute_rpc_mock = Mock(['execute_rpc_and_process_result'])
        execute_rpc_mock.execute_rpc_and_process_result.return_value = \
            ('out', {})

        base_rpc_command_processor_patch.return_value = execute_rpc_mock

        vcs_vxvm_driver = VxvmDriver(None)
        context = Mock()

        disk_names = ['disk_0']
        nodes = ['mn1']
        disk_group_name = 'disk_group_1'

        vcs_vxvm_driver._check_disk_for_dg = Mock()
        return_execute = vcs_vxvm_driver._setup_vx_disk(
            context, disk_names, nodes, disk_group_name
        )

        self.assertEqual(return_execute, None)
        self.assertEqual(vcs_vxvm_driver._check_disk_for_dg.call_count, 1)
        self.assertEqual(
            execute_rpc_mock.execute_rpc_and_process_result.call_args_list,
            [
                call(
                    context, nodes, 'vxvm', 'setup_disk',
                    {'disk_group': 'disk_group_1', 'disk_name': 'disk_0'},
                    retries=1, timeout=299
                )
            ]
        )

        # Combined Test for check that the same arguments are used in both
        # times 'setup_disk' ruby method is called
        self.assertTrue(
            "'setup_disk', " +
            str({'disk_group': 'disk_group_1', 'disk_name': 'disk_0'},)
            in
            str(execute_rpc_mock.execute_rpc_and_process_result.call_args_list)
        )
        self.assertEqual(
            call(
                pd1, 'Setup VxVM disk "disk_0" on node "mn1"',
                mock_base, ['mn1'], 'vxvm', 'setup_disk',
                disk_group=disk.disk_group, timeout=299, disk_name=disk.get_vpath()
            ), callback_patch.call_args
        )
