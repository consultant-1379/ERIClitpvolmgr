
class task_mn2__file___2ftrial(){
    file { "/trial":
        backup => "false",
        ensure => "directory",
        group => "0",
        mode => "0755",
        owner => "0",
        path => "/trial"
    }
}

class task_mn2__lvm_3a_3avolume__vg1__root(){
    lvm::volume { "vg1_root":
        ensure => "present",
        fstype => "ext4",
pv => [
        "$::disk_ata_vbox_harddisk_vb7b1206c5_0f325654_part3_dev"
        ]
,
        size => "8G",
        vg => "vg_root"
    }
}

class task_mn2__lvm_3a_3avolume__vg1__trial(){
    lvm::volume { "vg1_trial":
        ensure => "present",
        fstype => "ext4",
pv => [
        "$::disk_ata_vbox_harddisk_vb7b1206c5_0f325654_part3_dev"
        ]
,
        size => "7G",
        vg => "vg_root"
    }
}

class task_mn2__mount___2ftrial(){
    mount { "/trial":
        atboot => "true",
        device => "/dev/vg_root/vg1_trial",
        ensure => "mounted",
        fstype => "ext4",
        options => "defaults",
        require => [Lvm::Volume["vg1_trial"]]
    }
}


node "mn2" {

    class {'litp::mn_node':
        ms_hostname => "ms1",
        cluster_type => "NON-CMW"
        }


    class {'task_mn2__file___2ftrial':
        require => [Class["task_mn2__lvm_3a_3avolume__vg1__trial"]]
    }


    class {'task_mn2__lvm_3a_3avolume__vg1__root':
    }


    class {'task_mn2__lvm_3a_3avolume__vg1__trial':
    }


    class {'task_mn2__mount___2ftrial':
        require => [Class["task_mn2__file___2ftrial"],Class["task_mn2__lvm_3a_3avolume__vg1__trial"]]
    }


}
