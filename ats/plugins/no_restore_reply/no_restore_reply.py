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
from litp.core.callback_api import CallbackApi
from litp.core.constants import BASE_RPC_NO_ANSWER


def mock_rpc_command(self, nodes, agent, action, action_kwargs, timeout=None, retries=0):
    result = {}

    if action == 'restore' or action=='remove':
        for node in nodes:
            if node=="node1":
                result[node] = {
                       'data': {},
                       'errors': "{0} {1}".format(BASE_RPC_NO_ANSWER, node)
                }
                return result

    return original_rpc_command(self, nodes, agent, action, action_kwargs, timeout, retries)


original_rpc_command = CallbackApi.rpc_command
CallbackApi.rpc_command = mock_rpc_command

class NoRestoreReplyPlugin(Plugin):
    def create_configuration(self, plugin_api_context):
        return []

    def create_snapshot_plan(self, plugin_api_context):
        return []
