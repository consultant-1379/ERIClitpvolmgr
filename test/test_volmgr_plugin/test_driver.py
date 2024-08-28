##############################################################################
# COPYRIGHT Ericsson AB 2014
#
# The copyright to the computer program(s) herein is the property of
# Ericsson AB. The programs may be used and/or copied only with written
# permission from Ericsson AB. or in accordance with the terms and
# conditions stipulated in the agreement/contract under which the
# program(s) have been supplied.
##############################################################################

from volmgr_plugin.drivers.driver import DriverBase
from litp.core.constants import BASE_RPC_NO_ANSWER
import unittest


out_nonempty = {'n1': 'something', 'n2': 'morethings'}
out_empty = {'n1': '', 'n2': 'morethings'}
out_none = {'n1': None}
stderr_no_unreachable = {'n1': ['one failure'], 'n2': []}
stderr_unreachable = {'n1': [BASE_RPC_NO_ANSWER + ' n1'], 'n2': [BASE_RPC_NO_ANSWER + ' n2']}
stderr_mixed = {'n1': [BASE_RPC_NO_ANSWER, 'another err'], 'n2': []}


class TestVolMgrPlugin(unittest.TestCase):
    def setUp(self):
        self.driver = DriverBase({})

    def test_filter_unreachable_nodes_no_filter(self):
        self.assertEqual(({'n1': 'something', 'n2': 'morethings'},
                          {'n1': ['one failure']}),
                         self.driver.filter_unreachable_nodes(out_nonempty, stderr_no_unreachable))

    def test_filter_unreachable_filter_non_mixed(self):
        self.assertEqual(({'n1': '', 'n2': 'morethings'}, {}),
                         self.driver.filter_unreachable_nodes(out_empty, stderr_unreachable))

    def test_filter_unreachable_filter_mixed(self):
        self.assertEqual(({'n1': ''}, {'n1': ['another err']}),
                         self.driver.filter_unreachable_nodes(out_none, stderr_mixed))