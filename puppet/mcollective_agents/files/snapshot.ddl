metadata :name        => "snapshot",
         :description => "Agent to handle snapshots in LITP",
         :author      => "Ericsson AB",
         :license     => "Ericsson",
         :version     => "1.0",
         :url         => "http://ericsson.com",
         :timeout     => 300

action "create", :description => "Creates a snapshot of the volume" do
    display :always

    input :path,
          :prompt      => "Volume Path",
          :description => "The volume to snapshot",
          :type        => :string,
          :validation  => '^[a-zA-Z\/\-_\d]+$',
          :optional    => false,
          :maxlength   => 132 # maximum length from '/dev/' + 127 maximum device name length

    input :size,
          :prompt      => "Snapshot Size",
          :description => "The size of the snapshot plus its unit",
          :type        => :string,
          :validation  => '^\d+(\.\d*)?[B|M|G|T]$',
          :optional    => false,
          :maxlength   => 90

    input :name,
          :prompt      => "Snapshot Name",
          :description => "The name of the created snapshot",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 127 # 127 is the maximum LVM device name length

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"

    output :path,
           :description => "The snapshotted volume",
           :display_as  => "Snapshotted volume",
           :default     => ""
end

action "remove", :description => "Removes a snapshot in a volume group" do
    display :always

    input :vg,
          :prompt      => "Volume Group",
          :description => "The volume group that contains the snapshot",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 0 # no need to restrict on removal

    input :name,
          :prompt      => "Snapshot Name",
          :description => "The name of the snapshot to remove",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 0 # no need to restrict on removal

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"
end

action "is_merging", :description => "Checks if a snapshot is merging" do
    display :always

    input :vg,
          :prompt      => "Volume Group",
          :description => "The volume group that contains the snapshot",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 0 # no need to restrict on removal

    input :name,
          :prompt      => "Snapshot Name",
          :description => "The name of the snapshot to remove",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 0 # no need to restrict on removal

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"
end

action "restore", :description => "Restore a snapshot in a volume group" do
    display :always

    input :vg,
          :prompt      => "Volume Group",
          :description => "The volume group that contains the snapshot",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 0 # no need to restrict on restore

    input :name,
          :prompt      => "Snapshot Name",
          :description => "The name of the snapshot to be restored",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_\d]+$',
          :optional    => false,
          :maxlength   => 0 # no need to restrict on restore

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"
end

action "save_grub", :description => "Create copy of grub.conf file" do
    display :always

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"
end

action "remove_grub", :description => "Remove the copy of grub.conf file" do
    display :always

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"
end

action "restore_grub", :description => "Restore the copy of grub.conf file" do
    display :always

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result",
           :default     => "no output"
end
