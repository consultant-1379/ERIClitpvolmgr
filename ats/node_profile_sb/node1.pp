
class task_node1__lvm_3a_3avolume__root__vg__root(){
    lvm::volume { "root_vg_root":
        ensure => "present",
        fstype => "xfs",
pv => [
        "$::disk_ata_qemu_harddisk_qm00001_part3_dev"
        ]
,
        size => "100G",
        vg => "root_vg"
    }
}


node "node1" {

    class {'litp::mn_node':
        ms_hostname => "ms1",
        cluster_type => "NON-CMW"
        }


    class {'task_node1__lvm_3a_3avolume__root__vg__root':
    }


}