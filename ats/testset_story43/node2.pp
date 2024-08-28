class task_node2__lvm_3a_3avolume__vg__1__root(){
    lvm::volume { "vg_1_root":
        ensure => "present",
        fstype => "xfs",
pv => [
        "$::disk_ata_vbox_harddisk_vbf9ea7964_e6d13a01_part3_dev"
        ]
,
        size => "4G",
        vg => "vg_root"
    }
}


node "node2" {

    class {'litp::mn_node':
        ms_hostname => "ms1",
        cluster_type => "NON-CMW"
        }


    class {'task_node2__lvm_3a_3avolume__vg__1__root':
    }


}
