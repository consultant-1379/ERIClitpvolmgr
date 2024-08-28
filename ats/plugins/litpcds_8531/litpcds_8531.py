##############################################################################
# COPYRIGHT Ericsson AB 2013
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

from litp.core.plugin import Plugin
from litp.core.plugin_context_api import PluginApiContext


def mock_rpc_command(self, nodes, agent, action, action_kwargs, timeout=None, retries=0):
    result = {}
    if action == 'lvs':
        for node in nodes:
            if node == 'ms1':
                result[node] = {
                    'data': {
                        'out': "  /dev/vg_root/lv_home  5.62g -wi-ao---- /home\n"
                               "  /dev/vg_root/lv_root 24.10g -wi-ao---- /\n"
                               "  /dev/vg_root/lv_swap  5.62g -wi-ao---- [SWAP]\n"
                               "  /dev/vg_root/lv_var  25.25g -wi-ao---- /var\n"
                               "  /dev/vg_root/L_lv_var_ 4.00g swi-a-s--- [NOT-MOUNTED]",
                        'status': 0,
                        'err': ''
                    },
                    'errors': ''
                }
            else:
                result[node] = {
                    'data': {
                        'out': "  /dev/vg_root/L_lv_home_ 8.00g swi-a-s--- [NOT-MOUNTED]\n"
                               "  /dev/vg_root/L_lv_root_ 4.00g swi-a-s--- [NOT-MOUNTED]\n"
                               "  /dev/vg_root/vg_1_home               8.00g owi-aos--- /home\n"
                               "  /dev/vg_root/vg_1_root               4.00g owi-aos--- /\n"
                               "  /dev/vg_root/vg_1_swap               2.00g -wi-ao---- [SWAP]",
                        'status': 0,
                        'err': ''
                    },
                    'errors': ''
                }
        return result
    elif action == 'lsblk':
        for node in nodes:
            if 'swap' in action_kwargs['path']:
                result[node] = {
                    'data': {
                        'out': "FSTYPE=swap",
                        'status': 0,
                        'err': ''
                    },
                    'errors': ''
                }
            else:
                result[node] = {
                    'data': {
                        'out': "FSTYPE=ext4",
                        'status': 0,
                        'err': ''
                    },
                    'errors': ''
                }
        return result

    return original_rpc_command(self, nodes, agent, action, action_kwargs, timeout, retries)

original_rpc_command = PluginApiContext.rpc_command
PluginApiContext.rpc_command = mock_rpc_command


class Litpcds_8531(Plugin):
    def create_configuration(self, plugin_api_context):
        return []

    def create_snapshot_plan(self, plugin_api_context):
        return []
