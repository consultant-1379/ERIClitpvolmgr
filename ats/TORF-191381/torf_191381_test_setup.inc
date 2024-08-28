litp create -t os-profile -p /software/profiles/rhel_6_4 -o name=sample-profile version=rhel6 path=/var/www/html/6.4/os/x86_64/ arch=x86_64 breed=redhat

litp create -t system -p /infrastructure/systems/s0 -o system_name=ms

litp create -t system -p /infrastructure/systems/s1 -o system_name=mn1
litp create -t disk -p /infrastructure/systems/s1/disks/d0 -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBe2bee164-af9c23e3
litp create -t torf_191381_disk -p /infrastructure/systems/s1/disks/d1 -o name=hd1 size=40G bootable=false uuid=10000000fc85c921
litp create -t torf_191381_disk -p /infrastructure/systems/s1/disks/d2 -o name=hd2 size=10G bootable=false uuid=20000000fc85c922
litp create -t system -p /infrastructure/systems/s2 -o system_name=mn2
litp create -t disk -p /infrastructure/systems/s2/disks/d0 -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBf9ea7964-e6d13a01
litp create -t torf_191381_disk -p /infrastructure/systems/s2/disks/d1 -o name=hd1 size=40G bootable=false uuid=10000000fc85c921
litp create -t torf_191381_disk -p /infrastructure/systems/s2/disks/d2 -o name=hd2 size=10G bootable=false uuid=20000000fc85c922

litp create -t network -p /infrastructure/networking/networks/n1 -o name=mgmt subnet=10.10.10.0/24 litp_management=true

litp create -t storage-profile -p /infrastructure/storage/storage_profiles/sp1
litp create -t volume-group -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1 -o volume_group_name=vg_root
litp create -t file-system -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/file_systems/fs1 -o type=ext4 mount_point=/ size=4G snap_size=100
litp create -t file-system -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/file_systems/fs2 -o type=swap mount_point=swap size=2G
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp1/volume_groups/vg1/physical_devices/d0 -o device_name=hd0

litp create -t storage-profile -p /infrastructure/storage/storage_profiles/sp2 -o volume_driver=vxvm
litp create -t volume-group -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg1 -o volume_group_name=vg_data
litp create -t file-system -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg1/file_systems/fs1 -o type=vxfs mount_point=/data size=5G
litp create -t physical-device -p /infrastructure/storage/storage_profiles/sp2/volume_groups/vg1/physical_devices/d0 -o device_name=hd1

litp inherit -p /ms/system -s /infrastructure/systems/s0

litp create -t deployment -p /deployments/d1
litp create -t vcs-cluster -p /deployments/d1/clusters/c1 -o cluster_type=sfha cluster_id=1 low_prio_net=mgmt llt_nets=heartbeat1,heartbeat2
litp inherit -p /deployments/d1/clusters/c1/storage_profile/sp1 -s /infrastructure/storage/storage_profiles/sp2

litp create -t node -p /deployments/d1/clusters/c1/nodes/n1 -o hostname=node1
litp inherit -p /deployments/d1/clusters/c1/nodes/n1/system -s /infrastructure/systems/s1
litp inherit -p /deployments/d1/clusters/c1/nodes/n1/os -s /software/profiles/rhel_6_4
litp create -t eth -p /deployments/d1/clusters/c1/nodes/n1/network_interfaces/n0 -o device_name=eth0 macaddress=08:00:27:F7:B0:F9 network_name=mgmt ipaddress=10.10.10.101
litp inherit -p /deployments/d1/clusters/c1/nodes/n1/storage_profile -s /infrastructure/storage/storage_profiles/sp1

litp create -t node -p /deployments/d1/clusters/c1/nodes/n2 -o hostname=node2
litp inherit -p /deployments/d1/clusters/c1/nodes/n2/system -s /infrastructure/systems/s2
litp inherit -p /deployments/d1/clusters/c1/nodes/n2/os -s /software/profiles/rhel_6_4
litp create -t eth -p /deployments/d1/clusters/c1/nodes/n2/network_interfaces/n0 -o device_name=eth0 macaddress=08:00:27:8E:62:35 network_name=mgmt ipaddress=10.10.10.102
litp inherit -p /deployments/d1/clusters/c1/nodes/n2/storage_profile -s /infrastructure/storage/storage_profiles/sp1
