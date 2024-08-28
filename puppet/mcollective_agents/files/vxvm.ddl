metadata :name        => "VxVM",
         :description => "Agent to handle VxVM creation on LITP",
         :author      => "Ericsson AB",
         :license     => "Ericsson",
         :version     => "1.0",
         :url         => "http://ericsson.com",
         :timeout     => 300

action "create_volume", :description => "Create a Volume" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we want create",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_size,
          :prompt      => "Volume Size",
          :description => "The size of the Volume we want create",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "resize_volume", :description => "Resize a Volume" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we want to resize",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_size,
          :prompt      => "Volume Size",
          :description => "The size we want to resize the volume to.",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "resize_disk", :description => "Resize a Disk" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :disk_name,
          :prompt      => "The Disk Name",
          :description => "The name of the Disk we want to resize",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :force_flag,
          :prompt      => "Whether to use -f flag",
          :description => "Whether to use -f flag",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end


action "prepare_volume", :description => "Prepare a Volume" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we want create",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300


    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "disable_volume", :description => "Disable a Volume" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we want disable",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "delete_volume", :description => "Disable a Volume" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we want delete",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "create_filesystem", :description => "Create a file system" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we want create",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "create_disk_group", :description => "Create a disk group" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :disk_names,
          :prompt      => "Disk names",
          :description => "List of disk names divided by space",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 0

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "destroy_disk_group", :description => "Destroy a disk group" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "deport_disk_group", :description => "Deport a disk group" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "del_disk_from_group", :description => "Delete disk to group" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "Disk Name",
          :description => "The name of the Disk",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "add_disk_to_group", :description => "Add disk to group" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :disk_name,
          :prompt      => "Disk Name",
          :description => "The name of the Disk",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "stop_volumes", :description => "Stop all volumes in disk group" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "clear_keys", :description => "Remove SCSI-3 Registration keys from disk" do
    display :always

    input :disk_name,
          :prompt      => "Disk name",
          :description => "Disk name",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "setup_disk", :description => "Prepare disk for VxVm" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :disk_name,
          :prompt      => "Disk name",
          :description => "Disk name",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "unsetup_disk", :description => "Unprepare disk" do
    display :always


    input :disk_name,
          :prompt      => "Disk name",
          :description => "Disk name",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "snapshot_volume", :description => "Snapshot volume" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :volume_name,
          :prompt      => "The Volume Name",
          :description => "The name of the Volume we will be snapshoting",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :snapshot_name,
          :prompt      => "The Snap Name",
          :description => "The name of the Snapshot to be taken",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300


    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "delete_snapshot", :description => "Delete snapshot" do
    display :always

    input :disk_group,
          :prompt      => "Disk Group Name",
          :description => "The name of the Disk Group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300


    input :snapshot_name,
          :prompt      => "The Snap Name",
          :description => "The name of the Snapshot to be deleted",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300


    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "get_dg_hostname", :description => "Check where DG is available" do
    display :always

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end

action "is_snapshot_present", :description => "Check if snapshot exists for a given diskgroup" do
     display :always

     input  :snapshot_name,
            :prompt      => "Name of snapshot to check for",
            :description => "The name of the snapshot to check for",
            :type        => :string,
            :validation  => '',
            :optional    => false,
            :maxlength   => 0

    input  :disk_group,
           :prompt      => "Disk Group Name",
           :description => "The name of the disk group",
           :type        => :string,
           :validation  => '',
           :optional    => false,
           :maxlength   => 0

    input  :check_for_cache,
           :prompt      => "Check for cache volume and object required",
           :description => "Should a check for the cache volume and object be performed",
           :type        => :string,
           :validation  => '',
           :optional    => false,
           :maxlength   => 0

     output  :status,
             :description => "The exit code from running the command",
             :display_as => "Result code"

     output :out,
            :description => "The stdout from running the command",
            :display_as => "out"

     output :err,
            :description => "The stderr from running the command",
            :display_as => "err"
end

action "vgdg_set_coordinator_on", :description => "Sets the disk as coordinator" do
    display :always

    input  :disk_group_name,
           :prompt      => "Disk Group Name",
           :description => "The name of the disk group",
           :type        => :string,
           :validation  => '',
           :optional    => false,
           :maxlength   => 0

    input  :sys,
           :prompt      => "System",
           :description => "The system to run the command on",
           :type        => :string,
           :validation  => '',
           :optional    => true,
           :maxlength   => 0

    output :retcode,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"

end

action "vgdg_set_coordinator_off", :description => "Sets the disk group as not coordinator" do
    display :always

    input  :disk_group_name,
           :prompt      => "Disk Group Name",
           :description => "The name of the disk group",
           :type        => :string,
           :validation  => '',
           :optional    => false,
           :maxlength   => 0

    input  :sys,
           :prompt      => "System",
           :description => "The system to run the command on",
           :type        => :string,
           :validation  => '',
           :optional    => true,
           :maxlength   => 0

    output :retcode,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"

end

action "import_disk_group_t_flag", :description => "Imports the disk group with -t flag" do
    display :always

    input  :disk_group_name,
           :prompt      => "Disk Group Name",
           :description => "The name of the disk group",
           :type        => :string,
           :validation  => '',
           :optional    => false,
           :maxlength   => 0

    input  :sys,
           :prompt      => "System",
           :description => "The system to run the command on",
           :type        => :string,
           :validation  => '',
           :optional    => true,
           :maxlength   => 0

    output :retcode,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"

end

action "import_disk_group", :description => "Imports the disk group" do
    display :always

    input  :disk_group_name,
           :prompt      => "Disk Group Name",
           :description => "The name of the disk group",
           :type        => :string,
           :validation  => '',
           :optional    => false,
           :maxlength   => 0

    input  :force,
           :prompt      => "Force import",
           :description => "Force the disk group import",
           :type        => :boolean,
           :optional    => true

    output :retcode,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"

end

action "vxdisk_list", :description => "Shows information on a disk" do
    display :always

    input :disk_name,
          :prompt      => "Disk name",
          :description => "Disk name",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The exit code from running the command",
           :display_as => "Result code"

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"

    output :err,
           :description => "The stderr from running the command",
           :display_as => "err"
end
