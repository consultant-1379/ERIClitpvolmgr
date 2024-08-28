
class task_node1__file___2fopt_2fmisc1(){
    file { "/opt/misc1":
        backup => "false",
        ensure => "directory",
        group => "0",
        mode => "0755",
        owner => "0",
        path => "/opt/misc1"
    }
}

class task_node1__file___2fopt_2fmisc2(){
    file { "/opt/misc2":
        backup => "false",
        ensure => "directory",
        group => "0",
        mode => "0755",
        owner => "0",
        path => "/opt/misc2"
    }
}

class task_node1__lvm_3a_3avolume__root__vg__root(){
    lvm::volume { "root_vg_root":
        ensure => "present",
        fstype => "ext4",
        pv => "$::disk_litpsc2disk0000_part3_dev",
        size => "100G",
        vg => "root_vg"
    }
}

class task_node1__lvm_3a_3avolume__vgdisk1__misc1(){
    lvm::volume { "vgdisk1_misc1":
        ensure => "present",
        fstype => "ext4",
        pv => "$::disk_mn1disk2_dev",
        size => "400M",
        vg => "vg_disk1"
    }
}

class task_node1__lvm_3a_3avolume__vgdisk2__misc1(){
    lvm::volume { "vgdisk2_misc1":
        ensure => "present",
        fstype => "ext4",
        pv => "$::disk_mn1disk3_dev",
        size => "400M",
        vg => "vg_disk2"
    }
}

class task_node1__mount___2fopt_2fmisc1(){
    mount { "/opt/misc1":
        atboot => "true",
        device => "/dev/vg_disk1/vgdisk1_misc1",
        ensure => "mounted",
        fstype => "ext4",
        options => "defaults"
    }
}

class task_node1__mount___2fopt_2fmisc2(){
    mount { "/opt/misc2":
        atboot => "true",
        device => "/dev/vg_disk2/vgdisk2_misc1",
        ensure => "mounted",
        fstype => "ext4",
        options => "defaults"
    }
}


node "node1" {

    class {'litp::mn_node':
        ms_hostname => "ms1"
        }


    class {'task_node1__file___2fopt_2fmisc1':
        require => [Class["task_node1__lvm_3a_3avolume__vgdisk1__misc1"]]
    }


    class {'task_node1__file___2fopt_2fmisc2':
        require => [Class["task_node1__lvm_3a_3avolume__vgdisk2__misc1"]]
    }


    class {'task_node1__lvm_3a_3avolume__root__vg__root':
    }


    class {'task_node1__lvm_3a_3avolume__vgdisk1__misc1':
    }


    class {'task_node1__lvm_3a_3avolume__vgdisk2__misc1':
    }


    class {'task_node1__mount___2fopt_2fmisc1':
        require => [Class["task_node1__file___2fopt_2fmisc1"]]
    }


    class {'task_node1__mount___2fopt_2fmisc2':
        require => [Class["task_node1__file___2fopt_2fmisc2"]]
    }


}