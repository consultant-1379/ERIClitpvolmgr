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
from litp.core.execution_manager import CallbackTask


class DummyPlugin(Plugin):
    """
    LITP Mock volmgr plugin to provide tasks in ats
    """

    def __init__(self, *args, **kwargs):
        super(DummyPlugin, self).__init__(*args, **kwargs)

    def create_configuration(self, plugin_api_context):
        tasks = []
        for node in sorted(plugin_api_context.query("node")):
            task1 = CallbackTask(node,
                "Callback - update  property",
                self.cb_update_property)
            tasks.append(task1)
        return tasks

    def cb_update_property(self, plugin_api_context):
        node = plugin_api_context.query('node')[0]
        for test_item in node.query("test_item"):
            test_item.version = "Z.Z.Z"
