##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

from litp.core.constants import UPGRADE_SNAPSHOT_NAME, BASE_RPC_NO_ANSWER
from litp.core.litp_logging import LitpLogger
from volmgr_plugin.volmgr_utils import VolMgrUtils

log = LitpLogger()


class DriverBase(object):

    def __init__(self, callbacks):
        self.rpc_callbacks = callbacks
        self.unreachable_nodes = []
        self.reachable_nodes = []
        self.cli_force = False

    def get_snapshot_tag(self, context):
        if context.snapshot_name() != UPGRADE_SNAPSHOT_NAME:
            return context.snapshot_name()
        return ''

    def _get_snapshot_type(self, tag):
        if tag == '':
            return 'deployment'
        else:
            return 'named backup'

    def filter_unreachable_nodes(self, out, errors):
        # {node_hostname: err_list}
        log.trace.debug("filter_unreachable_nodes")
        errors_unreachable = [n for (n, l) in errors.iteritems() if
                                       any(BASE_RPC_NO_ANSWER in e for e in l)]
        for node in iter(errors_unreachable):
            if node not in self.unreachable_nodes:
                self.unreachable_nodes.append(node)
            # remove it from the error list
            errors[node] = [e for e in errors[node] if
                                                not BASE_RPC_NO_ANSWER in e]
            if out.get(node, '') is None:
                out[node] = ''
        # filter empty lists, otherwise we could have a dictionary of empty
        # lists and 'if errors:' would return true when it shouldn't
        errors = dict((k, v) for (k, v) in errors.iteritems() if v)
        log.trace.debug("print out %s" % out)
        return out, errors

    def _local_storage_backed_vgs(self, node, snap_name=UPGRADE_SNAPSHOT_NAME):
        return [vg for vg in node.storage_profile.volume_groups
                if self._vg_snap_operation_allowed_in_san_aspect(node, vg,
                                                                 snap_name)]

    def _vg_snap_operation_allowed_in_san_aspect(self, node, vg,
                                                 name=UPGRADE_SNAPSHOT_NAME):
        result = True

        if name == UPGRADE_SNAPSHOT_NAME:

            the_system = None

            if node and node.system:
                the_system = node.system
            else:
                the_system = VolMgrUtils.vg_system(vg)

            if the_system:
                base_disk_count = 0
                disk_count = 0
                for pd in vg.physical_devices:
                    if the_system.query('disk-base', name=pd.device_name):
                        base_disk_count += 1
                    if the_system.query('disk', name=pd.device_name):
                        disk_count += 1

                result = base_disk_count == disk_count

        log.trace.debug("VG: %s. Remote-storage backed: %s" %
                        (vg.item_id, result))

        return result
