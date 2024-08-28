metadata :name        => "grub",
         :description => "Agent to update lv names in grub files and re-make the grub",
         :author      => "Ericsson AB",
         :license     => "Ericsson",
         :version     => "1.0",
         :url         => "http://ericsson.com",
         :timeout     => 300

action "update_grub", :description => "Replace lv names in grub files and re-make the grub" do
    display :always

    input  :lv_names,
           :prompt      => "Lv names",
           :description => "Space separated list of logical volume names to include in grub files",
           :type        => :string,
           :validation  => '.*',
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