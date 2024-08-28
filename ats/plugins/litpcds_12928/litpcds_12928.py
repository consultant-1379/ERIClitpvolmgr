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

    if action == 'vg2fact':
        vg2fact_result = "disk_ata_vbox_harddisk_vbe2bee164_ffffffff_part3_dev"
        ms_hostname = nodes[0]

        result[ms_hostname] = {
            'data': {
                'out': vg2fact_result,
                'status': 0,
                'err': ''
            },
            'errors': ''
        }

        return result

    return original_rpc_command(
        self, nodes, agent, action, action_kwargs, timeout, retries
    )


original_rpc_command = PluginApiContext.rpc_command
PluginApiContext.rpc_command = mock_rpc_command


class LITPCDS12928(Plugin):

    def create_configuration(self, plugin_api_context):
        return []

    def create_snapshot_plan(self, plugin_api_context):
        return []
