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
import uuid

from mock import Mock, MagicMock, PropertyMock

from litp.core.exceptions import ViewError
from litp.core.plugin import Plugin

from volmgr_plugin.volmgr_plugin import SnapshotRebootPlanner

from mock_vol_items import (
    VolMgrMock,
    VolMgrMockCluster,
    VolMgrMockContext,
    VolMgrMockDeployment,
    VolMgrMockDisk,
    VolMgrMockFS,
    VolMgrMockMS,
    VolMgrMockNode,
    VolMgrMockPD,
    VolMgrMockStorageProfile,
    VolMgrMockSystem,
    VolMgrMockVG)

def an_id():
    return 'x' + str(uuid.uuid4())


class SnapshotRebootPlannerTest(unittest.TestCase):
    """
    volume group model status
    a - initial
    b - for removal
    c - applied

    VG has fs:
    A - yes
    B - no

    origin of VG:
    1 - MS
    2 - MN
    3 - VxVM MN
    4 - other

    disk is present in phisical devices of VG:
    I - yes
    II - no

    a, b, ab => False
    B x (c, ac, bc, abc) => False
    4 x (A x (c, ac, bc, abc)) => False
    II x (1, 2, 3) x A * (c, ac, bc, abc)) => False
    ===
    I * (1, 2, 3) x A * (c, ac, bc, abc)) => True
    """
    def _add_initial_vg(self):
        vg = VolMgrMockVG(an_id(), an_id())
        vg.is_initial = MagicMock(return_value=True)
        vg.is_for_removal = MagicMock(return_value=False)
        return vg

    def _add_for_removal_vg(self):
        vg = VolMgrMockVG(an_id(), an_id())
        vg.is_initial = MagicMock(return_value=False)
        vg.is_for_removal = MagicMock(return_value=True)
        return vg

    def _add_applied_vg(self, pds=[], fss=[], ms=None, node=None, cluster=None):
        vg = VolMgrMockVG(an_id(), an_id())
        vg.is_initial = MagicMock(return_value=False)
        vg.is_for_removal = MagicMock(return_value=False)
        vg.file_systems = fss
        def query(item_type_id):
            if item_type_id == 'file-system':
                return fss
        vg.query = MagicMock(side_effect=query)
        if ms is not None:
            vg.get_ms = lambda: ms
        if node is not None:
            vg.get_node = lambda: node
        if cluster is not None:
            vg.get_ms = lambda: cluster
        vg.physical_devices = pds
        return vg

    def _add_fs(self):
        fs = VolMgrMockFS(an_id(), '1G', '/mnt/' + an_id())
        return fs

    def _add_ms(self):
        ms = VolMgrMockMS()
        ms.system = VolMgrMockSystem(an_id())
        return ms

    def _add_node(self):
        node = VolMgrMockNode(an_id(), an_id())
        node.system = VolMgrMockSystem(an_id())
        return node

    def _add_cluster(self):
        node = VolMgrMockNode(an_id(), an_id())
        node.system = VolMgrMockSystem(an_id())
        cluster = MagicMock()
        cluster.nodes = [node]
        return cluster

    def _add_pd(self):
        return VolMgrMockPD(an_id(), an_id())

    def _build_cluster(self, vgs, volume_driver='lvm'):
        sp = VolMgrMockStorageProfile(an_id(), volume_driver)
        for vg in vgs:
            sp.volume_groups.append(vg)
        c =  VolMgrMockCluster(an_id())
        c.storage_profile = sp
        def query(item_type_id):
            if item_type_id == 'volume-group':
                return vgs
        c.query = MagicMock(side_effect=query)
        return c

    def _build_planner(self):
        self.planner_callbacks = {
            'reboot_ms_callback': MagicMock(return_value=None),
            'reboot_node_callback': MagicMock(return_value=None),
            'wait_node_callback': MagicMock(return_value=None),
            'force_reboot_wait_node_callback': MagicMock(return_value=None)
        }
        planner = SnapshotRebootPlanner(
            self.planner_callbacks['reboot_ms_callback'],
            self.planner_callbacks['reboot_node_callback'],
            self.planner_callbacks['wait_node_callback'],
            self.planner_callbacks['force_reboot_wait_node_callback'],
            Mock())
        return planner

    def test_cluster_should_not_reboot(self):
        planner = self._build_planner()
        # a
        c = self._build_cluster([self._add_initial_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # b
        c = self._build_cluster([self._add_for_removal_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # ab
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # B x (c, ac, bc, abc)
        # B x c
        c = self._build_cluster([self._add_applied_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # B x ac
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_applied_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # B x bc
        c = self._build_cluster([self._add_for_removal_vg(),
                                 self._add_applied_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # B x abc
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg(),
                                 self._add_applied_vg()])
        self.assertFalse(planner._should_cluster_reboot(c))
        # 4 x (A x (c, ac, bc, abc)) => False
        # cA4
        c = self._build_cluster([self._add_applied_vg(fss=[self._add_fs()])])
        self.assertFalse(planner._should_cluster_reboot(c))
        # acA4
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()])])
        self.assertFalse(planner._should_cluster_reboot(c))
        # bcA4
        c = self._build_cluster([self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()])])
        self.assertFalse(planner._should_cluster_reboot(c))
        # abcA4
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()])])
        self.assertFalse(planner._should_cluster_reboot(c))
        # II x (1, 2, 3) x A * (c, ac, bc, abc))
        # cA1II
        c = self._build_cluster([self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_ms())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # acA1II
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_ms())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # bcA1II
        c = self._build_cluster([self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_ms())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # abcA1II
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_ms())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # cA2II
        c = self._build_cluster([self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_node())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # acA2II
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_node())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # bcA2II
        c = self._build_cluster([self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_node())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # abcA2II
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_node())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # cA3II
        c = self._build_cluster([self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_cluster())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # acA3II
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_cluster())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # bcA3II
        c = self._build_cluster([self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_cluster())])
        self.assertFalse(planner._should_cluster_reboot(c))
        # abcA3II
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      ms=self._add_cluster())])
        self.assertFalse(planner._should_cluster_reboot(c))

    def test_cluster_should_reboot(self):
        # I * (1, 2, 3) x A * (c, ac, bc, abc))
        planner = self._build_planner()
        # cA1I
        c = self._build_cluster([self._add_applied_vg(fss=[self._add_fs()],
                                                      pds=[self._add_pd()],
                                                      ms=self._add_cluster())])
        self.assertTrue(planner._should_cluster_reboot(c))
        # acA1I
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      pds=[self._add_pd()],
                                                      ms=self._add_cluster())])
        self.assertTrue(planner._should_cluster_reboot(c))
        # bcA1I
        c = self._build_cluster([self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      pds=[self._add_pd()],
                                                      ms=self._add_cluster())])
        self.assertTrue(planner._should_cluster_reboot(c))
        # abcA1I
        c = self._build_cluster([self._add_initial_vg(),
                                 self._add_for_removal_vg(),
                                 self._add_applied_vg(fss=[self._add_fs()],
                                                      pds=[self._add_pd()],
                                                      ms=self._add_cluster())])
        self.assertTrue(planner._should_cluster_reboot(c))

    def test_reboot_tasks_ms(self):
        planner = SnapshotRebootPlanner(Mock(), Mock(), Mock(), Mock(), Mock())

        def get_context(is_snapshot_action_forced=False):
            m = MagicMock()
            m.is_snapshot_action_forced = MagicMock(return_value=is_snapshot_action_forced)
            ms = VolMgrMockNode(item_id='ms', hostname='ms1')
            VolMgrMock._set_state_initial([ms])
            qo = MagicMock()
            qo.query = MagicMock(return_value=[ms])
            m.snapshot_model = MagicMock(return_value=qo)
            return m

        cluster = VolMgrMockCluster(item_id='c1', cluster_type='sfha')
        cluster.nodes.extend([])

        tasks = planner._gen_tasks_for_mses(get_context(True))
        expected = [
            'Restart node "ms1"'

        ]
        ms = get_context().snapshot_model().query("ms")[0]
        # LITPCDS-12783
        self.assertTrue(ms.is_initial())
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

    def test_reboot_tasks(self):
        planner = SnapshotRebootPlanner(Mock(), Mock(), Mock(), Mock(), Mock())

        def get_context(is_snapshot_action_forced=False):
            m = MagicMock()
            m.is_snapshot_action_forced = MagicMock(return_value=is_snapshot_action_forced)
            return m

        node1 = VolMgrMockNode(item_id='n1', hostname='n1')
        node2 = VolMgrMockNode(item_id='n2', hostname='n2')
        node3 = VolMgrMockNode(item_id='n3', hostname='n3')

        nodes = [node1, node2, node3]

        cluster = VolMgrMockCluster(item_id='c1', cluster_type='sfha')
        cluster.nodes.extend(nodes)

        # set the states of all model items
        # if this is a reboot task then one would presume that the state of all
        # model items should be set to applied
        items = [cluster]
        items.extend(nodes)
        VolMgrMock._set_state_applied(items)

        tasks = planner._gen_tasks_for_cluster_reboot(get_context(), cluster)
        expected =[
            'Restart node(s) "n1", "n2" and "n3"',
            'Wait for node "n1" to restart',
            'Wait for node "n2" to restart',
            'Wait for node "n3" to restart'
        ]

        VolMgrMock.assert_task_descriptions(self, expected, tasks)

        tasks = planner._gen_tasks_for_cluster_reboot(get_context(True), cluster)
        expected = [
            'Restart and wait for nodes'

        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)

        cluster.nodes = [node1]
        tasks = planner._gen_tasks_for_cluster_reboot(get_context(), cluster)
        expected = [
            'Restart node(s) "n1"',
            'Wait for node "n1" to restart'
        ]
        VolMgrMock.assert_task_descriptions(self, expected, tasks)


    def test_multi_cluster_reboot_tasks(self):

        def reboot_ms(*args, **kwargs):
            pass

        def reboot_node(*args, **kwargs):
            pass

        def wait_node(*args, **kwargs):
            pass

        def force_reboot_wait_node(*args, **kwargs):
            pass

        def warn_invalid_cluster_order(*args, **kwargs):
            pass

        planner = SnapshotRebootPlanner(
            reboot_ms,
            reboot_node,
            wait_node,
            force_reboot_wait_node,
            warn_invalid_cluster_order)

        disk1 = VolMgrMockDisk('d1', 'sda', '8G', 'id_disk1', True)
        disk2 = VolMgrMockDisk('d2', 'sda', '8G', 'id_disk2', True)
        disk3 = VolMgrMockDisk('d3', 'sda', '8G', 'id_disk3', True)
        disk4 = VolMgrMockDisk('d4', 'sda', '8G', 'id_disk4', True)
        disk5 = VolMgrMockDisk('d5', 'sda', '8G', 'id_disk5', True)
        disk6 = VolMgrMockDisk('d6', 'sda', '8G', 'id_disk6', True)
        disk7 = VolMgrMockDisk('d7', 'sda', '8G', 'id_disk7', True)
        disk8 = VolMgrMockDisk('d8', 'sda', '8G', 'id_disk8', True)

        sys1 = VolMgrMockSystem('s1')
        sys1.disks.append(disk1)
        sys2 = VolMgrMockSystem('s2')
        sys2.disks.append(disk2)
        sys3 = VolMgrMockSystem('s3')
        sys3.disks.append(disk3)
        sys4 = VolMgrMockSystem('s4')
        sys4.disks.append(disk4)
        sys5 = VolMgrMockSystem('s5')
        sys5.disks.append(disk5)
        sys6 = VolMgrMockSystem('s6')
        sys6.disks.append(disk6)
        sys7 = VolMgrMockSystem('s7')
        sys7.disks.append(disk7)
        sys8 = VolMgrMockSystem('s8')
        sys8.disks.append(disk8)

        node1 = VolMgrMockNode('n1', 'n1')
        node1.system = sys1
        node2 = VolMgrMockNode('n2', 'n2')
        node2.system = sys2
        node3 = VolMgrMockNode('n3', 'n3')
        node3.system = sys3
        node4 = VolMgrMockNode('n4', 'n4')
        node4.system = sys4
        node5 = VolMgrMockNode('n5', 'n5')
        node5.system = sys5
        node6 = VolMgrMockNode('n6', 'n6')
        node6.system = sys6
        node7 = VolMgrMockNode('n7', 'n7')
        node7.system = sys7
        node8 = VolMgrMockNode('n8', 'n8')
        node8.system = sys8

        fs1 = VolMgrMockFS('fs1', '1G', '/')
        fs2 = VolMgrMockFS('fs2', '1G', '/')
        fs3 = VolMgrMockFS('fs3', '1G', '/')
        fs4 = VolMgrMockFS('fs4', '1G', '/')
        fs5 = VolMgrMockFS('fs5', '1G', '/')
        fs6 = VolMgrMockFS('fs6', '1G', '/')
        fs7 = VolMgrMockFS('fs7', '1G', '/')
        fs8 = VolMgrMockFS('fs8', '1G', '/')

        pd1 = VolMgrMockPD('pd1', 'sda')
        pd2 = VolMgrMockPD('pd2', 'sda')
        pd3 = VolMgrMockPD('pd3', 'sda')
        pd4 = VolMgrMockPD('pd4', 'sda')
        pd5 = VolMgrMockPD('pd5', 'sda')
        pd6 = VolMgrMockPD('pd6', 'sda')
        pd7 = VolMgrMockPD('pd7', 'sda')
        pd8 = VolMgrMockPD('pd8', 'sda')

        vg1 = VolMgrMockVG('vg1', 'vg_root', node_for_get=node1)
        vg1.file_systems.append(fs1)
        vg1.physical_devices.append(pd1)

        vg2 = VolMgrMockVG('vg2', 'vg_root', node_for_get=node2)
        vg2.file_systems.append(fs2)
        vg2.physical_devices.append(pd2)

        vg3 = VolMgrMockVG('vg1', 'vg_root', node_for_get=node3)
        vg3.file_systems.append(fs3)
        vg3.physical_devices.append(pd3)

        vg4 = VolMgrMockVG('vg1', 'vg_root', node_for_get=node4)
        vg4.file_systems.append(fs4)
        vg4.physical_devices.append(pd4)

        vg5 = VolMgrMockVG('vg1', 'vg_root', node_for_get=node5)
        vg5.file_systems.append(fs5)
        vg5.physical_devices.append(pd5)

        vg6 = VolMgrMockVG('vg1', 'vg_root', node_for_get=node6)
        vg6.file_systems.append(fs6)
        vg6.physical_devices.append(pd6)

        vg7 = VolMgrMockVG('vg7', 'vg_root', node_for_get=node7)
        vg7.file_systems.append(fs7)
        vg7.physical_devices.append(pd7)

        vg8 = VolMgrMockVG('vg1', 'vg_root', node_for_get=node8)
        vg8.file_systems.append(fs8)
        vg8.physical_devices.append(pd8)

        sp1 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp1.volume_groups.append(vg1)

        sp2 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp2.volume_groups.append(vg1)

        sp3 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp3.volume_groups.append(vg1)

        sp4 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp4.volume_groups.append(vg1)

        sp5 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp5.volume_groups.append(vg1)

        sp6 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp6.volume_groups.append(vg1)

        sp7 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp7.volume_groups.append(vg1)

        sp8 = VolMgrMockStorageProfile('sp8', 'lvm')
        sp8.volume_groups.append(vg1)

        node1.storage_profile = sp1
        node2.storage_profile = sp2
        node3.storage_profile = sp3
        node4.storage_profile = sp4
        node5.storage_profile = sp5
        node6.storage_profile = sp6
        node7.storage_profile = sp7
        node8.storage_profile = sp8

        nodes1 = [node1, node2]
        nodes2 = [node3, node4]
        nodes3 = [node5, node6]
        nodes4 = [node7, node8]

        cluster1 = VolMgrMockCluster('c1')
        cluster1.nodes.extend(nodes1)
        cluster2 = VolMgrMockCluster('c2')
        cluster2.nodes.extend(nodes2)
        cluster3 = VolMgrMockCluster('c3')
        cluster3.nodes.extend(nodes3)
        cluster4 = VolMgrMockCluster('c4')
        cluster4.nodes.extend(nodes4)

        clusters1 = [cluster1, cluster2, cluster3, cluster4]


        # Cluster Dependencies
        #
        # |      1
        # |     / \
        # V    2   \
        #      |\  |
        #      | \ |
        #      |  \|
        #      |   3
        #       \ /
        #        4
        #
        cluster1.dependency_list = 'c2,c3'
        cluster2.dependency_list = 'c3,c4'
        cluster3.dependency_list = 'c4'


        deployment1 = VolMgrMockDeployment('d1')
        deployment1.clusters.extend(clusters1)

        context1 = VolMgrMockContext()

        # set state of all model items
        items = [
            fs1,
            pd1,
            vg1,
            sp1,
            disk1, disk2, disk3, disk4, disk5, disk6, disk7, disk8,
            sys1, sys2, sys3, sys4, sys5, sys6, sys7, sys8,
            node1, node2, node3, node4, node5, node6, node7, node8,
            cluster1, cluster2, cluster3, cluster4,
            deployment1,
        ]

        VolMgrMock._set_state_applied(items)

        # mocking the ordered_clusters view, so not actually really being
        # extrapolated from declared dependency_list above - think unit test 
        # not integration test i.e. not using a real deployment with real view
        # from core here.
        type(deployment1).ordered_clusters = PropertyMock(
            return_value=[cluster4, cluster3, cluster2, cluster1])

        tasks = planner._gen_tasks_for_deployment(context1, deployment1)

        # either wait order acceptable within a cluster.
        expected1a = [
            'Restart node(s) "n1" and "n2"',
            'Wait for node "n1" to restart',
            'Wait for node "n2" to restart',
        ]

        expected1b = [
            'Restart node(s) "n1" and "n2"',
            'Wait for node "n2" to restart',
            'Wait for node "n1" to restart',
        ]

        expected2a = [
            'Restart node(s) "n3" and "n4"',
            'Wait for node "n3" to restart',
            'Wait for node "n4" to restart',
        ]

        expected2b = [
            'Restart node(s) "n3" and "n4"',
            'Wait for node "n4" to restart',
            'Wait for node "n3" to restart',
        ]

        expected3a = [
            'Restart node(s) "n5" and "n6"',
            'Wait for node "n5" to restart',
            'Wait for node "n6" to restart',
        ]

        expected3b = [
            'Restart node(s) "n5" and "n6"',
            'Wait for node "n6" to restart',
            'Wait for node "n5" to restart',
        ]

        expected4a = [
            'Restart node(s) "n7" and "n8"',
            'Wait for node "n7" to restart',
            'Wait for node "n8" to restart',
        ]

        expected4b = [
            'Restart node(s) "n7" and "n8"',
            'Wait for node "n8" to restart',
            'Wait for node "n7" to restart',
        ]


        actual_ordered = [task.description for task in tasks]
        actual_ordered4 = actual_ordered[0:3]
        actual_ordered3 = actual_ordered[3:6]
        actual_ordered2 = actual_ordered[6:9]
        actual_ordered1 = actual_ordered[9:12]

        self.assertTrue(actual_ordered4 in (expected4a, expected4b), tasks)
        self.assertTrue(actual_ordered3 in (expected3a, expected3b), tasks)
        self.assertTrue(actual_ordered2 in (expected2a, expected2b), tasks)
        self.assertTrue(actual_ordered1 in (expected1a, expected1b), tasks)

        # after a ViewError from the deployment.ordered_clusters View,
        # inter-cluster order is not enforced, but all expected tasks should
        # be present, and intra-cluster, tasks should still be in the correct
        # order relative to eachother.

        type(deployment1).ordered_clusters = PropertyMock(
            side_effect=ViewError)

        tasks = planner._gen_tasks_for_deployment(context1, deployment1)

        actual = [task.description for task in tasks]

        self.assertTrue(
            (expected1a == [d for d in actual if d in expected1a]) or
            (expected1b == [d for d in actual if d in expected1b]), tasks)

        self.assertTrue(
            (expected2a == [d for d in actual if d in expected2a]) or
            (expected2b == [d for d in actual if d in expected2b]), tasks)

        self.assertTrue(
            (expected3a == [d for d in actual if d in expected3a]) or
            (expected3b == [d for d in actual if d in expected3b]), tasks)

        self.assertTrue(
            (expected4a == [d for d in actual if d in expected4a]) or
            (expected4b == [d for d in actual if d in expected4b]), tasks)


if __name__ == '__main__':
    unittest.main()
