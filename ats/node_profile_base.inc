litp create -p /infrastructure/storage/storage_profiles/profile_1 -t storage-profile
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/root_vg -t volume-group -o volume_group_name="root_vg"
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/root_vg/file_systems/root -t file-system -o type="xfs" mount_point="/" size="100G"
litp create -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/root_vg/file_systems/swap -t file-system -o type="swap" mount_point="swap" size="32G"
litp create -t physical-device -p /infrastructure/storage/storage_profiles/profile_1/volume_groups/root_vg/physical_devices/internal -o device_name=hd0

litp create -p /infrastructure/systems/system1 -t system -o system_name="AT_node"
litp create -p /software/profiles/osprofile1 -t os-profile -o name="node-iso" version="rhel6" arch="x86_64" breed="linux" path="/cobbler/ks_mirror/node-iso-x86_64"

litp create -p /deployments/at -t deployment
litp create -p /deployments/at/clusters/cluster1 -t cluster
litp create -p /deployments/at/clusters/cluster1/nodes/node1 -t node -o hostname="node1"

litp inherit -p /deployments/at/clusters/cluster1/nodes/node1/system -s /infrastructure/systems/system1
litp inherit -p /deployments/at/clusters/cluster1/nodes/node1/os -s /software/profiles/osprofile1
litp create -p /deployments/at/clusters/cluster1/nodes/node1/network_interfaces/ip1 -t network-interface -o network_name='bla'
litp inherit -p /deployments/at/clusters/cluster1/nodes/node1/storage_profile -s /infrastructure/storage/storage_profiles/profile_1
