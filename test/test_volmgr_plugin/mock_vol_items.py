##############################################################################
# COPYRIGHT Ericsson AB 2015
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################
from mock import Mock
from litp.core.litp_logging import LitpLogger
from litp.core.task import OrderedTaskList

log = LitpLogger()


class VolMgrMock(Mock):

    def __init__(
            self,
            item_type_id,
            item_id,
            ms_for_get=None,
            node_for_get=None,
            cluster_for_get=None
    ):

        super(VolMgrMock, self).__init__(item_id=item_id,
                                         item_type_id=item_type_id)

        self.ms_for_get = ms_for_get
        self.node_for_get = node_for_get
        self.cluster_for_get = cluster_for_get

        self.properties = {}
        self.applied_properties = {}
        self.property_names = []
        self.get_vpath = lambda: "/%s/%s" % (self.item_type_id, self.item_id)
        self.get_source = lambda: self
        self.vpath = self.get_vpath()
        self.collections = {}
        self.subitems = {}

    def get_ms(self):
        return self.ms_for_get

    def get_node(self):
        return self.node_for_get

    def get_cluster(self):
        return self.cluster_for_get

    @staticmethod
    def filter_on_item_properties(items, **properties):

        if not properties:
            return items

        filtered_items = []

        # iterate items from the model
        for item in items:
            # iterate query attributes and values
            for attr, value in properties.iteritems():

                # if the item has the attribute and it equals the value
                if getattr(item, attr) == value:
                    filtered_items.append(item)

        return filtered_items

    def query(self, item_type_id):
        return []

    @staticmethod
    def mock_query(items):

        def query(item_type, **properties):
            return VolMgrMock.filter_on_item_properties(
                items.get(item_type, []), **properties)

        return query

        self.is_initial = lambda: True
        self.is_updated = lambda: False
        self.is_for_removal = lambda: False

    @staticmethod
    def filter_on_item_properties(items, **properties):

        if not properties:
            return items

        filtered_items = []

        # iterate items from the model
        for item in items:
            # iterate query attributes and values
            for attr, value in properties.iteritems():

                # if the item has the attribute and it equals the value
                if getattr(item, attr) == value:
                    filtered_items.append(item)

        return filtered_items

    def query(self, item_type_id):
        return []

    @staticmethod
    def mock_query(items):

        def query(item_type, **properties):
            return VolMgrMock.filter_on_item_properties(
                items.get(item_type, []), **properties)

        return query

    @staticmethod
    def set_properties(item):
        for prop_name in item.property_names:
            item.properties[prop_name] = getattr(item, prop_name)

    @staticmethod
    def set_applied_properties(item):
        for prop_name in item.property_names:
            item.applied_properties[prop_name] = getattr(item, prop_name)

    @staticmethod
    def _set_state_xxx(items, state):
        for item in items:
            if not isinstance(item, VolMgrMock):
                raise Exception('Invalid Mock item', item)

            VolMgrMock.set_properties(item)

            if 'applied' == state:
                item.is_applied = lambda: True
                item.is_for_removal = lambda: False
                item.is_updated = lambda: False
                item.is_initial = lambda: False
                VolMgrMock.set_applied_properties(item)
            elif 'removed' == state:
                item.is_applied = lambda: False
                item.is_for_removal = lambda: True
                item.is_updated = lambda: False
                item.is_initial = lambda: False
            # removed will be removed and for_removal used instead
            elif 'for_removal' == state:
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
        VolMgrMock._set_state_xxx(items, 'applied')

    @staticmethod
    def _set_state_updated(items):
        VolMgrMock._set_state_xxx(items, 'updated')

    @staticmethod
    def _set_state_initial(items):
        VolMgrMock._set_state_xxx(items, 'initial')

    @staticmethod
    def _set_state_removed(items):
        VolMgrMock._set_state_xxx(items, 'removed')

    @staticmethod
    def _set_state_for_removal(items):
        VolMgrMock._set_state_xxx(items, 'for_removal')

    @staticmethod
    def log_trace(preamble, expected, actual):

        log.trace.debug("")
        log.trace.debug("%s" % preamble)
        log.trace.debug("")

        log.trace.debug("Expected Output :")
        log.trace.debug("-----------------")
        log.trace.debug("")

        for idx, exp in enumerate(expected):
            log.trace.debug("%d. %s" % ((idx + 1), exp))

        log.trace.debug("")
        log.trace.debug("Actual Output :")
        log.trace.debug("---------------")
        log.trace.debug("")

        for idx, act in enumerate(actual):
            log.trace.debug("%d. %s" % ((idx + 1), act))

        log.trace.debug("")

    @staticmethod
    def assert_validation_errors(test, expected, errors):

        preamble = "VolMgrMock.assert_validation_errors()"

        test.assertTrue(
            len(expected) <= len(errors),
            preamble + " less errors were generated than expected ..."
        )

        VolMgrMock.log_trace(preamble, expected, errors)

        all_present = all([e in errors for e in expected])

        test.assertTrue(
            all_present,
            preamble + "all expected errors were not generated ..."
        )

    @staticmethod
    def __recursive_task_list_flatten(items, flattened):
        for item in items:
            if isinstance(item, OrderedTaskList):
                VolMgrMock.__recursive_task_list_flatten(
                    item.task_list, flattened
                )
            else:
                flattened.append(item)

    @staticmethod
    def flatten_tasks(tasks):
        task_list = []
        VolMgrMock.__recursive_task_list_flatten(tasks, task_list)
        return task_list

    @staticmethod
    def assert_tasks(test, expected, tasks, attr, equal=False):
        """
        Determine if the expected tasks exist in the tasks generated.
        :param test: The test in progress.
        :type test: Class object
        :param expected: List of expected tasks.
        :type expected: list
        :param tasks: List of generated tasks.
        :type tasks: list
        :param attr: Attribute we are looking for from the tasks.
        :type attr: string
        :param equal: Set this to True if the generated tasks should
        contain only the expected tasks.
        :type equal: boolean
        """

        preamble = "VolMgrMock.assert_tasks(attr='%s')" % attr

        flattened = VolMgrMock.flatten_tasks(tasks)

        # select the attribute we are looking for from the tasks
        attributes = [getattr(task, attr) for task in flattened]
        VolMgrMock.log_trace(preamble, expected, attributes)

        # assert we have the expected response
        test.assertTrue(
            len(expected) <= len(attributes),
            preamble + " expected more tasks to be generated ..."
        )

        # assert all expected responses are present
        all_present = all([e in attributes for e in expected])

        test.assertTrue(
            all_present,
            preamble + " all expected tasks were not generated ..."
        )

        if equal:

            # assert only the expected responses are present
            all_present = all([e in expected for e in attributes])

            test.assertTrue(
                all_present,
                preamble + " at least one unwanted task was generated ..."
            )

    @staticmethod
    def assert_task_descriptions(test, expected, tasks, equal=False):
        VolMgrMock.assert_tasks(
            test, expected, tasks, 'description', equal)

    @staticmethod
    def assert_task_call_types(test, expected, tasks, equal=False):
        VolMgrMock.assert_tasks(
            test, expected, tasks, 'call_type', equal)

    @staticmethod
    def assert_task_kwargs(test, expected, tasks, equal=False):
        VolMgrMock.assert_tasks(
            test, expected, tasks, 'kwargs', equal)

    def query(self, arg, **kwargs):
        # this does not currently handle sub- item-types
        # this does not currently handle filtering kwargs
        items = []
        if self.collections.has_key(arg):
            items.extend(getattr(self, self.collections[arg]))
        for coll_attr in self.collections.values():
            coll = getattr(self, coll_attr)
            for child_item in coll:
                items.extend(child_item.query(arg, **kwargs))
        if self.subitems.has_key(arg):
            items.extend(getattr(self, self.subitems[arg]))
        for subitem_attr in self.subitems.values():
            child_item = getattr(self, subitem_attr)
            items.extend(child_item.query(arg, **kwargs))
        # print "%s.query('%s') -> %s" % (self, arg, items)
        return items


class VolMgrMockCluster(VolMgrMock):

    def __init__(
            self,
            item_id,
            cluster_type='',
            dependency_list=None,
            item_type_id='cluster',
            grub_lv_enable='false'
            ):

        super(VolMgrMockCluster, self).__init__(
            item_type_id=item_type_id, item_id=item_id
        )

        self.cluster_type = cluster_type
        self.dependency_list = dependency_list
        self.cluster_id = item_id
        self.grub_lv_enable = grub_lv_enable
        self.property_names = ['cluster_type', 'dependency_list', 'grub_lv_enable']

        # these are all collections
        self.storage_profile = []
        self.software = []
        self.nodes = []
        self.services = []
        self.software = []
        self.collections['storage-profile'] = 'storage_profile'
        self.collections['software'] = 'software'
        self.collections['node'] = 'nodes'
        self.collections['service'] = 'services'
        self.collections['software'] = 'software'


class VolMgrMockVCSCluster(VolMgrMockCluster):

    def __init__(self, item_id, cluster_type='', dependency_list=None):

        super(VolMgrMockVCSCluster, self).__init__(
            item_type_id='vcs-cluster',
            cluster_type=cluster_type,
            dependency_list=dependency_list,
            item_id=item_id
        )
        self.fencing_disks = []


class VolMgrMockNode(VolMgrMock):

    def __init__(self, item_id, hostname, item_type_id='node'):

        super(VolMgrMockNode, self).__init__(
            item_type_id=item_type_id,
            item_id=item_id
        )

        self.hostname = hostname
        self.property_names = ['hostname']

        self.system = None
        self.storage_profile = None
        self.subitems['system'] = 'system'
        self.subitems['storage_profile'] = 'storage_profile'

    def is_ms(self):
        return self.item_type_id is 'ms'


class VolMgrMockMS(VolMgrMockNode):

    def __init__(self):

        super(VolMgrMockMS, self).__init__(
            item_id='ms',
            hostname='ms1',
            item_type_id='ms'
        )


class VolMgrMockSystem(VolMgrMock):
    def __init__(self, item_id, system_name='default_system'):
        super(VolMgrMockSystem, self).__init__(
            item_type_id='system', item_id=item_id)
        self.system_name = system_name
        self.disks = []
        self.collections['disk'] = 'disks'


class VolMgrMockOS(VolMgrMock):
    def __init__(self, item_id, version='rhel6'):
        super(VolMgrMockOS, self).__init__(
            item_type_id='os-profile', item_id=item_id)
        self.version = version

class VolMgrMockStorageProfile(VolMgrMock):

    def __init__(
            self,
            item_id,
            volume_driver,
            ms_for_get=None,
            node_for_get=None,
            cluster_for_get=None
    ):

        super(VolMgrMockStorageProfile, self).__init__(
            item_type_id='storage-profile',
            item_id=item_id,
            ms_for_get=ms_for_get,
            node_for_get=node_for_get,
            cluster_for_get=cluster_for_get
        )

        self.volume_driver = volume_driver
        self.property_names = ['volume_driver']
        self.view_root_vg = ''
        self.volume_groups = []
        self.collections['volume-group'] = 'volume_groups'


class VolMgrMockDisk(VolMgrMock):

    def __init__(
            self, item_id, name, size, uuid, bootable, item_type_id='disk'):

        super(VolMgrMockDisk, self).__init__(
            item_type_id=item_type_id, item_id=item_id)

        self.name = name
        self.size = size
        self.uuid = uuid
        self.bootable = bootable
        self.disk_part = 'false'
        self.disk_fact_name = '$::disk_abcd_dev'
        self.disk_group = 'dg'

        self.property_names = ['name', 'size', 'uuid', 'bootable', 'disk_part']


class VolMgrMockOtherDisk(VolMgrMockDisk):

    def __init__(
            self, item_id, name, size,
            uuid, bootable, item_type_id='lun-disk'):

        super(VolMgrMockOtherDisk, self).__init__(
            item_id, name, size, uuid, bootable, item_type_id=item_type_id)


class VolMgrMockPD(VolMgrMock):
    def __init__(self, item_id, device_name):
        super(VolMgrMockPD, self).__init__(
            item_type_id='physical-device',
            item_id=item_id
        )

        self.device_name = device_name

        self.property_names = ['device_name']


class VolMgrMockVG(VolMgrMock):

    def __init__(
            self,
            item_id,
            volume_group_name,
            ms_for_get=None,
            node_for_get=None,
            cluster_for_get=None
    ):

        super(VolMgrMockVG, self).__init__(
            item_type_id='volume-group',
            item_id=item_id,
            ms_for_get=ms_for_get,
            node_for_get=node_for_get,
            cluster_for_get=cluster_for_get
        )

        self.volume_group_name = volume_group_name
        self.file_systems = []
        self.physical_devices = []

        self.property_names = ['volume_group_name']

        self.collections['file-system'] = 'file_systems'
        self.collections['physical-device'] = 'physical_devices'


class VolMgrMockFS(VolMgrMock):
    def __init__(self, item_id, size, mount_point=None,
                 snap_size='100', snap_external='false',
                 type='ext4', fsck_pass='2', mount_options='defaults'):
        super(VolMgrMockFS, self).__init__(
            item_type_id='file-system',
            item_id=item_id
        )
        self.size = size
        self.snap_size = snap_size
        self.current_snap_size = snap_size
        self.backup_snap_size = None
        self.snap_external = snap_external
        self.mount_point = mount_point
        self.type = type
        self.fsck_pass = fsck_pass
        self.mount_options = mount_options

        self.property_names = ['size', 'snap_size', 'snap_external', 'fsck_pass',
                               'type', 'mount_point', 'current_snap_size', 'backup_snap_size', 'mount_options']

    def get_ms(self):
        return None

    def get_cluster(self):
        return None


class GlobalProperty(VolMgrMock):
    def __init__(self):
        super(GlobalProperty, self).__init__(item_type_id='', item_id='')
        self.key = ''
        self.value = ''


class ConfigManager(VolMgrMock):
    def __init__(self):
        super(ConfigManager, self).__init__(item_type_id='', item_id='')
        self.global_properties = [GlobalProperty()]


class VolMgrMockContext(VolMgrMock):
    def __init__(self):
        super(VolMgrMockContext, self).__init__(item_type_id='', item_id='')
        self.rpc_command = None
        self.snapshot_object = None
        self.exclude_nodes = []
        self.config_manager = ConfigManager()

    def query(self, item_type):
        if item_type == 'config-manager':
            return [self.config_manager]
        else:
            return None

    def snapshot_model(self):
        return None

    def snapshot_name(self):
        return ''

    def snapshot_action(self):
        return ''

    def is_snapshot_action_forced(self):
        return False


class VolMgrMockDeployment(VolMgrMock):
    def __init__(self, item_id, item_type_id='deployment'):
        super(VolMgrMockDeployment, self).__init__(
            item_type_id=item_type_id, item_id=item_id
        )
        self.clusters = []
        self.collections['cluster'] = 'clusters'


class MockCallbackTask(Mock):

    def __init__(self, model_item, description, callback, *args, **kwargs):
        super(MockCallbackTask, self).__init__(
            model_item=model_item,
            description=description,
            callback=callback,
            args=args,
            kwargs=kwargs)

    def _get_child_mock(self, **kwargs):
        return Mock(**kwargs)


class MockConfigTask(Mock):

    def __init__(
            self, node, model_item, description, call_type, call_id, **kwargs):
        super(MockConfigTask, self).__init__(
            node=node,
            model_item=model_item,
            description=description,
            call_type=call_type,
            call_id=call_id,
            persist=True,
            replaces=set(),
            kwargs=kwargs)

    def _get_child_mock(self, **kwargs):
        return Mock(**kwargs)


class VolMgrMockSnapshot(VolMgrMock):
    def __init__(self, item_id):
        super(VolMgrMockSnapshot, self).__init__(
            item_type_id='snapshot', item_id=item_id)
        self.active = 'false'
        self.force = 'false'

