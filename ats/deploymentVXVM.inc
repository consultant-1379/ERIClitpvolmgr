runLitpScript ./deploymentLV.inc

litp create -p /infrastructure/storage/storage_profiles/sp -t storage-profile -o volume_driver=vxvm
litp create -p /infrastructure/storage/storage_profiles/sp/volume_groups/vxvm_vol_grp -t volume-group -o volume_group_name='vxvm_vol_grp'
litp create -p /infrastructure/storage/storage_profiles/sp/volume_groups/vxvm_vol_grp/file_systems/vxfs1 -t file-system -o type='vxfs' size='2G' mount_point='/mp__4'
litp create -p /infrastructure/storage/storage_profiles/sp/volume_groups/vxvm_vol_grp/physical_devices/disk_1 -t physical-device -o device_name=lun_0
litp inherit -p /deployments/d1/clusters/c1/storage_profile/sp -s /infrastructure/storage/storage_profiles/sp


