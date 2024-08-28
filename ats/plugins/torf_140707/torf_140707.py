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


class Torf_140707(Plugin):
    def create_configuration(self, plugin_api_context):
        tasks = []
        for cs in plugin_api_context.query("vcs-clustered-service"):
            if (cs.is_initial() or cs.is_updated() or (cs.is_applied and
                    cs.applied_properties_determinable == False)):
                tasks.append(CallbackTask(cs,
                    "Mock operation on clustered_service {0}".format(cs.name),
                    self.cb_dummy_task,
                    service_name=cs.name))
        return tasks

    def cb_dummy_task(self, service_name):
        """Dummy Task"""
        pass
