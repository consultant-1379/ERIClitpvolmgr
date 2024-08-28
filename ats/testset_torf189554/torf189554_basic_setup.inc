litp create -t os-profile -p /software/profiles/rhel_6_4 -o name=sample-profile version=rhel6 path=/var/www/html/6.4/os/x86_64/ arch=x86_64 breed=redhat

litp create -t system -p /infrastructure/systems/sys_ms -o system_name=MS1VM

litp create -t system -p /infrastructure/systems/s1 -o system_name=MN1VM
litp create -t disk -p /infrastructure/systems/s1/disks/d1 -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBe2bee164-af9c23e1
litp create -t disk -p /infrastructure/systems/s1/disks/d2 -o name=lun1 size=50G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa1
litp create -t disk -p /infrastructure/systems/s1/disks/d3 -o name=lun2 size=90G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa2

litp create -t system -p /infrastructure/systems/s2 -o system_name=MN2VM
litp create -t disk -p /infrastructure/systems/s2/disks/d1 -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBf9ea7964-e6d13a02
litp create -t disk -p /infrastructure/systems/s2/disks/d2 -o name=lun1 size=50G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa1
litp create -t disk -p /infrastructure/systems/s2/disks/d3 -o name=lun2 size=90G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa2

litp create -t system -p /infrastructure/systems/s3 -o system_name=MN3VM
litp create -t disk -p /infrastructure/systems/s3/disks/d1 -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBf9ea7964-e6d13a03
litp create -t disk -p /infrastructure/systems/s3/disks/d2 -o name=lun3 size=80G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa3
litp create -t disk -p /infrastructure/systems/s3/disks/d3 -o name=lun4 size=70G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa4
litp create -t disk -p /infrastructure/systems/s3/disks/d4 -o name=lun5 size=30G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa5

litp create -t system -p /infrastructure/systems/s4 -o system_name=MN4VM
litp create -t disk -p /infrastructure/systems/s4/disks/d1 -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBf9ea7964-e6d13a04
litp create -t disk -p /infrastructure/systems/s4/disks/d2 -o name=lun3 size=80G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa3
litp create -t disk -p /infrastructure/systems/s4/disks/d3 -o name=lun4 size=70G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa4
litp create -t disk -p /infrastructure/systems/s4/disks/d4 -o name=lun5 size=30G bootable=false uuid=ATA_VBOX_HARDDISK_VBe2bee164-aaaaaaa5

litp create -t network -p /infrastructure/networking/networks/n1 -o name=mgmt subnet=10.10.10.0/24 litp_management=true

litp create -t storage-profile -p /infrastructure/storage/storage_profiles/sp1 -o volume_driver=lvm
litp create -t volume-group -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1 -o volume_group_name=vg_root
litp create -t file-system -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/file_systems/fs1 -o type=ext4 mount_point=/ size=4G snap_size=100 backup_snap_size=100
litp create -t file-system -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/file_systems/fs2 -o type=swap mount_point=swap size=2G
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/physical_devices/pd1 -o device_name=hd0

litp inherit -p /ms/system -s /infrastructure/systems/sys_ms

litp create -t deployment -p /deployments/d1

litp create -t vcs-cluster -p /deployments/d1/clusters/c1 -o cluster_type=sfha cluster_id=1 low_prio_net=mgmt llt_nets=heartbeat1,heartbeat2
litp create -t vcs-cluster -p /deployments/d1/clusters/c2 -o cluster_type=sfha cluster_id=2 low_prio_net=mgmt llt_nets=heartbeat1,heartbeat2

litp create  -t node -p /deployments/d1/clusters/c1/nodes/n1 -o hostname=mn1
litp inherit -p /deployments/d1/clusters/c1/nodes/n1/system -s /infrastructure/systems/s1
litp inherit -p /deployments/d1/clusters/c1/nodes/n1/os -s /software/profiles/rhel_6_4
litp create -t eth -p /deployments/d1/clusters/c1/nodes/n1/network_interfaces/n1 -o device_name=eth1 macaddress=08:00:27:F7:B0:F9 network_name=mgmt ipaddress=10.10.10.101
litp inherit -p /deployments/d1/clusters/c1/nodes/n1/storage_profile -s /infrastructure/storage/storage_profiles/sp1

litp create -t node -p /deployments/d1/clusters/c1/nodes/n2 -o hostname=mn2
litp inherit -p /deployments/d1/clusters/c1/nodes/n2/system -s /infrastructure/systems/s2
litp inherit -p /deployments/d1/clusters/c1/nodes/n2/os -s /software/profiles/rhel_6_4
litp create -t eth -p /deployments/d1/clusters/c1/nodes/n2/network_interfaces/n1 -o device_name=eth1 macaddress=08:00:27:8E:62:35 network_name=mgmt ipaddress=10.10.10.102
litp inherit -p /deployments/d1/clusters/c1/nodes/n2/storage_profile -s /infrastructure/storage/storage_profiles/sp1

litp create -t node -p /deployments/d1/clusters/c2/nodes/n3 -o hostname=mn3
litp inherit -p /deployments/d1/clusters/c2/nodes/n3/system -s /infrastructure/systems/s3
litp inherit -p /deployments/d1/clusters/c2/nodes/n3/os -s /software/profiles/rhel_6_4
litp create -t eth -p /deployments/d1/clusters/c2/nodes/n3/network_interfaces/n1 -o device_name=eth1 macaddress=08:00:27:8E:62:36 network_name=mgmt ipaddress=10.10.10.103
litp inherit -p /deployments/d1/clusters/c2/nodes/n3/storage_profile -s /infrastructure/storage/storage_profiles/sp1

litp create -t node -p /deployments/d1/clusters/c2/nodes/n4 -o hostname=mn4
litp inherit -p /deployments/d1/clusters/c2/nodes/n4/system -s /infrastructure/systems/s4
litp inherit -p /deployments/d1/clusters/c2/nodes/n4/os -s /software/profiles/rhel_6_4
litp create -t eth -p /deployments/d1/clusters/c2/nodes/n4/network_interfaces/n1 -o device_name=eth1 macaddress=08:00:27:8E:62:37 network_name=mgmt ipaddress=10.10.10.104
litp inherit -p /deployments/d1/clusters/c2/nodes/n4/storage_profile -s /infrastructure/storage/storage_profiles/sp1

litp create -t storage-profile -p /infrastructure/storage/storage_profiles/sp2 -o volume_driver=vxvm
litp create -t volume-group    -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg1 -o volume_group_name=foo
litp create -t file-system     -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg1/file_systems/fs1 -o type=vxfs mount_point=/foo size=4G snap_size=100 backup_snap_size=100
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg1/physical_devices/pd1 -o device_name=lun1
litp create -t volume-group    -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg2 -o volume_group_name=bar
litp create -t file-system     -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg2/file_systems/fs1 -o type=vxfs mount_point=/bar size=2G snap_size=100 backup_snap_size=100
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg2/physical_devices/pd1 -o device_name=lun2

litp inherit -p /deployments/d1/clusters/c1/storage_profile/foo_bar -s /infrastructure/storage/storage_profiles/sp2

litp create -t storage-profile -p /infrastructure/storage/storage_profiles/sp3 -o volume_driver=vxvm
litp create -t volume-group    -p /infrastructure/storage/storage_profiles/sp3/volume_groups/vg1 -o volume_group_name=bar
litp create -t file-system     -p /infrastructure/storage/storage_profiles/sp3/volume_groups/vg1/file_systems/fs1 -o type=vxfs mount_point=/bar size=30G snap_size=100 backup_snap_size=100
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp3/volume_groups/vg1/physical_devices/pd1 -o device_name=lun3
litp create -t volume-group    -p /infrastructure/storage/storage_profiles/sp3/volume_groups/vg2 -o volume_group_name=baz
litp create -t file-system     -p /infrastructure/storage/storage_profiles/sp3/volume_groups/vg2/file_systems/fs1 -o type=vxfs mount_point=/baz size=20G snap_size=100 backup_snap_size=100
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp3/volume_groups/vg2/physical_devices/pd1 -o device_name=lun4

litp inherit -p /deployments/d1/clusters/c2/storage_profile/bar_baz -s /infrastructure/storage/storage_profiles/sp3

litp create -t storage-profile -p /infrastructure/storage/storage_profiles/sp4 -o volume_driver=vxvm
litp create -t volume-group    -p /infrastructure/storage/storage_profiles/sp4/volume_groups/vg1 -o volume_group_name=naz
litp create -t file-system     -p /infrastructure/storage/storage_profiles/sp4/volume_groups/vg1/file_systems/fs1 -o type=vxfs mount_point=/naz size=10G snap_size=100 backup_snap_size=100
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp4/volume_groups/vg1/physical_devices/pd1 -o device_name=lun5

litp inherit -p /deployments/d1/clusters/c2/storage_profile/naz -s /infrastructure/storage/storage_profiles/sp4
