runLitpScript minimum_deployment.inc

litp create_plan
litp run_plan


litp create -p /infrastructure/systems/system43 -t system -o system_name='system43'
litp create -p /infrastructure/systems/system43/disks/disk0 -t disk -o name='hd0' size='40G' bootable='true' uuid='ee7cf0a0-1922-401b-a1ae-6ec9261484c0'
litp create -p /infrastructure/systems/system43/disks/disk1 -t disk -o name='hd1' size='40G' bootable='false' uuid='ff7cf0a0-1922-401b-a1ae-6ec9261484c0'
litp create -p /infrastructure/storage/storage_profiles/profile_test43 -t storage-profile 
litp create -p /infrastructure/storage/storage_profiles/profile_test43/volume_groups/vg0 -t volume-group -o volume_group_name='vg_root' 
litp create -p /infrastructure/storage/storage_profiles/profile_test43/volume_groups/vg0/file_systems/root -t file-system -o type='xfs' mount_point='/' size='16G' snap_size='100'
litp create -p /infrastructure/storage/storage_profiles/profile_test43/volume_groups/vg0/file_systems/swap -t file-system -o type='swap' mount_point='swap' size='2G' snap_size='0' 
litp create -p /infrastructure/storage/storage_profiles/profile_test43/volume_groups/vg0/physical_devices/internal -t physical-device -o device_name='hd0' 

litp create -p /deployments/d1/clusters/tmp -t cluster 
litp create -p /deployments/d1/clusters/tmp/nodes/node43 -t node -o hostname='mn99' 
litp inherit -p /deployments/d1/clusters/tmp/nodes/node43/system -s /infrastructure/systems/system43 

litp inherit -p /deployments/d1/clusters/tmp/nodes/node43/os -s /software/profiles/rhel_6_4 
litp inherit -p /deployments/d1/clusters/tmp/nodes/node43/storage_profile -s /infrastructure/storage/storage_profiles/profile_test43 
litp create -p /deployments/d1/clusters/tmp/nodes/node43/network_interfaces/if0 -t eth -o device_name='eth0' macaddress='BB:BB:BB:BB:BB:BB' ipaddress='192.168.0.2' network_name='mgmt'


