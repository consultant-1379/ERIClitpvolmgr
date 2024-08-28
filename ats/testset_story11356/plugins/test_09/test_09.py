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


class Test_09(Plugin):
    """
    LITP Mock volmgr plugin to provide snapshots tasks in ats
    """

    def __init__(self, *args, **kwargs):
        super(Test_09, self).__init__(*args, **kwargs)

    def create_configuration(self, plugin_api_context):
        tasks = []
        return tasks

    def update_model(self, plugin_api_context):
        node = plugin_api_context.query('node')[0]
        for disk in node.query("disk"):
            disk.disk_part = "true"
