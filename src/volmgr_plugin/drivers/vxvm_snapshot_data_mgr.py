##############################################################################
# COPYRIGHT Ericsson AB 2015
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################


from litp.core.litp_logging import LitpLogger

from litp.core.execution_manager import CallbackExecutionException

from volmgr_plugin.volmgr_utils import VolMgrUtils

from litp.core.rpc_commands import (
    RpcExecutionException,
    RpcCommandOutputProcessor,
    RpcCommandProcessorBase,
)

import json
import copy

log = LitpLogger()

VXVM_AGENT = "vxvm"
SNAPSHOT_AGENT = "vxvmsnapshot"


class FileSystemData(object):

    def __init__(self, fs_item_id, fs_data):

        self.id = fs_item_id

        self.snapshot_name = fs_data['snapshot_name']
        self.snapshot_size = fs_data['snapshot_size']
        self.valid = fs_data['valid']
        self.present = fs_data['present']
        self.errors_list = fs_data['errors_list']
        self.restore_in_progress = fs_data['restore_in_progress']
        self.snap_external = fs_data['snap_external']

    def __eq__(self, other):
        return self.id == other.id

    def as_string(self):
        return "%s('%s', present='%s', valid='%s')" % \
               (self.__class__.__name__, self.id, self.present, self.valid)

    def __str__(self):
        return self.as_string()

    def __repr__(self):
        return self.as_string()

    def is_snappable(self):
        return self.snap_external == 'false' and self.snapshot_size != '0M'


class VolumeGroupData(object):

    def __init__(self, vpath, vg_data):

        self.id = vpath

        self.name = vg_data['vg_name']
        self.active_node = vg_data['active_node']
        self.cluster_id = vg_data['cluster_id']
        self.errors_list = vg_data['errors_list']
        self.file_systems = [FileSystemData(fs_id, fs_data)
                             for fs_id, fs_data
                             in vg_data['file_systems'].iteritems()]

    def get_file_system(self, fs_id):
        return [fs for fs in self.file_systems if fs.id == fs_id][0]

    def __eq__(self, other):
        return self.id == other.id and self.name == other.name

    def as_string(self):
        return "%s('%s', '%s')" % (self.__class__.__name__, self.id, self.name)

    def __str__(self):
        return self.as_string()

    def __repr__(self):
        return self.as_string()

    def get_active_node(self):
        if self.active_node:
            return self.active_node
        else:
            return None

    def has_snappable_file_systems(self):
        return any(fs.is_snappable() for fs in self.file_systems)


class NodeData(object):

    def __init__(self, hostname, node_data):

        self.id = hostname
        self.reachable = node_data['reachable']
        self.errors_list = node_data['errors_list']

    def is_reachable(self):
        return self.reachable

    def as_string(self):
        return "%s('%s')" % (self.__class__.__name__, self.id)

    def __str__(self):
        return self.as_string()

    def __repr__(self):
        return self.as_string()


class ClusterData(object):

    def __init__(self, cluster_item_id, cluster_data):

        self.id = cluster_item_id

        self.nodes = [
            NodeData(node_id, node_data)
            for node_id, node_data in cluster_data['nodes'].iteritems()]

    def __eq__(self, other):
        return self.id == other.id \
               and all([hostname in other.get_hostnames()
                        for hostname in self.get_hostnames()])

    def is_reachable(self):
        return any([node.reachable for node in self.nodes])

    def get_hostnames(self):
        return [node.id for node in self.nodes]

    def as_string(self):
        return "%s('%s')" % (self.__class__.__name__, self.id)

    def __str__(self):
        return self.as_string()

    def __repr__(self):
        return self.as_string()


class VxvmSnapshotDataMgr(object):
    """
        VxvmSnapshotDataMgr is a container for managing data extracted from a
        snapshot model and organized so that it is easily referenced
        CallbackTasks.

        Some CallbackTasks may need to use the whole structure, while others
        may need the accessor methods to query the data and return views and
        subsets of the data for specific tasks related to volume groups and
        file systems.

        Example of iterating over the data in the data manager

        for cid in data_manager.get_cluster_ids():
            for vg in data_manager.get_cluster_volume_groups(cid):
                for fs in data_manger.get_volume_group_file_systems(vg):

                    hostname = vg.active_node
                    vg_name = vg.name
                    old_snap_name = fs.L_snap_name
                    new_snap_name = fs.litp_snap_name

    """

    def __init__(self, driver, context=None, data_set=None):
        """
        1 - If called when a valid context is available (eg, creating tasks),
            then that context can be passed and the internal data_set
            is populated with the snapshot_model in that context.

            data_manager = VxvmSnapshotDataMgr(context=context)

        2 - If a data_set dictionary is available (eg, when a callback is
            called and no object can be passed), the data_manager object can
            be rebuilt by just creating a new object with that data_set as
            argument.

            data_manager = VxvmSnapshotDataMgr(data_set=data_set)
        """

        self.driver = driver
        self.context = context
        self.filters = {}
        self.exclude_node_hostnames = []

        self.data = {
            'snapshot_name': '',
            'clusters': {},
            'volume_groups': {},
            'filter': {},
            'action_forced': '',
            'action': ''
        }

        self.set_filters()

        if self.context:
            self._set_action_forced(self.context.is_snapshot_action_forced())
            self._set_exclude_node_hostnames()
            self.set_snapshot_name(self.context.snapshot_name())
            self._populate()
        elif data_set:
            self.data = data_set

    def _set_exclude_node_hostnames(self):
        if self.context.snapshot_action() in ('create', 'remove') and \
           self.context.exclude_nodes:
            self.exclude_node_hostnames = \
                [node.hostname for node in self.context.exclude_nodes]

    def _set_action_forced(self, value):
        self.data['action_forced'] = str(value)

    def get_action_forced(self):
        return self.data['action_forced'].lower() == 'true'

    def _set_action(self, value):
        self.data['action'] = value

    def get_action(self):
        return self.data['action']

    def set_filters(self, cluster_ids=None, vg_vpaths=None, fs_ids=None):

        self.filters['cluster_ids'] = cluster_ids if cluster_ids else []
        self.filters['vg_vpaths'] = vg_vpaths if vg_vpaths else []
        self.filters['fs_ids'] = fs_ids if fs_ids else []

    def get_filters(self):
        return self.filters

    def reset_filters(self):
        self.filters = {}

    def get_snapshot_name(self):
        return self.data['snapshot_name']

    def set_snapshot_name(self, name):
        self.data['snapshot_name'] = name

    def get_data_set(self, make_deep_copy=False):
        if make_deep_copy:
            return copy.deepcopy(self.data)
        else:
            return self.data

    def _is_in_the_filter(self, filter_name, value):
        the_filter = self.filters.get(filter_name)
        if not the_filter:
            return True
        else:
            return value in the_filter

    def _populate(self):

        action = self.context.snapshot_action()
        self._set_action(action)

        if action == 'create':
            vcs_vxvm_clusters = [
                cluster for cluster in self.context.query('vcs-cluster')
                if not cluster.is_initial()
                and cluster.query('storage-profile', volume_driver='vxvm')]

        else:
            # can assume we have a valid snapshot model by this point
            snapshot_model = self.context.snapshot_model()
            vcs_clusters = VolMgrUtils.get_intersection_items_by_itemtype(
                snapshot_model,
                self.context,
                'vcs-cluster'
            )

            vcs_vxvm_clusters = [
                cluster for cluster in vcs_clusters
                if not cluster.is_initial()
                and cluster.query('storage-profile', volume_driver='vxvm')]

        snap_name = self.data['snapshot_name']

        for cluster in vcs_vxvm_clusters:

            current_cluster = {'nodes': {}}
            current_cluster_vgs = []

            for node in cluster.nodes:
                if node.is_initial():
                    continue

                if node.hostname in self.exclude_node_hostnames:
                    log.trace.debug("Excluding node %s" % node.hostname)
                else:
                    current_cluster['nodes'][node.hostname] = {'reachable': '',
                                                              'errors_list': []
                                                              }

            for sp in cluster.storage_profile:
                if sp.is_initial():
                    continue

                for vg in sp.volume_groups:
                    if vg.is_initial():
                        continue

                    current_vg = {'cluster_id': cluster.item_id,
                                  'sp_item_id': sp.item_id,
                                  'vg_name': vg.volume_group_name,
                                  'active_node': '',
                                  'file_systems': {},
                                  'errors_list': []
                                 }

                    for fs in vg.file_systems:
                        if fs.is_initial():
                            continue

                        if (action in ('create') and not
                          self.driver._should_snapshot_fs(fs, vg, snap_name)) \
                        or \
                         (action in ('remove', 'restore') and not
                          self.driver._can_delete_or_restore_snapshot(
                              vg, fs, snap_name)):
                            continue

                        ss = VolMgrUtils.gen_snapshot_name(fs.item_id,
                                                           snap_name)

                        snapshot_size = VolMgrUtils.\
                                        compute_snapshot_size(fs, 'vxvm')

                        current_vg['file_systems'][fs.item_id] = {
                                            'snapshot_name': ss,
                                            'present': '',
                                            'valid': '',
                                            'snapshot_size': snapshot_size,
                                            'snap_external': fs.snap_external,
                                            'restore_in_progress': '',
                                            'errors_list': []
                                           }
                    if len(current_vg['file_systems'].keys()) > 0:
                        self.data['volume_groups'][vg.get_vpath()] = current_vg
                        current_cluster_vgs.append(current_vg)

            if len(current_cluster_vgs) > 0:
                self.data['clusters'][cluster.item_id] = current_cluster

    def get_cluster_ids(self):
        return self.data['clusters'].keys()

    def get_cluster_by_id(self, cluster_id):
        return self.data['clusters'].get(cluster_id)

    def get_cluster_hostnames(self, cluster_id):

        cluster = self.get_cluster_by_id(cluster_id)
        hostnames = cluster['nodes'].keys()

        return hostnames

    def get_clusters(self):
        clusters = [ClusterData(cluster_id, cluster_data)
                    for cluster_id, cluster_data
                    in self.data['clusters'].iteritems()]

        return clusters

    def _has_reachable_nodes(self, cluster):
        nodes = self.data['clusters'][cluster.id]['nodes']
        return any([node_data['reachable'] for node_data in nodes.values()])

    def _get_unreachable_nodes(self, cluster):
        nodes = self.data['clusters'][cluster.id]['nodes']
        return [node for node, val in nodes.iteritems()
                       if not val['reachable']]

    def get_all_unreachable_nodes(self):
        all_nodes = []
        for cluster in self.get_clusters():
            all_nodes = all_nodes + self._get_unreachable_nodes(cluster)
        return all_nodes

    def _get_reachable_nodes(self, cluster):
        nodes = self.data['clusters'][cluster.id]['nodes']
        return [
            NodeData(node_id, node_data)
            for node_id, node_data in nodes.iteritems()
            if node_data['reachable']
        ]

    def _get_first_reachable_node(self, cluster):
        nodes = self._get_reachable_nodes(cluster)
        if nodes:
            return nodes[0].id
        return None

    def _set_node_reachable(self, cluster, hostname):
        cluster_data = self.data['clusters'][cluster.id]
        cluster_data['nodes'][hostname]['reachable'] = True

    def _set_node_unreachable(self, cluster, hostname):
        cluster_data = self.data['clusters'][cluster.id]
        cluster_data['nodes'][hostname]['reachable'] = False

    def _add_node_error(self, cluster, hostname, error):
        cluster_data = self.data['clusters'][cluster.id]
        cluster_data['nodes'][hostname]['errors_list'].append(error)

    def _add_node_errors(self, cluster, hostname, error_list):
        cluster_data = self.data['clusters'][cluster.id]
        cluster_data['nodes'][hostname]['errors_list'].extend(error_list)

    def get_node_errors(self, cluster, hostname):
        cluster_data = self.data['clusters'][cluster.id]
        return cluster_data['nodes'][hostname]['errors_list']

    def get_all_volume_groups(self):
        return [VolumeGroupData(vpath, vg_data)
                for vpath, vg_data in self.data['volume_groups'].iteritems()]

    def get_volume_groups(self, cluster):
        return [VolumeGroupData(vpath, vg_data)
                for vpath, vg_data in self.data['volume_groups'].iteritems()
                if vg_data['cluster_id'] == cluster.id]

    def get_cluster_volume_groups(self, cluster_id):
        return [VolumeGroupData(vpath, vg_data)
                for vpath, vg_data in self.data['volume_groups'].iteritems()
                if vg_data['cluster_id'] == cluster_id]

    def get_volume_group(self, vg_vpath):
        vg_data = self.data['volume_groups'].get(vg_vpath)
        if vg_data:
            return VolumeGroupData(vg_vpath, vg_data)
        else:
            return None

    def set_active_node(self, volume_group, hostname):

        vpath = volume_group.id

        if vpath in self.data['volume_groups']:
            self.data['volume_groups'][vpath]['active_node'] = hostname
        else:
            err_msg = '{0} does not exist in data manager'.format(volume_group)
            raise KeyError(err_msg)

    def get_active_node(self, volume_group):

        vpath = volume_group.id

        if vpath in self.data['volume_groups']:
            return self.data['volume_groups'][vpath]['active_node']
        else:
            return None

    def get_volume_group_filesystems(self, volume_group):

        fs_dict = self.data['volume_groups'][volume_group.id]['file_systems']

        return [FileSystemData(fs_item_id, fs_data)
                for fs_item_id, fs_data in fs_dict.iteritems()]

    def _get_volume_group_info_rpc(self, cb_api, cluster):
        #
        # Send an RPC call to gather the vxvm disk groups for a given set of
        # nodes. String data is returned from the call, so it needs to be
        # loaded using the json module.
        #
        preamble = "_get_volume_group_info_rpc()"

        log.trace.debug("%s: to get nodes that DGs are imported on" % preamble)

        try:
            output, errors = RpcCommandOutputProcessor().\
                execute_rpc_and_process_result(
                        cb_api,
                        cluster.get_hostnames(),
                        SNAPSHOT_AGENT,
                        'check_active_nodes',
                        timeout=60,
                        retries=2
                    )

        except RpcExecutionException as e:

            log.trace.debug("plugin error occurred ...")
            raise CallbackExecutionException(e)

        else:

            log.trace.debug("gathered data from nodes ...")

            # process RPC output, expected in the following format
            # output = { host1: {dg_name: [snaps]}, host2: {dg_name: [...]} }
            # errors = { host3: ['error message goes here']}

            data = {}

            for hostname, raw_data in output.iteritems():
                # unreachable nodes will return and empty string
                if raw_data:
                    data[hostname] = json.loads(raw_data)
                    self._set_node_reachable(cluster, hostname)

            # these are nodes that did not respond
            for hostname, error_list in errors.iteritems():
                self._set_node_unreachable(cluster, hostname)
                self._add_node_errors(cluster, hostname, error_list)

            return data

    def _get_volume_group_map(self, cb_api, cluster):
        #
        # Post process the RPC call data and map the volume groups names to the
        # hostnames so they can be easily matched in the data manager.
        #
        preamble = "_get_volume_group_map()"

        log.trace.debug(
            "%s: create a map of volume groups to nodes" % preamble)

        query_data = self._get_volume_group_info_rpc(cb_api, cluster)

        volume_group_map = {}

        for hostname, imported_volume_groups in query_data.iteritems():

            if imported_volume_groups:

                # gather all volume group names
                for vg_name in imported_volume_groups:

                    if vg_name not in volume_group_map:
                        volume_group_map[vg_name] = hostname

                    else:
                        # vg imported twice, this should never happen
                        # but in the event that it does, raise an error
                        raise CallbackExecutionException(
                            "DG %s is imported in more than one node" % vg_name
                        )

        return volume_group_map

    def _set_active_nodes(self, cluster, volume_group_map):

        # iterate the volume groups for a given cluster
        for vg in self.get_volume_groups(cluster):

            # check the vg name is in the volume group map
            if vg.name in volume_group_map:

                # if it is then set the active node field
                active_node = volume_group_map[vg.name]
                self.set_active_node(vg, active_node)
            else:

                # otherwise set the active node to None
                self.set_active_node(vg, None)

    def get_not_imported_volume_groups(self, cluster):
        return [vg for vg in self.get_volume_groups(cluster)
                if vg.active_node is None]

    def _import_volume_groups(self, cb_api, hostname, not_imported_vgs):

        for vg in not_imported_vgs:

            log.trace.info("Disk group {0} is not imported. Will try to "
                           "import it now in {1}.".format(vg.name, hostname))

            self.driver.import_disk_group_t_flag(cb_api, vg.name, [hostname])

    def gather_active_nodes(self, cb_api, import_dgs=True,
                                          stop_on_unreachable_nodes=False):

        unreachable_nodes = []
        for cluster in self.get_clusters():

            volume_group_map = self._get_volume_group_map(cb_api, cluster)

            if self._has_reachable_nodes(cluster):

                self._set_active_nodes(cluster, volume_group_map)

                unreachable_nodes = self._get_unreachable_nodes(cluster)
                if stop_on_unreachable_nodes and unreachable_nodes:
                    continue

                # query data manager to make sure all of the DGs are imported
                not_imported_vgs = self.get_not_imported_volume_groups(cluster)

                # if import dgs is true and vgs not imported
                if import_dgs and not_imported_vgs:

                    # Try to import not imported vgs on node
                    hostname = self._get_first_reachable_node(cluster)

                    self._import_volume_groups(
                        cb_api, hostname, not_imported_vgs)

                    # check again to see if they have been imported
                    volume_group_map = self._get_volume_group_map(
                        cb_api, cluster)
                    self._set_active_nodes(cluster, volume_group_map)

            elif self.get_action_forced():
                # cluster is unreachable and action is forced
                self.driver.log_unreachable_cluster_message(
                    log.trace.warn, cluster)

    def gather_snapshots_are_present(self, cb_api, check_for_cache=False):

        for vg_path, vg_data in self.data['volume_groups'].iteritems():

            if not self._is_in_the_filter('vg_paths', vg_path):
                continue

            if self.get_action_forced() == True and \
                not vg_data.get('active_node'):
                log.trace.warn("No active node - vg %s cluster %s" % \
                           (vg_data['vg_name'], vg_data['cluster_id']))
                return

            if vg_data.get('active_node'):

                active_node = vg_data['active_node']

                for fs_id, fs_data in vg_data['file_systems'].iteritems():

                    if not self._is_in_the_filter('fs_ids', fs_id):
                        continue

                    snapshot_name = fs_data['snapshot_name']

                    try:
                        _, errors = RpcCommandOutputProcessor().\
                            execute_rpc_and_process_result(
                                cb_api,
                                [active_node],
                                VXVM_AGENT,
                                'is_snapshot_present',
                                {
                                  'disk_group': vg_data['vg_name'],
                                  'snapshot_name': snapshot_name,
                                  'check_for_cache': str(check_for_cache)
                                },
                                timeout=None
                            )

                    except RpcExecutionException as e:
                        raise CallbackExecutionException(e)

                    else:
                        if not errors:
                            fs_data['present'] = snapshot_name
                            # There can only be one snapshot ...
                            break
                        else:
                            fs_data['errors_list'].extend(
                                      errors[active_node])

    def gather_snapshots_are_valid(self, cb_api):

        for vg_vpath, vg_data in self.data['volume_groups'].iteritems():

            if not self._is_in_the_filter('vg_paths', vg_vpath):
                continue

            if self.get_action_forced() == True and \
                not vg_data.get('active_node'):
                log.trace.warn("Warning: No active node - vg %s cluster %s" % \
                           (vg_data['vg_name'], vg_data['cluster_id']))
                return

            if vg_data.get('active_node'):

                active_node = vg_data['active_node']

                for fs_id, fs_data in vg_data['file_systems'].iteritems():

                    if not self._is_in_the_filter('fs_ids', fs_id):
                        continue

                    # If the snapshot is not present,
                    # do not check for validity.
                    if not fs_data['present']:
                        continue

                    try:
                        _, errors = RpcCommandProcessorBase().\
                            execute_rpc_and_process_result(
                                cb_api,
                                [active_node],
                                SNAPSHOT_AGENT,
                                'check_snapshot',
                                {
                                 'disk_group': vg_data['vg_name'],
                                 'snapshot_name': fs_data['present']
                                },
                                timeout=None
                            )
                    except RpcExecutionException as e:
                        raise CallbackExecutionException(e)

                    else:
                        if errors:
                            fs_data['valid'] = False
                            fs_data['errors_list'].extend(errors[active_node])
                        else:
                            fs_data['valid'] = True

    def gather_restores_in_progress(self, cb_api):

        for _, vg_data in self.data['volume_groups'].iteritems():

            if not vg_data.get('active_node') and \
                self.get_action_forced() == True:
                return

            if vg_data.get('active_node'):

                active_node = vg_data['active_node']

                for _, fs_data in vg_data['file_systems'].iteritems():

                    if fs_data['present']:

                        try:
                            _, errors = RpcCommandOutputProcessor().\
                                execute_rpc_and_process_result(
                                    cb_api,
                                    [active_node],
                                    SNAPSHOT_AGENT,
                                    'check_restore_completed',
                                    {'disk_group': vg_data['vg_name'],
                                     'volume_name': fs_data['present']},
                                    timeout=60,
                                    retries=2
                                )
                        except RpcExecutionException as e:

                            raise CallbackExecutionException(e)

                        else:
                            if errors:
                                fs_data['restore_in_progress'] = True
                                fs_data['errors_list'].extend(
                                    errors[active_node])
                            else:
                                fs_data['restore_in_progress'] = False
