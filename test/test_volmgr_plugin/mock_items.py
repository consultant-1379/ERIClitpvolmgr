##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################
from mock import MagicMock

class MockDisk(MagicMock):
    def __init__(self, item_id, name, size, uuid, bootable):
        super(MockDisk, self).__init__()
        self.item_type_id = 'disk'
        self.item_id = item_id
        self.name = name
        self.size = size
        self.uuid = uuid
        self.disk_part = "false"
        self.disk_fact_name = '$::disk_abcd_dev',
        self.bootable = bootable
        self.properties = {
            "name": name,
            "size": size,
            "uuid": uuid,
            "disk_part": "false",
            "bootable": bootable
        }
        self.get_vpath = MagicMock(return_value="/a/b/c")

        self.is_initial = MagicMock(return_value=True)
        self.is_for_removal = MagicMock(return_value=False)
        self.is_applied = MagicMock(return_value=False)
        self.is_updated = MagicMock(return_value=False)


class MockCluster(MagicMock):
    def __init__(self, item_id, cluster_type=False, *a, **kw):
        super(MockCluster, self).__init__(*a, **kw)
        self.item_id = item_id
        self.item_type_id = 'cluster'
        self.cluster_type = cluster_type
        self.storage_profile = []

        # these are all collections
        self.software = []
        self.nodes = []
        self.services = []
        self.software = []

        # mock functions to return a predetermined type
        self.get_vpath = MagicMock(return_value="/my/path")
        self.cluster_id = kw.get('cluster_id', 'c1')


class MockPd(MagicMock):
    def __init__(self, item_id, device_name):
        super(MockPd, self).__init__()
        self.item_id = item_id
        self.item_type_id = 'physical-device'
        self.device_name = device_name
        self.get_vpath = MagicMock(return_value="/my/path")
        self.get_vpath.device_name = self.device_name
        self.get_source = MagicMock(return_value=self.get_vpath)

        self.is_initial = MagicMock(return_value=True)
        self.is_for_removal = MagicMock(return_value=False)
        self.is_applied = MagicMock(return_value=False)
        self.is_updated = MagicMock(return_value=False)


class MockVG(MagicMock):
    def __init__(self, item_id, volume_group_name):
        super(MockVG, self).__init__()
        self.item_id = item_id
        self.item_type_id = 'volume-group'
        self.volume_group_name = volume_group_name
        self.file_systems = []
        self.physical_devices = []
        self.get_vpath = MagicMock(return_value="/my/path")

        self.is_initial = MagicMock(return_value=True)
        self.is_for_removal = MagicMock(return_value=False)
        self.is_applied = MagicMock(return_value=False)
        self.is_updated = MagicMock(return_value=False)


class MockStorageProfile(MagicMock):
    def __init__(self, item_id, volume_driver):
        super(MockStorageProfile, self).__init__()
        self.item_type_id = 'storage-profile'
        self.item_id = item_id
        self.volume_driver = volume_driver
        self.volume_groups = MagicMock()
        self.get_vpath = MagicMock(return_value="/my/path")


class MockFs(MagicMock):
    def __init__(self, item_id, size, snap_size, snap_external='false'):
        super(MockFs, self).__init__()
        self.item_id = item_id
        self.item_type_id = 'file-system'
        self.size = size
        self.snap_size = snap_size
        self.snap_external = snap_external
        self.get_vpath = MagicMock(return_value="/my/path")

        self.is_initial = MagicMock(return_value=True)
        self.is_for_removal = MagicMock(return_value=False)
        self.is_applied = MagicMock()
        self.is_updated = MagicMock(return_value=False)

    def get_ms(self):
        return None

    def get_cluster(self):
        return None


class MockSystem(object):
    def __init__(self, item_id, system_name):
        super(MockSystem, self).__init__()
        self.item_id = item_id
        self.system_name = system_name
        self.controllers = []
        self.disks = []

        self.is_initial = MagicMock(return_value=True)
        self.is_for_removal = MagicMock(return_value=False)
        self.is_applied = MagicMock(return_value=False)
        self.is_updated = MagicMock(return_value=False)


class MockNode(MagicMock):
    def __init__(self, item_id, hostname, is_ms=False, *a, **kw):
        super(MockNode, self).__init__(*a, **kw)
        self.item_id = item_id
        self.item_type_id = 'node'
        self.is_ms = MagicMock(return_value=self.item_type_id == 'ms')
        self.hostname = hostname
        self.system = MagicMock()
        self.storage_profile = MagicMock(return_value=None)
        self.get_vpath = MagicMock(return_value="/my/path")
        self.get_vpath.vpath = self.get_vpath
        self.get_cluster = MagicMock(return_value=self.get_vpath)

        self.is_applied = MagicMock(return_value=True)
        self.is_ms = MagicMock(return_value=is_ms)
        self.is_for_removal = MagicMock(return_value = False)
        self.is_updated = MagicMock(return_value = False)
        self.is_initial = MagicMock(return_value = False)
