litp create -p /software/profiles/rhel_6_4 -t os-profile -o name=sample-profile version=rhel6 path=/var/www/html/6.4/os/x86_64/ arch=x86_64 breed=redhat
litp create -p /infrastructure/systems/system_ms -t system -o system_name=MS1VM
litp create -p /infrastructure/systems/system_1 -t system -o system_name=MN1VM
litp create -p /infrastructure/systems/system_1/disks/disk_0 -t disk -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBe2bee164-af9c23e3
#litp create -p /infrastructure/systems/system_1/disks/lun_0 -t disk -o name=lun_0 size=40G bootable=false uuid=30000000fc85c928
litp create -p /infrastructure/systems/system_2 -t system -o system_name=MN2VM
litp create -p /infrastructure/systems/system_2/disks/disk_0 -t disk -o name=hd0 size=28G bootable=true uuid=ATA_VBOX_HARDDISK_VBf9ea7964-e6d13a01
#litp create -p /infrastructure/systems/system_2/disks/lun_0 -t disk -o name=lun_0 size=40G bootable=false uuid=30000000fc85c928
litp create -p /infrastructure/networking/networks/mgmt -t network -o name=mgmt subnet=10.10.10.0/24 litp_management=true
litp create -p /infrastructure/networking/routes/default -t route -o  subnet=0.0.0.0/0 gateway=10.10.10.1
litp create -t network -p /infrastructure/networking/networks/traffic1 -o name=traffic1 subnet=192.168.100.0/24
litp create -t network -p /infrastructure/networking/networks/traffic2 -o name=traffic2 subnet=192.168.200.128/25
litp create -t network -p /infrastructure/networking/networks/traffic3 -o name=traffic3 subnet=192.168.101.0/24
litp create -t network -p /infrastructure/networking/networks/traffic4 -o name=traffic4 subnet=192.168.201.128/25
litp create -t network -p /infrastructure/networking/networks/heartbeat2 -o name=heartbeat2
litp create -t network -p /infrastructure/networking/networks/heartbeat1 -o name=heartbeat1
litp create -p /infrastructure/storage/storage_profiles/profile_1 -t storage-profile
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1 -t volume-group -o volume_group_name=vg_root
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/file_systems/root -t file-system -o type=ext4 mount_point=/ size=4G
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/file_systems/swap -t file-system -o type=swap mount_point=swap size=2G
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/file_systems/home -t file-system -o type=ext4 mount_point=/home size=8G
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/vg_1/physical_devices/disk_0 -t physical-device -o device_name=hd0

litp create -p /software/items/luci -t package -o name=luci version=0.26.0 release=37.el6
litp create -p /software/items/httpd -t package -o name=httpd version=2.2.15 release=29.el6_4
litp create -p /software/items/cups -t package -o name=cups version=1.4.2 release=50.el6_4.5

litp inherit -p /ms/system -s /infrastructure/systems/system_ms
litp create -p /ms/network_interfaces/nic_0 -t eth -o device_name=eth0 macaddress=08:00:27:E2:EC:4E master=bondmgt
litp create -p /ms/network_interfaces/nic_2 -t eth -o device_name=eth2 macaddress=08:00:27:7B:26:04 master=bondmgt
litp create -t bond -p /ms/network_interfaces/b0 -o device_name=bondmgt ipaddress=10.10.10.100 network_name=mgmt mode=1 miimon=100

litp inherit -p /ms/routes/default -s /infrastructure/networking/routes/default
litp create -p /deployments/d1 -t deployment
litp create -p /deployments/d1/clusters/c1 -t vcs-cluster -o cluster_type=sfha cluster_id=1 low_prio_net=mgmt llt_nets=heartbeat1,heartbeat2
litp create -p /deployments/d1/clusters/c1/nodes/node1 -t node -o hostname=node1
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/system -s /infrastructure/systems/system_1
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/os -s /software/profiles/rhel_6_4

litp inherit -p /deployments/d1/clusters/c1/nodes/node1/items/luci -s /software/items/luci
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/items/httpd -s /software/items/httpd
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/items/cups -s /software/items/cups

litp create -p /deployments/d1/clusters/c1/nodes/node1/network_interfaces/nic0 -t eth -o device_name=eth0 macaddress=08:00:27:F7:B0:F9 network_name=mgmt ipaddress=10.10.10.101
litp create -p /deployments/d1/clusters/c1/nodes/node1/network_interfaces/nic2 -t eth -o device_name=eth1 macaddress=08:00:27:BF:20:60 network_name=heartbeat1
litp create -p /deployments/d1/clusters/c1/nodes/node1/network_interfaces/nic3 -t eth -o device_name=eth2 macaddress=08:00:27:76:39:57 network_name=heartbeat2

litp inherit -p /deployments/d1/clusters/c1/nodes/node1/storage_profile -s /infrastructure/storage/storage_profiles/profile_1
litp inherit -p /deployments/d1/clusters/c1/nodes/node1/routes/default -s /infrastructure/networking/routes/default
litp create -p /deployments/d1/clusters/c1/nodes/node2 -t node -o hostname=node2
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/system -s /infrastructure/systems/system_2
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/os -s /software/profiles/rhel_6_4

litp inherit -p /deployments/d1/clusters/c1/nodes/node2/items/luci -s /software/items/luci
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/items/httpd -s /software/items/httpd
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/items/cups -s /software/items/cups

litp create -p /deployments/d1/clusters/c1/nodes/node2/network_interfaces/nic0 -t eth -o device_name=eth0 macaddress=08:00:27:8E:62:35 network_name=mgmt ipaddress=10.10.10.102
litp create -p /deployments/d1/clusters/c1/nodes/node2/network_interfaces/nic2 -t eth -o device_name=eth1 macaddress=08:00:27:82:C5:2B network_name=heartbeat1
litp create -p /deployments/d1/clusters/c1/nodes/node2/network_interfaces/nic3 -t eth -o device_name=eth2 macaddress=08:00:27:BF:DF:DC network_name=heartbeat2
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/storage_profile -s /infrastructure/storage/storage_profiles/profile_1
litp inherit -p /deployments/d1/clusters/c1/nodes/node2/routes/default -s /infrastructure/networking/routes/default

