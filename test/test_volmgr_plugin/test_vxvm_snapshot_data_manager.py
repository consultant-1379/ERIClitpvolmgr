##############################################################################
# COPYRIGHT Ericsson AB 2015
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

import unittest
from litp.core.model_manager import ModelManager
from volmgr_plugin.volmgr_plugin import VolMgrPlugin
from litp.core.plugin_context_api import PluginApiContext
from litp.plan_types.remove_snapshot import remove_snapshot_tags

from volmgr_plugin.drivers.vxvm_snapshot_data_mgr import (
    VolumeGroupData,
    FileSystemData,
    ClusterData,
    VxvmSnapshotDataMgr,
)

from mock import patch, MagicMock

from .mock_vol_items import (
    VolMgrMock,
    VolMgrMockContext,
    VolMgrMockVCSCluster,
    VolMgrMockNode,
    VolMgrMockStorageProfile,
    VolMgrMockVG,
    VolMgrMockFS,
    VolMgrMockSystem,
    VolMgrMockPD,
    MockCallbackTask,
    VolMgrMockSnapshot
)

from litp.core.rpc_commands import (
    RpcCommandOutputProcessor,
    RpcCommandProcessorBase
)

from litp.core.execution_manager import (
    PluginError,
    CallbackExecutionException
)


def get_raw_cluster_data(cluster):

    return {
        'nodes': dict(
            (host, {'reachable': '', 'errors_list': []})
            for host in cluster.nodes
        ),
    }


def get_mock_cluster_data(cluster):
    return ClusterData(cluster.item_id, get_raw_cluster_data(cluster))


class TestVxvmSnapshotDataMgr(unittest.TestCase):

    def generate_mock_snapshot_model_litpcds_10831(self):
        c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        n1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        n2 = VolMgrMockNode(item_id='n2', hostname='mn2')
        n3 = VolMgrMockNode(item_id='n3', hostname='mn3')

        nodes = [n1, n2, n3]

        fs1 = VolMgrMockFS(item_id='fs1', size='1G', mount_point='/foo')
        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='data-vg',
                           cluster_for_get=c1)

        fs2 = VolMgrMockFS(item_id='fs2', size='1G', mount_point='/bar')
        vg2 = VolMgrMockVG(item_id='vg2', volume_group_name='db-vg',
                           cluster_for_get=c1)

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        file_systems = [fs1, fs2]
        volume_groups = [vg1, vg2]
        storage_profiles = [sp1]

        vg1.file_systems = [fs1]
        vg2.file_systems = [fs2]

        sp1.volume_groups = volume_groups

        c1.nodes = nodes
        c1.query = VolMgrMock.mock_query({
            'node': nodes,
            'file-system': file_systems,
            'volume-group': volume_groups,
            'storage-profile': storage_profiles,
        })
        c1.storage_profile = storage_profiles

        clusters = [c1]

        items = nodes + file_systems + volume_groups \
            + storage_profiles + clusters

        VolMgrMock._set_state_applied(items)

        # set up model items
        model = VolMgrMockContext()

        model.query = VolMgrMock.mock_query({
            'node': nodes,
            'file-system': file_systems,
            'volume-group': volume_groups,
            'storage-profile': storage_profiles,
            'vcs-cluster': clusters
        })

        return model

    def generate_mock_snapshot_model_litpcds_12327_3_clusters(self):

        n1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        n2 = VolMgrMockNode(item_id='n2', hostname='mn2')
        n3 = VolMgrMockNode(item_id='n3', hostname='mn3')
        n4 = VolMgrMockNode(item_id='n4', hostname='mn4')
        n5 = VolMgrMockNode(item_id='n5', hostname='mn5')
        n6 = VolMgrMockNode(item_id='n6', hostname='mn6')
        c1_nodes = [n1, n2]
        c2_nodes = [n3, n4]

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

        c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        c1.nodes = c1_nodes
        c1.query = VolMgrMock.mock_query({
            'node': c1_nodes,
            'file-system': file_systems,
            'volume-group': volume_groups,
            'storage-profile': storage_profiles,
        })

        c1.storage_profile = storage_profiles

        # additional cluster with no vxvm storage profile
        c2 = VolMgrMockVCSCluster(item_id='c2', cluster_type='vcs')
        c2.nodes = c2_nodes
        c2.query = VolMgrMock.mock_query({'node': c2_nodes,})
        snap_clusters = [c1, c2]

        snap_nodes = c1_nodes + c2_nodes

        snap_items = snap_nodes + file_systems + volume_groups \
            + storage_profiles + snap_clusters

        VolMgrMock._set_state_applied(snap_items)

        # set up snapshot model items
        snapshot_model = VolMgrMockContext()

        snapshot_model.query = VolMgrMock.mock_query({
            'node': snap_nodes,
            'file-system': file_systems,
            'volume-group': volume_groups,
            'storage-profile': storage_profiles,
            'vcs-cluster': snap_clusters
        })

        # additional cluster with no vxvm storage profile
        c3_nodes = [n5, n6]

        fs3 = VolMgrMockFS(item_id='fs3', size='1G', mount_point='/foofoo')
        vg3 = VolMgrMockVG(item_id='vg3', volume_group_name='data-vg3')

        sp2 = VolMgrMockStorageProfile(item_id='sp2', volume_driver='vxvm')

        c3_file_systems = [fs3,]
        c3_volume_groups = [vg3,]
        c3_storage_profiles = [sp2]

        vg3.file_systems = [fs3]

        sp2.volume_groups = c3_volume_groups

        c3 = VolMgrMockVCSCluster(item_id='c3', cluster_type='sfha')
        c3.nodes = c3_nodes
        c3.storage_profile = c3_storage_profiles
        c3.query = VolMgrMock.mock_query({
            'node': c3_nodes,
            'file-system': c3_file_systems,
            'volume-group': c3_volume_groups,
            'storage-profile': c3_storage_profiles,
        })
        initial_items = c3_nodes + [c3] + \
                        c3_file_systems + \
                        c3_volume_groups + \
                        c3_storage_profiles

        VolMgrMock._set_state_initial(initial_items)

        # set up model items
        model = VolMgrMockContext()

        model.query = VolMgrMock.mock_query( {
            'node': snap_nodes + c3_nodes,
            'file-system': file_systems + c3_file_systems,
            'volume-group': volume_groups + c3_volume_groups,
            'storage-profile': storage_profiles + c3_storage_profiles,
            'vcs-cluster': snap_clusters + [c3]
        })

        model.snapshot_model = lambda: snapshot_model

        return model

    def generate_mock_snapshot_model_litpcds_12982(self):
        #create the first cluster in a state of initial
        c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        n1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        n2 = VolMgrMockNode(item_id='n2', hostname='mn2')

        c1_nodes = [n1, n2]

        fs1 = VolMgrMockFS(item_id='fs1', size='1G', mount_point='/foo')
        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='data-vg',
                           cluster_for_get=c1)

        fs2 = VolMgrMockFS(item_id='fs2', size='1G', mount_point='/bar')
        vg2 = VolMgrMockVG(item_id='vg2', volume_group_name='db-vg',
                           cluster_for_get=c1)

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')

        c1_file_systems = [fs1, fs2]
        c1_volume_groups = [vg1, vg2]
        c1_storage_profiles = [sp1]

        vg1.file_systems.append(fs1)
        vg2.file_systems.append(fs2)

        sp1.volume_groups = c1_volume_groups

        c1.nodes = c1_nodes
        c1.query = VolMgrMock.mock_query({
            'node': c1_nodes,
            'file-system': c1_file_systems,
            'volume-group': c1_volume_groups,
            'storage-profile': c1_storage_profiles,
        })
        c1.storage_profile = c1_storage_profiles

        c1_items = c1_nodes + c1_file_systems + c1_volume_groups \
            + c1_storage_profiles + [c1]

        VolMgrMock._set_state_initial(c1_items)

        #create the second cluster in a state of updated
        c2 = VolMgrMockVCSCluster(item_id='c2', cluster_type='sfha')
        n3 = VolMgrMockNode(item_id='n3', hostname='mn3')
        n4 = VolMgrMockNode(item_id='n4', hostname='mn4')

        c2_nodes = [n3, n4]

        fs3 = VolMgrMockFS(item_id='fs3', size='1G', mount_point='/foo')
        vg3 = VolMgrMockVG(item_id='vg3', volume_group_name='data-vg',
                           cluster_for_get=c1)

        fs4 = VolMgrMockFS(item_id='fs4', size='1G', mount_point='/bar')
        vg4 = VolMgrMockVG(item_id='vg4', volume_group_name='db-vg',
                           cluster_for_get=c1)

        sp2 = VolMgrMockStorageProfile(item_id='sp2', volume_driver='vxvm')

        c2_file_systems = [fs3, fs4]
        c2_volume_groups = [vg3, vg4]
        c2_storage_profiles = [sp2]

        vg3.file_systems.append(fs3)
        vg4.file_systems.append(fs4)

        sp2.volume_groups = c2_volume_groups

        c2.nodes = c2_nodes
        c2.query = VolMgrMock.mock_query({
            'node': c2_nodes,
            'file-system': c2_file_systems,
            'volume-group': c2_volume_groups,
            'storage-profile': c2_storage_profiles,
        })
        c2.storage_profile = c2_storage_profiles

        c2_items = c2_nodes + c2_file_systems + c2_volume_groups \
            + c2_storage_profiles + [c2]

        VolMgrMock._set_state_updated(c2_items)

        #create the third cluster in a stae for_removal
        c3 = VolMgrMockVCSCluster(item_id='c3', cluster_type='sfha')
        n5 = VolMgrMockNode(item_id='n5', hostname='mn5')
        n6 = VolMgrMockNode(item_id='n6', hostname='mn6')

        c3_nodes = [n5, n6]

        fs5 = VolMgrMockFS(item_id='fs5', size='1G', mount_point='/foo')
        vg5 = VolMgrMockVG(item_id='vg5', volume_group_name='data-vg',
                           cluster_for_get=c1)

        fs6 = VolMgrMockFS(item_id='fs6', size='1G', mount_point='/bar')
        vg6 = VolMgrMockVG(item_id='vg6', volume_group_name='db-vg',
                           cluster_for_get=c1)

        sp3 = VolMgrMockStorageProfile(item_id='sp3', volume_driver='vxvm')

        c3_file_systems = [fs5, fs6]
        c3_volume_groups = [vg5, vg6]
        c3_storage_profiles = [sp3]

        vg5.file_systems.append(fs5)
        vg6.file_systems.append(fs6)

        sp3.volume_groups = c3_volume_groups

        c3.nodes = c3_nodes
        c3.query = VolMgrMock.mock_query({
            'node': c3_nodes,
            'file-system': c3_file_systems,
            'volume-group': c3_volume_groups,
            'storage-profile': c3_storage_profiles,
        })
        c3.storage_profile = c3_storage_profiles

        c3_items = c3_nodes + c3_file_systems + c3_volume_groups \
            + c3_storage_profiles + [c3]

        VolMgrMock._set_state_for_removal(c3_items)

        # set up snapshot model items
        snap_clusters = [c2, c3]

        c2_snap_items = c2_nodes + c2_file_systems + c2_volume_groups \
            + c2_storage_profiles + [c2]

        c3_snap_items = c3_nodes + c3_file_systems + c3_volume_groups \
            + c3_storage_profiles + [c3]

        VolMgrMock._set_state_updated(c2_snap_items)
        VolMgrMock._set_state_for_removal(c3_snap_items)

        snapshot_model = VolMgrMockContext()

        snap_nodes = [c2_nodes, c3_nodes]
        snap_file_systems = [c2_file_systems, c3_file_systems]
        snap_volume_groups = [c2_volume_groups, c3_volume_groups]
        snap_storage_profiles = [c2_storage_profiles, c3_storage_profiles]
        snap_clusters = [c2, c3]

        snapshot_model.query = VolMgrMock.mock_query({
            'node': snap_nodes,
            'file-system': snap_file_systems,
            'volume-group': snap_volume_groups,
            'storage-profile': snap_storage_profiles,
            'vcs-cluster': snap_clusters
        })

        # set up model items
        nodes = [c1_nodes, c2_nodes, c3_nodes]
        file_systems = [c1_file_systems, c2_file_systems, c3_file_systems]
        volume_groups = [c1_volume_groups, c2_volume_groups, c3_volume_groups]
        storage_profiles = [c1_storage_profiles, c2_storage_profiles,
            c3_storage_profiles]
        clusters = [c1, c2, c3]
        model = VolMgrMockContext()

        model.query = VolMgrMock.mock_query({
            'node': clusters,
            'file-system': file_systems,
            'volume-group': volume_groups,
            'storage-profile': storage_profiles,
            'vcs-cluster': clusters
        })

        model.snapshot_model = lambda: snapshot_model

        return model

    def setUp(self):

        self.snapshot_name = 'snap_name'
        model = self.generate_mock_snapshot_model_litpcds_10831()
        self.context = model
        self.context.snapshot_model = lambda: model

        self.plugin = VolMgrPlugin()
        self.data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=self.context)

    def test__populate(self):

        #Case 1. create and remove action with 3 clusters in states
        #         initial, updated and for_removal
        #         clusters should be ignored if state is initial
        #         data manager should be in same state for restore
        context = self.generate_mock_snapshot_model_litpcds_12982()

        context.snapshot_action = lambda: 'create'

        data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=context)

        clusters = data_manager.get_clusters()

        self.assertEquals(2, len(clusters))

        vcs_cluster = clusters
        self.assertTrue('c3' in c.id for c in vcs_cluster)
        self.assertTrue('c2' in c.id for c in vcs_cluster)

        context.snapshot_action = lambda: 'remove'

        data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=context)

        clusters = data_manager.get_clusters()

        self.assertEquals(2, len(clusters))

        vcs_cluster = clusters
        self.assertTrue('c3' in c.id for c in vcs_cluster)
        self.assertTrue('c2' in c.id for c in vcs_cluster)

        context = self.generate_mock_snapshot_model_litpcds_12327_3_clusters()

        # Case 2. create action
        context.snapshot_action = lambda: 'create'

        data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=context)

        clusters = data_manager.get_clusters()

        self.assertEquals(1, len(clusters))

        vcs_cluster = clusters[0]
        self.assertEquals('c1', vcs_cluster.id)

        # Case 3. remove action
        #         data manager should be in same state for restore
        context.snapshot_action = lambda: 'remove'

        data_manager = VxvmSnapshotDataMgr(
            driver=self.plugin.vxvm_driver, context=context)

        clusters = data_manager.get_clusters()

        self.assertEquals(1, len(clusters))

        vcs_cluster = clusters[0]
        self.assertEquals('c1', vcs_cluster.id)

    @patch('volmgr_plugin.drivers.vxvm.CallbackTask', new=MockCallbackTask)
    def test_litpcds_12788_driver_generates_tasks(self):
        snapshot_name = 'ombs'
        self.context.snapshot_name = lambda: snapshot_name
        self.context.snapshot_action = lambda: 'remove'
        driver = self.plugin.vxvm_driver
        system = VolMgrMockSystem('s1')
        def system_query(item_type, **kwargs):
            if item_type == 'disk':
                return []
            elif item_type == 'disk-base':
                return [None]
        system.query = system_query
        for node in self.context.query('node'):
            node.system = system
        for vg in self.context.query('volume-group'):
            vg.physical_devices = [VolMgrMockPD('pd1', 'sda')]
        tasks = driver._remove_snapshot_tasks(self.context)

        expected = set(['Check that an active node exists for each VxVM volume group on cluster(s) "c1"',
                        'Check VxVM snapshots are currently not being restored',
                        'Remove VxVM named backup snapshot "L_fs1_' + snapshot_name + '" for cluster "c1", volume group "data-vg"',
                        'Remove VxVM named backup snapshot "L_fs2_' + snapshot_name + '" for cluster "c1", volume group "db-vg"'])
        actual = set([x.description for x in tasks])
        self.assertEqual(actual, expected)

    def test_litpcds_12788_driver_called_with_name(self):
        snapshot_name = 'ombs'
        self.context.snapshot_name = lambda: snapshot_name
        self.context.snapshot_action = lambda: 'remove'
        driver = self.plugin.vxvm_driver
        orig = driver._vg_snap_operation_allowed_in_san_aspect
        mocked = MagicMock(wraps=orig)

        with patch.object(driver,
                          '_vg_snap_operation_allowed_in_san_aspect',
                          mocked) as m:
            data_manager = VxvmSnapshotDataMgr(
                driver=driver, context=self.context)
        for kall in m.call_args_list:
            args, kwargs = kall
            if len(args) == 3:
                self.assertEqual(args[2], snapshot_name)
            else:
                self.assertEqual(kwargs.get('name'), snapshot_name)

    def test_set_filters(self):

        my_filter = 'my_filter'
        self.data_manager.set_filters(my_filter)
        #self.assertEqual(my_filter, self.data_manager.get_filter())

    def test_get_snapshot_name(self):

        snap_name = 'my_snap'
        self.data_manager.set_snapshot_name(snap_name)
        self.assertEqual(snap_name, self.data_manager.get_snapshot_name())

    def test_get_data_set(self):

        data = self.data_manager.get_data_set()

        clusters = data['clusters']

        # get the cs cluster from the model
        cluster = self.context.snapshot_model().query('vcs-cluster')[0]

        self.assertEqual(1, len(clusters))
        self.assertTrue(cluster.item_id in clusters.keys())

    def test_get_cluster_ids(self):

        actual = self.data_manager.get_cluster_ids()
        expected = ['c1']

        self.assertEquals(expected, actual)

    def test_get_cluster_by_id(self):

        mock_cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        mock_cluster.nodes = ['mn1', 'mn2', 'mn3']

        actual = self.data_manager.get_cluster_by_id('c1')
        expected = get_raw_cluster_data(mock_cluster)

        self.assertEquals(expected, actual)

    def test_get_cluster_hostnames(self):

        actual = self.data_manager.get_cluster_hostnames('c1')
        expected = ['mn1', 'mn2', 'mn3']

        self.assertEquals(expected, actual)

    def test_get_clusters(self):

       mock_cluster = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
       mock_cluster.nodes = ['mn1', 'mn2', 'mn3']
       expected = [get_mock_cluster_data(mock_cluster)]
       self.assertEquals(expected, self.data_manager.get_clusters())

    def test_get_volume_groups(self):

        cluster = self.data_manager.get_clusters()[0]

        actual = self.data_manager.get_volume_groups(cluster)

        expected = [

            VolumeGroupData(
                '/volume-group/vg1',
                {'vg_name': 'data-vg', 'active_node': '',
                 'file_systems': {}, 'cluster_id': '', 'errors_list': []}),

            VolumeGroupData(
                '/volume-group/vg2',
                {'vg_name': 'db-vg', 'active_node': '',
                 'file_systems': {}, 'cluster_id': '', 'errors_list': []})

        ]

        self.assertEquals(expected, actual)

    def test_get_cluster_volume_groups(self):

        actual = self.data_manager.get_cluster_volume_groups('c1')

        expected = [

            VolumeGroupData(
                '/volume-group/vg1',
                {'vg_name': 'data-vg', 'active_node': '',
                 'file_systems': {}, 'cluster_id': '', 'errors_list': []}),

            VolumeGroupData(
                '/volume-group/vg2',
                {'vg_name': 'db-vg', 'active_node': '',
                 'file_systems': {}, 'cluster_id': '', 'errors_list': []})

        ]

        self.assertEquals(expected, actual)

    def test_set_and_get_active_node(self):

        volume_groups = self.data_manager.get_cluster_volume_groups('c1')
        hostnames = self.data_manager.get_cluster_hostnames('c1')

        # set the active node for volume groups to the
        # first hostname in the cluster

        active_node = hostnames[0]

        for vg in volume_groups:
            self.data_manager.set_active_node(vg, active_node)

        for vg in volume_groups:
            self.assertEquals(
                active_node, self.data_manager.get_active_node(vg))

        # make sure that you get an error if you pass an non-existent vg
        bogus = VolumeGroupData(
            'vpath/that/does/not/exist',
            {'vg_name': 'bogus_vg',
             'active_node': '',
             'file_systems': {},
             'cluster_id': '',
             'errors_list': []}
        )

        try:
            self.data_manager.set_active_node(bogus, 'some_node')
        except KeyError as actual:

            expected = KeyError('%s does not exist in data manager' % bogus)
            self.assertEquals(str(expected), str(actual))

        # Expect None if pass a vg not belonging
        self.assertEqual(None, self.data_manager.get_active_node(bogus))

    def test_gather_active_nodes(self):

        nodes = self.context.query('node')

        # gather hostnames and vg_names from
        hostnames = [node.hostname for node in nodes]

        # unpack the list of hostnames
        host1, host2, host3 = hostnames
        cluster = self.data_manager.get_clusters()[0]

        with patch.object(VxvmSnapshotDataMgr, '_get_volume_group_map') \
                as mock_map:

            # 1. Happy Path (Positive Case) all nodes reachable

            self.data_manager._set_node_reachable(cluster, host1)
            self.data_manager._set_node_reachable(cluster, host2)
            self.data_manager._set_node_reachable(cluster, host3)

            volume_group_map = {
                "data-vg": host1,
                "foo-vg": host1,
                "bar-vg": host2,
                "db-vg": host2,
            }

            # mock map needs to return a node report and map
            mock_map.return_value = volume_group_map

            self.data_manager.gather_active_nodes(VolMgrMockContext())

            # LITPCDS-12803 positive case - all nodes are reachable
            self.assertFalse(self.data_manager._get_unreachable_nodes(cluster))

            # cluster should be reachable
            self.assertTrue(self.data_manager._has_reachable_nodes(cluster))

            # 2. Negative Case, no hosts can be reached LITPCDS-12242

            self.data_manager._set_node_unreachable(cluster, host1)
            self.data_manager._set_node_unreachable(cluster, host2)
            self.data_manager._set_node_unreachable(cluster, host3)

            volume_group_map = {}
            mock_map.return_value = volume_group_map

            self.data_manager.gather_active_nodes(VolMgrMockContext())

            # cluster should not be reachable
            self.assertFalse(self.data_manager._has_reachable_nodes(cluster))

            # 3. Positive Case, no active nodes for vgs, need to import

            # mock passed this as if import was OK
            self.plugin.vxvm_driver.import_disk_group_t_flag = \
                lambda x, y, z: True

            self.data_manager._set_node_unreachable(cluster, host1)
            self.data_manager._set_node_reachable(cluster, host2)
            self.data_manager._set_node_unreachable(cluster, host3)

            volume_group_map = {}

            # mock map needs to return a node report and map
            mock_map.return_value = volume_group_map
            self.data_manager.gather_active_nodes(VolMgrMockContext())

            # cluster should be reachable
            self.assertTrue(self.data_manager._has_reachable_nodes(cluster))

            # LITPCDS-12803 error raised for 'restore' when all nodes are not reachable
            self.data_manager._set_action('restore')
            self.assertEquals(['mn1', 'mn3'], self.data_manager._get_unreachable_nodes(cluster))
            self.assertEquals(['mn1', 'mn3'], self.data_manager.get_all_unreachable_nodes())

    def test_gather_snapshots_are_present(self):

        # Error
        vg = self.context.query('volume-group')[0]
        self.data_manager.set_filters(vg_vpaths=[vg.get_vpath()],
                                      fs_ids=['doesntexist'])

        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:
            output = 0
            errors = {}
            mock_rpc.return_value = (output, errors)

            cluster = self.data_manager.get_clusters()[0]

            for vg in self.data_manager.get_volume_groups(cluster):
                self.data_manager.set_active_node(vg, 'n1')
                self.data_manager.set_active_node(vg, 'n1')

            self.data_manager.gather_snapshots_are_present(VolMgrMockContext())

    def test_gather_snapshots_are_valid(self):

        # Error
        vg = self.context.query('volume-group')[0]
        fs = vg.file_systems[0]
        self.data_manager.set_filters(vg_vpaths=[vg.get_vpath()],
                                      fs_ids=[fs.item_id])
        vg_item = self.data_manager.data['volume_groups'][vg.get_vpath()]
        vg_item['file_systems'][fs.item_id]['valid']=False

        with patch.object(RpcCommandProcessorBase,
                         'execute_rpc_and_process_result') as mock_rpc:
            output = 0
            errors = {'n1': ['error1','error2']}
            mock_rpc.return_value = (output, errors)

            cluster = self.data_manager.get_clusters()[0]

            for vg in self.data_manager.get_volume_groups(cluster):
                self.data_manager.set_active_node(vg, 'n1')
                self.data_manager.set_active_node(vg, 'n1')

            self.data_manager.gather_snapshots_are_valid(VolMgrMockContext())

    def test_gather_restores_in_progress(self):

        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:
            output = 0
            errors = ['errs']
            mock_rpc.return_value = (output, errors)

            cluster = self.data_manager.get_clusters()[0]

            for vg in self.data_manager.get_volume_groups(cluster):
                self.data_manager.set_active_node(vg, 'n1')
                self.data_manager.set_active_node(vg, 'n1')

            self.data_manager.gather_restores_in_progress(VolMgrMockContext())

            errors = {}
            mock_rpc.return_value = (output, errors)
            self.data_manager.gather_restores_in_progress(VolMgrMockContext())

    def test_get_filesystems(self):

        volume_groups = self.data_manager.get_cluster_volume_groups('c1')

        expected = {'data-vg': ['fs1'], 'db-vg': ['fs2']}

        for vg in volume_groups:

            # Test get vg filesystems
            filesystems = self.data_manager.get_volume_group_filesystems(vg)

            expected_fs_ids = expected[vg.name]
            actual_fs_ids = [fs.id for fs in filesystems]

            self.assertEqual(expected_fs_ids, actual_fs_ids)

    def test__get_volume_group_info_rpc(self):

        clusters = self.context.query('vcs-cluster')

        c1 = clusters[0]

        # gather hostnames and vg_names from
        hostnames = [node.hostname for node in c1.nodes]

        # unpack the list of hostnames
        host1, host2, host3 = hostnames

        cluster = get_mock_cluster_data(c1)

        with patch.object(RpcCommandOutputProcessor,
                         'execute_rpc_and_process_result') as mock_rpc:

            # if all nodes are up this is what the output looks like

            output = {
                host1: '["data-vg", "foo-vg"]',
                host2: '["db-vg", "bar-vg"]',
                host3: '[]'
            }

            errors = {}

            mock_rpc.return_value = (output, errors)

            actual = self.data_manager._get_volume_group_info_rpc(
                self.context.snapshot_model(), cluster)

            expected = {

                host1: ["data-vg", "foo-vg"],
                host2: ["db-vg", "bar-vg"],
                host3: []
            }

            self.assertEquals(expected, actual)

    def test__get_volume_group_map(self):

        clusters = self.context.query('vcs-cluster')

        c1 = clusters[0]

        # gather hostnames and vg_names from
        hostnames = [node.hostname for node in c1.nodes]

        # unpack the list of hostnames
        host1, host2, host3 = hostnames

        cluster = get_mock_cluster_data(c1)

        # positive case where a result is returned
        with patch.object(VxvmSnapshotDataMgr, '_get_volume_group_info_rpc') \
                as mock_func:

            output = {
                host1: ["data-vg", "foo-vg"],
                host2: ["db-vg", "bar-vg"],
                host3: []
            }

            mock_func.return_value = output

            volume_group_map = self.data_manager._get_volume_group_map(
                self.context.snapshot_model, cluster
            )

            expected_map = {
                "data-vg": host1,
                "foo-vg": host1,
                "bar-vg": host2,
                "db-vg": host2,
            }

            actual_active_nodes = set()

            for vg_name, active_node in expected_map.iteritems():
                self.assertTrue(vg_name in volume_group_map)
                self.assertEquals(volume_group_map[vg_name], active_node)
                actual_active_nodes.add(active_node)

            actual_active_nodes = list(actual_active_nodes)
            expected_active_nodes = [host1, host2]

            self.assertEquals(
                len(expected_active_nodes), len(actual_active_nodes))

            self.assertTrue(all(e in actual_active_nodes
                                for e in expected_active_nodes))

        # negative case where an error is encountered
        # e.g. the dg is imported on more than one node
        with patch.object(VxvmSnapshotDataMgr, '_get_volume_group_info_rpc') \
                as mock_func:

            output = {
                host1: ["data-vg", "foo-vg"],
                host2: ["db-vg", "bar-vg"],
                host3: ["foo-vg"]
            }

            mock_func.return_value = output

            self.assertRaises(
                CallbackExecutionException,
                self.data_manager._get_volume_group_map,
                self.context.snapshot_model,
                cluster
            )

            try:
                _ = self.data_manager._get_volume_group_map(
                    self.context.snapshot_model, cluster
                )
            except CallbackExecutionException as actual:
                expected = CallbackExecutionException(
                    "DG foo-vg is imported in more than one node"
                )
                self.assertEquals(str(expected), str(actual))


class TestFileSystemData(unittest.TestCase):

    def test__eq__(self):
        fs_data = { 'snapshot_name' : '', 'present' : '', 'errors_list' : '', 'valid' : '', 'restore_in_progress': '', 'snapshot_size':'', 'snap_external' : 'false'}
        fs1 = FileSystemData('fs1', fs_data)
        fs2 = FileSystemData('fs2', fs_data)
        fs3 = FileSystemData('fs1', fs_data)
        self.assertFalse(fs1 == fs2)
        self.assertTrue(fs1 == fs3)

    def test__str__(self):
        fs_data = { 'snapshot_name' : '', 'present' : '', 'errors_list' : '', 'valid' : '', 'restore_in_progress': '', 'snapshot_size':'', 'snap_external' : 'false'}
        fs1 = FileSystemData('fs1', fs_data)
        expected = "FileSystemData('fs1', present='', valid='')"
        self.assertEqual(expected, str(fs1))
        self.assertEqual(expected, repr(fs1))

    def test_snappable_(self):
        fs_data = { 'snapshot_name' : '', 'present' : '', 'errors_list' : '', 'valid' : '', 'restore_in_progress': '', 'snapshot_size':'', 'snap_external' : 'false'}
        fs1 = FileSystemData('fs1', fs_data)
        self.assertEqual(True, fs1.is_snappable())


class TestVolumeGroupData(unittest.TestCase):

    def test__eq__(self):
        vg_data = { 'vg_name' : 'my_vg', 'active_node': '',
                    'file_systems' : {}, 'cluster_id': '', 'errors_list': []}
        vg_other_data = { 'vg_name' : 'my_other_vg', 'active_node': '',
                          'file_systems' : {}, 'cluster_id': '',
                          'errors_list': []}
        vg1 = VolumeGroupData('/volume-group/my_vg', vg_data)
        vg2 = VolumeGroupData('/volume-group/my_vg', vg_data)
        vg3 = VolumeGroupData('/volume-group/my_other_vg', vg_data)
        vg4 = VolumeGroupData('/volume-group/my_vg', vg_other_data)

        self.assertTrue(vg1 == vg2)
        self.assertFalse(vg1 == vg3)
        self.assertFalse(vg1 == vg4)

    def test__str__(self):
        vg_data = { 'vg_name' : 'my_vg', 'active_node': '', 'file_systems' :
            {}, 'cluster_id': '', 'errors_list': []}
        vg1 = VolumeGroupData('/volume-group/my_vg', vg_data)

        expected = "VolumeGroupData('/volume-group/my_vg', 'my_vg')"
        self.assertEqual(expected, str(vg1))
        self.assertEqual(expected, repr(vg1))

    def test_has_snappable_file_systems(self):
        fs_data = { 'snapshot_name' : '', 'present' : '', 'errors_list' : '', 'valid' : '', 'restore_in_progress': '', 'snapshot_size':'', 'snap_external' : 'false'}
        vg_data = { 'vg_name' : 'my_vg', 'active_node': '', 'file_systems' : { 'fs1' : fs_data }, 'cluster_id': '', 'errors_list': []}
        vg1 = VolumeGroupData('/volume-group/my_vg', vg_data)
        self.assertTrue(vg1.has_snappable_file_systems())

        fs_data['snap_external'] = 'true'
        vg1 = VolumeGroupData('/volume-group/my_vg', vg_data)
        self.assertFalse(vg1.has_snappable_file_systems())

        fs_data['snap_external'] = 'false'
        fs_data['snapshot_size'] = '0M'
        vg1 = VolumeGroupData('/volume-group/my_vg', vg_data)
        self.assertFalse(vg1.has_snappable_file_systems())


class TestClusterData(unittest.TestCase):

    def test__eq__(self):

        cluster_data = {
            'nodes' : {},
        }

        cluster_other_data = {
            'nodes' : {
                'n1': {
                    'reachable': '',
                    'errors_list': [],
                }
            },
        }

        cluster1 = ClusterData('/clusters/c1', cluster_data)
        cluster2 = ClusterData('/clusters/c2', cluster_data)
        cluster3 = ClusterData('clusters/c1', cluster_other_data)
        cluster4 = ClusterData('/clusters/c1', cluster_data)

        self.assertFalse(cluster1 == cluster2)
        self.assertFalse(cluster1 == cluster3)
        self.assertTrue(cluster1 == cluster4)

    def test__str__(self):

        cluster_data = {
            'nodes' : {},
        }

        cluster1 = ClusterData('/clusters/c1', cluster_data)

        expected = "ClusterData('/clusters/c1')"
        self.assertEqual(expected, str(cluster1))
        self.assertEqual(expected, repr(cluster1))

class TestTorf189554(unittest.TestCase):
    def setUp(self):

        self.context = VolMgrMockContext()
        self.context.snapshot_model = lambda: self.context
        self.plugin = VolMgrPlugin()

        snap = VolMgrMockSnapshot('s1')
        snap.active = 'true'

        self.c1 = VolMgrMockVCSCluster(item_id='c1', cluster_type='sfha')
        self.c2 = VolMgrMockVCSCluster(item_id='c2', cluster_type='sfha')

        n1 = VolMgrMockNode(item_id='n1', hostname='mn1')
        n2 = VolMgrMockNode(item_id='n2', hostname='mn2')
        n3 = VolMgrMockNode(item_id='n3', hostname='mn3')
        n4 = VolMgrMockNode(item_id='n4', hostname='mn4')

        sp1 = VolMgrMockStorageProfile(item_id='sp1', volume_driver='vxvm')
        sp2 = VolMgrMockStorageProfile(item_id='sp2', volume_driver='vxvm')
        sp3 = VolMgrMockStorageProfile(item_id='sp3', volume_driver='vxvm')

        vg1 = VolMgrMockVG(item_id='vg1', volume_group_name='vg1')
        vg2 = VolMgrMockVG(item_id='vg2', volume_group_name='vg2')
        vg3 = VolMgrMockVG(item_id='vg3', volume_group_name='vg3')
        vg4 = VolMgrMockVG(item_id='vg4', volume_group_name='vg4')
        vg5 = VolMgrMockVG(item_id='vg5', volume_group_name='vg5')

        self.fs1 = VolMgrMockFS(item_id='fs1', mount_point='/fs1', size='1G', type='vxfs')
        self.fs2 = VolMgrMockFS(item_id='fs2', mount_point='/fs2', size='2G', type='vxfs')
        self.fs3 = VolMgrMockFS(item_id='fs3', mount_point='/fs3', size='3G', type='vxfs')
        self.fs4 = VolMgrMockFS(item_id='fs4', mount_point='/fs4', size='4G', type='vxfs')
        self.fs5 = VolMgrMockFS(item_id='fs5', mount_point='/fs5', size='5G', type='vxfs')

        self.c1.nodes.extend([n1, n2])
        self.c2.nodes.extend([n3, n4])
        n1.get_cluster = n2.get_cluster = lambda: self.c1
        n3.get_cluster = n4.get_cluster = lambda: self.c2

        self.c1.storage_profile.append(sp1)
        self.c2.storage_profile.extend([sp2, sp3])

        self.c1.query = VolMgrMock.mock_query({
            'node': [n1, n2],
            'storage-profile': [sp1]
        })

        self.c2.query = VolMgrMock.mock_query({
            'node': [n3, n4],
            'storage-profile': [sp2, sp3]
        })

        sp1.volume_groups.extend([vg1, vg2])
        sp2.volume_groups.extend([vg3, vg4])
        sp3.volume_groups.append(vg5)

        vg1.file_systems.append(self.fs1)
        vg2.file_systems.append(self.fs2)
        vg3.file_systems.append(self.fs3)
        vg4.file_systems.append(self.fs4)
        vg5.file_systems.append(self.fs5)

        all_items = [self.c1, self.c2,
                     n1, n2, n3, n4,
                     sp1, sp2, sp3,
                     vg1, vg2, vg3, vg4, vg5,
                     self.fs1, self.fs2, self.fs3, self.fs4, self.fs5]

        VolMgrMock._set_state_applied(all_items)

        self.context.query = VolMgrMock.mock_query({
            'vcs-cluster': [self.c1, self.c2],
            'node': [n1, n2, n3, n4],
            'storage-profile': [sp1, sp2, sp3],
            'snapshot-base': [snap]
        })

        task_desc_template = 'Remove VxVM deployment snapshot "L_%s_" for cluster "%s", volume group "%s"'
        self.task1_desc = task_desc_template % (self.fs1.item_id, self.c1.cluster_id, vg1.volume_group_name)
        self.task2_desc = task_desc_template % (self.fs2.item_id, self.c1.cluster_id, vg2.volume_group_name)
        self.task3_desc = task_desc_template % (self.fs3.item_id, self.c2.cluster_id, vg3.volume_group_name)
        self.task4_desc = task_desc_template % (self.fs4.item_id, self.c2.cluster_id, vg4.volume_group_name)
        self.task5_desc = task_desc_template % (self.fs5.item_id, self.c2.cluster_id, vg5.volume_group_name)
        self.task6_desc = 'Check VxVM snapshots are currently not being restored'

        self.active_node_task_preamble = 'Check that an active node exists for each VxVM volume group on cluster(s) '

        self.context.snapshot_action = lambda: 'remove'

    def gen_tasks_and_assert_results(self, expected_descriptions):

        tasks = self.plugin.vxvm_driver.gen_tasks_for_snapshot(self.context)
        self.assertEquals(len(expected_descriptions), len(tasks))

        actual_descriptions = set([x.description for x in tasks])
        self.assertEqual(actual_descriptions, set(expected_descriptions))

    def test_create_named_snapshot(self):
        self.context.snapshot_action = lambda: 'create'

        tasks = self.plugin.vxvm_driver.gen_tasks_for_snapshot(self.context)

        self.assertEquals(5, len(tasks))

    def test_remove_named_snapshot(self):
        expected = [self.active_node_task_preamble + ('"%s" and "%s"' % (self.c2.cluster_id, self.c1.cluster_id)),
                    self.task1_desc,
                    self.task2_desc,
                    self.task3_desc,
                    self.task4_desc,
                    self.task5_desc,
                    self.task6_desc]

        self.gen_tasks_and_assert_results(expected)

    def test_remove_named_snapshot_snap_zero_first_cluster(self):
        for fs in (self.fs1, self.fs2):
            fs.current_snap_size = '0'

        expected = [self.active_node_task_preamble + ('"%s"' % self.c2.cluster_id),
                    self.task3_desc,
                    self.task4_desc,
                    self.task5_desc,
                    self.task6_desc]

        self.gen_tasks_and_assert_results(expected)

    def test_remove_named_snapshot_snap_zero_second_cluster(self):
        for fs in (self.fs3, self.fs4):
            fs.current_snap_size = '0'

        expected = [self.active_node_task_preamble + ('"%s" and "%s"' % (self.c2.cluster_id, self.c1.cluster_id)),
                    self.task1_desc,
                    self.task2_desc,
                    self.task5_desc,
                    self.task6_desc]

        self.gen_tasks_and_assert_results(expected)

        # ----
        self.fs5.current_snap_size = '0'

        expected = [self.active_node_task_preamble + ('"%s"' % self.c1.cluster_id),
                    self.task1_desc,
                    self.task2_desc,
                    self.task6_desc]

        self.gen_tasks_and_assert_results(expected)

    def test_remove_named_snapshot_snap_zero_both_cluster(self):
        for fs in (self.fs1, self.fs2, self.fs3, self.fs4, self.fs5):
            fs.current_snap_size = '0'

        tasks = self.plugin.vxvm_driver.gen_tasks_for_snapshot(self.context)

        self.assertEquals([], tasks)

    def test_remove_named_snapshot_snap_zero_and_external_both_cluster(self):
        for fs in (self.fs1, self.fs2, self.fs3, self.fs4):
            fs.current_snap_size = '0'
        self.fs5.snap_external = 'true'

        tasks = self.plugin.vxvm_driver.gen_tasks_for_snapshot(self.context)

        self.assertEquals([], tasks)
