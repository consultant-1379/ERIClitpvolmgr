metadata :name        => "lv",
         :description => "Agent to handle lvs in LITP",
         :author      => "Ericsson AB",
         :license     => "Ericsson",
         :version     => "1.0",
         :url         => "http://ericsson.com",
         :timeout     => 60

action "lvs", :description => "Get a scan of present LVs" do
    display :always

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result"
end

action "lsblk", :description => "Get info on LVs and the file system associated" do
    display :always

    input :path,
          :prompt      => "Volume Path",
          :description => "The FS examined",
          :type        => :string,
          :validation  => '^[a-zA-Z\/\-_\d]+$',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result"
end

action "vg2fact", :description => "Map a VG name to a fact name" do
    display :always

    input :vgname,
          :prompt      => "Volume group name",
          :description => "The volume group name to be mapped",
          :type        => :string,
          :validation  => '^[a-zA-Z\-_]+$',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result"
end

action "pvresize", :description => "Execute pvresize on a disk" do
    display :always

    input :disk_fact_name,
          :prompt      => "Disk fact name",
          :description => "Path of the disk device",
          :type        => :string,
          :validation  => '^[a-zA-Z0-9\-_]+$',
          :optional    => false,
          :maxlength   => 300

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result"
end

action "pvs", :description => "Execute pvs" do
    display :always

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result"
end

action "vgs", :description => "Execute vgs" do
    display :always

    output :status,
           :description => "The output of the command",
           :display_as  => "Command result"
end