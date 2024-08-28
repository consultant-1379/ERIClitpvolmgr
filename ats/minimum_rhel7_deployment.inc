litp create -p /software/profiles/rhel_7_7 -t os-profile -o name=sample-profile version=rhel7 path=/var/www/html/7.7/os/x86_64/ arch=x86_64 breed=redhat
litp create -p /infrastructure/systems/system_ms -t system -o system_name=MS1VM

litp create -p /infrastructure/systems/system_1 -t system -o system_name=MN1VM
litp create -p /infrastructure/systems/system_1/disks/disk_0 -t disk -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBe2bee164-af9c23e3
litp create -p /infrastructure/systems/system_1/disks/lun_0 -t disk -o name=lun_0 size=40G bootable=false uuid=30000000fc85c928

litp create -p /infrastructure/systems/system_2 -t system -o system_name=MN2VM
litp create -p /infrastructure/systems/system_2/disks/disk_0 -t disk -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBf9ea7964-e6d13a01
litp create -p /infrastructure/systems/system_2/disks/lun_0 -t disk -o name=lun_0 size=40G bootable=false uuid=30000000fc85c929

litp create -p /infrastructure/networking/networks/mgmt -t network -o name=mgmt subnet=10.10.10.0/24 litp_management=true

litp create -p /infrastructure/storage/storage_profiles/profile_1 -t storage-profile
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1 -t volume-group -o volume_group_name=vg_root
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/file_systems/root -t file-system -o type=xfs mount_point=/ size=4G snap_size=100
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/file_systems/swap -t file-system -o type=swap mount_point=swap size=2G
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/physical_devices/disk_0 -t physical-device -o device_name=hd0

litp inherit -p /ms/system -s /infrastructure/systems/system_ms

litp create  -p /deployments/d1 -t deployment
litp create  -p /deployments/d1/clusters/c1 -t vcs-cluster -o cluster_type=sfha cluster_id=1 low_prio_net=mgmt llt_nets=heartbeat1,heartbeat2 grub_lv_enable="true"
litp create  -p /deployments/d1/clusters/c1/nodes/node1 -t node -o hostname=node1
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/system -s /infrastructure/systems/system_1
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/os -s /software/profiles/rhel_7_7

litp create -p /deployments/d1/clusters/c1/nodes/node1/network_interfaces/nic0 -t eth -o device_name=eth0 macaddress=08:00:27:F7:B0:F9 network_name=mgmt ipaddress=10.10.10.101

litp inherit -p /deployments/d1/clusters/c1/nodes/node1/storage_profile -s /infrastructure/storage/storage_profiles/profile_1
litp create -p /deployments/d1/clusters/c1/nodes/node2 -t node -o hostname=node2
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/system -s /infrastructure/systems/system_2
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/os -s /software/profiles/rhel_7_7

litp create -p /deployments/d1/clusters/c1/nodes/node2/network_interfaces/nic0 -t eth -o device_name=eth0 macaddress=08:00:27:8E:62:35 network_name=mgmt ipaddress=10.10.10.102
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/storage_profile -s /infrastructure/storage/storage_profiles/profile_1
