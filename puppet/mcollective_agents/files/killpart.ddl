metadata :name        => "killpart",
     :description => "Drive partition eraser",
     :author      => "Ericsson AB",
     :license     => "Ericsson",
     :version     => "1.0",
     :timeout     => 60,
     :url         => "http://ericsson.com"

action "clear", :description => "Clear partitions from devices" do
    display :always

    input :uuid,
          :prompt      => "Device UUID",
          :description => "Device to erase",
          :type        => :string,
          :optional    => false,
          :validation  => /[a-zA-Z]+/,
          :maxlength   => 96

    output :status,
           :description => "The status of the clear request",
           :display_as  => "Clearout status",
           :default     => "unknown"
end
