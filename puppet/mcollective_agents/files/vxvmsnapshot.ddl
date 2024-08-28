metadata :name        => "VxVM Snapshot",
         :description => "Agent to handle VxVM snapshot on LITP",
         :author      => "Ericsson AB",
         :license     => "Ericsson",
         :version     => "1.0",
         :url         => "http://ericsson.com",
         :timeout     => 302

action "check_active_nodes", :description => "Check DGs" do
    display :always

    output :out,
           :description => "The stdout from running the command",
           :display_as => "out"
end

action "create_snapshot", :description => "Snapshot volume" do
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

    input :volume_size,
          :prompt      => "Volume Size",
          :description => "The size of the Volume we want create",
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

    input :region_size,
          :prompt      => "The Region size",
          :description => "The size of the cache regionsz to set",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 5


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

action "check_dcoregionsz", :description => "Check dcoregionsz" do
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

action "restore_snapshot", :description => "Restore snapshot" do
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
          :description => "The name of the Volume to be restored",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :snapshot_name,
          :prompt      => "The Snap Name",
          :description => "The name of the Snapshot to be restored",
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

action "check_snapshot", :description => "Checks snapshot integrity" do
    display :always

    input :disk_group,
          :prompt      => "Disk group",
          :description => "Disk group",
          :type        => :string,
          :validation  => '',
          :optional    => false,
          :maxlength   => 300

    input :snapshot_name,
          :prompt      => "Snapshot name",
          :description => "Snapshot name",
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

action "check_restore_completed", :description => "Check restore progress" do
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
          :description => "The name of the Volume to be restored",
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
