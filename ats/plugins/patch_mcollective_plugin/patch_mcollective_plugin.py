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
from volmgr_plugin.volmgr_utils import VolMgrUtils

disk_fact_name = "disk_ata_vbox_harddisk_vb24150799_28e77304_part2_dev"

def mock_rpc_command(self, nodes, agent, action, action_kwargs, timeout=None, retries=0):
    result = {}

    if action == 'lvs':
        for node in nodes:
            if node == 'ms1':
                result[node] = {'data': {'out': "  /dev/vg_root/L_lv_home_              5.62g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_root_             24.10g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_var_              25.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_var_tmp_          21.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_var_lib_puppetdb_ 11.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_var_opt_rh_       99.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_var_log_          23.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_var_www_          27.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/L_lv_software_         25.25g swi-a-s--- [NOT-MOUNTED]\n" + \
                                                "  /dev/vg_root/lv_home                 5.62g owi-aos--- /home\n" + \
                                                "  /dev/vg_root/lv_root                24.10g owi-aos--- /\n" + \
                                                "  /dev/vg_root/lv_swap                 5.62g -wi-ao---- [SWAP]\n" + \
                                                "  /dev/vg_root/lv_var_tmp             21.25g owi-aos--- /var/tmp\n" + \
                                                "  /dev/vg_root/lv_var_lib_puppetdb    11.25g owi-aos--- /var/lib/puppetdb\n" + \
                                                "  /dev/vg_root/lv_var_opt_rh          99.25g owi-aos--- /var/opt/rh\n" + \
                                                "  /dev/vg_root/lv_var_log             26.25g owi-aos--- /var/log\n" + \
                                                "  /dev/vg_root/lv_var_www             22.25g owi-aos--- /var/www\n" + \
                                                "  /dev/vg_root/lv_software            28.25g owi-aos--- /software\n" + \
                                                "  /dev/vg_root/lv_var                 25.25g owi-aos--- /var",
                                         'status': 0,
                                         'err': ''},
                                'errors': ''}
            else:
                result[node] = {'data': {'out': "/dev/vg_root/L_vg_1_root_backup_ 24.10g swi-a-s--- [NOT-MOUNTED]",
                                         'status': 0,
                                         'err': ''},
                                'errors': ''}
            return result
    elif action == 'lsblk':
        for node in nodes:
            result[node] = {'data': {'out': "FSTYPE=ext4",
                                     'status': 0,
                                     'err': ''},
                            'errors': ''}
            return result

    elif action == 'vg2fact':
        for node in nodes:
            if node == 'ms1':
                result[node] = {'data': {'out': disk_fact_name,
                                         'status': 0,
                                         'err': ''},
                                'errors': ''}
        return result

    return original_rpc_command(self, nodes, agent, action, action_kwargs, timeout, retries)

original_rpc_command = PluginApiContext.rpc_command
PluginApiContext.rpc_command = mock_rpc_command

class PatchMcollectivePlugin(Plugin):
    def create_configuration(self, plugin_api_context):
        global disk_fact_name

        # set fact name to '_part3' if the MS system disk size >= 2TB
        ms = plugin_api_context.query('ms')[0]
        if ms.system and ms.system.disks:
            ms_system_disks = [disk for disk in ms.system.disks]
            if ms_system_disks:
                ms_system_disk = ms_system_disks[0]
                if VolMgrUtils().get_size_megabytes(ms_system_disk.size) >= 2097152:
                    disk_fact_name = "disk_ata_vbox_harddisk_vb24150799_28e77304_part3_dev"

        return []

    def create_snapshot_plan(self, plugin_api_context):
        return []
