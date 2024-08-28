require 'securerandom'

module MCollective
    module Agent
        class Killpart < RPC::Agent

            begin
                PluginManager.loadclass("MCollective::Util::LogAction")
                log_action = Util::LogAction
            rescue LoadError => e
                raise "Cannot load utils: %s" % [e.to_s]
            end

            @@success = 0
            @@error = 1
            @@no_operation = 2

            facter_output = ""

            def device_from_uuid(dev_uuid)
                if dev_uuid == 'kgb'
                    # XXX(xigomil) LITPCDS-5766 Fix disk device name
                    # to enable KGB in the E/// Cloud
                    dev_value = "disk_cloud_dev => /dev/sda2"
                else
                    dev_value = ""
                    run("facter -p| grep disk_wmp_%s_dev" % [dev_uuid],
                        :stdout => dev_value,
                        :stderr => :err)
                end
                if dev_value == ""
                    return nil
                end
                dev_value = dev_value.split("=>")[1]
                if dev_value == nil
                    return nil
                end
                dev_name = dev_value.strip.split("/dev/")[1]
                return dev_name
            end

            def get_volgroup_list()
                list_volgroups = Array.new
                raw_volgroups = ""
                run("pvs --noheadings -o name,pv_uuid,vg_name,vg_uuid --separator : 2>/dev/null",
                    :stdout => raw_volgroups,
                    :stderr => :err
                    )
                raw_volgroups.each_line do |vg|
                    vg_line = vg.strip.split(':')
                    vg_info = {
                        :pv_dev => vg_line[0],
                        :pv_uuid => vg_line[1],
                        :vg_uuid => vg_line[3],
                        :vg_name => vg_line[2]
                    }
                    list_volgroups << vg_info
                end
                return list_volgroups
            end

            def vg_names_clash(list_volgroups, vg_name, vg_uuid, pv_dev)
                list_volgroups.each do |vg|
                    # if VGs have matching UUIDs, they do not clash
                    if vg[:vg_name] == vg_name and vg[:pv_dev] != pv_dev and vg[:vg_uuid] != vg_uuid
                        return true
                    end
                end
                return false
            end

            def get_volume_group_map()

                vg_map = {}

                lines = ""

                run("vgs --noheadings -o vg_name,vg_uuid --separator : 2>/dev/null",
                    :stdout => lines, :stderr => :err)

                lines.each_line do |line|

                    line = line.strip.split(':')

                    vg_name = line[0]
                    vg_uuid = line[1]

                    vg_map[vg_uuid] = vg_name
                end

                return vg_map
            end

            def vg_already_exists(vg_name, vg_uuid)

                vgs = get_volume_group_map()

                return (vgs.has_key?(vg_uuid) and vgs[vg_uuid] == vg_name)
            end

            def is_mounted(vg_name, lv_name)

                # much safer check to see if the lv is mounted
                # mitigate risk of false positives

                log_action = Util::LogAction

                label      = "killpart:is_mounted"
                mount_path = "/dev/mapper/#{vg_name}-#{lv_name}"
                lines      = ""

                log_action.log(label, "check for '#{mount_path}' in mount table")

                run("mount", :stdout => lines, :stderr => :err)

                lines.each_line do |line|

                    line = line.strip.split(' ')
                    fs = line[0] # we know it's the first one

                    if fs == mount_path
                        log_action.log(label, "'#{mount_path}' is mounted")
                        return true
                    end
                end

                log_action.log(label, "'#{mount_path}' is not mounted")
                return false
            end

            def kill_logical_volume(lv)

                lvrm_err = ""
                lvrm_out = ""

                log_action = Util::LogAction
                label = "killpart:kill_logical_volume"

                if File.exist?("#{lv}") then

                    log_action.log(label, "removing logical volume '#{lv}'")
                    retcode = run("lvremove -f #{lv}", :stdout => lvrm_out, :stderr => lvrm_err)

                    # try the dmsetup remove step
                    if retcode != @@success

                        log_action.log(label, "lvremove was not successful, #{lvrm_err}, #{lvrm_out}")
                        # Odds are we've got stale /dev/mapper entry - unlink it
                        retcode = run("dmsetup remove #{lv}", :stdout => lvrm_out, :stderr => lvrm_err)
                        log_action.log(label, "dmsetup remove result: out #{lvrm_out}, err #{lvrm_err}")
                    end
                end

                return retcode == @@success
            end

            def kill_logicals(pv_name, vg_name, vg_uuid)

                log_action = Util::LogAction
                label = "killpart:kill_logicals"

                retcode = 0
                logical_volumes = Dir["/dev/%s/*" % [vg_name]]
                logical_volumes.each do |lv|

                    lv_name = lv.split("/")[-1]

                    if is_mounted(vg_name, lv_name)

                        if vg_already_exists(vg_name, vg_uuid)

                            # LITPCDS-12497: check if the volume group is idempotent
                            message = "volume group '#{vg_name}' on '#{pv_name}' is idempotent"
                            log_action.log(label, message)
                            reply[:out] = message

                            return @@no_operation # exit with no-op code
                        else

                            # LITPCDS-9104: if volume is mounted, raise an error
                            message = "logical volume '#{lv_name}' is mounted at '#{mount_path}'"
                            log_action.log(label, message)
                            reply[:err] = message

                            return @@error
                        end

                    else
                        # if it isn't mounted then kill it
                        successful = kill_logical_volume(lv)
                        if not successful
                            return @@error
                        end
                    end
                end

                log_action.log(label, "removing volume group '#{vg_name}'")

                retcode = run("vgremove -f #{vg_name}", :stdout => :out, :stderr => :err)

                if retcode == 0
                    return @@success
                else
                    return @@error
                end
            end

            action "clear" do

                label = "killpart:clear"

                dev_uuid = request[:uuid].downcase.gsub("-","_").strip()
                dev_id = device_from_uuid(dev_uuid)

                log_action.log(label, "Starting clear for uuid '#{dev_uuid}' [#{dev_id}]")

                if dev_id.nil?

                    error_message = "Error getting device name for uuid '#{dev_uuid}'"

                    log_action.log(label, error_message)

                    reply[:status] = @@error
                    reply[:out]    = ""
                    reply[:err]    = error_message

                    return
                end

                list_volgroups = get_volgroup_list()

                if list_volgroups.nil?

                    error_message = "Error getting volume groups list"

                    log_action.log(label, error_message)

                    reply[:status] = @@error
                    reply[:out]    = ""
                    reply[:err]    = error_message

                    return
                else

                    vg_name = nil
                    vg_uuid = nil
                    dev_name = nil

                    # Is this vg the one we are looking for ?
                    @facter_output = `facter -p | grep disk`

                    list_volgroups.each do |vg|

                        if @facter_output.match(".*#{dev_uuid}.* => #{vg[:pv_dev]}.*")
                            vg_name = vg[:vg_name]
                            vg_uuid = vg[:vg_uuid]
                            dev_name = vg[:pv_dev]
                            log_action.log(label ,"'#{dev_uuid}' disk vg found: '#{vg_name}'")
                            break
                        end
                    end

                    unless vg_name.nil?
                        # Have a VG to clear
                        if vg_names_clash(list_volgroups, vg_name, vg_uuid, dev_name)
                            # Clashing VG name.
                            while vg_names_clash(list_volgroups, vg_name, vg_uuid, dev_name)
                                temp_name = SecureRandom.hex[0..6]
                                vg_name = "vg_#{temp_name}"
                            end
                            retcode = run("vgrename #{vg_uuid} #{vg_name}", :stdout => :out, :stderr => :err)
                            if retcode != @@success

                                error_message = "Unable to rename clashing VG name for '#{dev_uuid}'"

                                log_action.log(label, error_message)

                                reply[:status] = @@error
                                reply[:out]    = ""
                                reply[:err]    = error_message

                                return
                            end
                        end

                        retcode = kill_logicals(dev_name, vg_name, vg_uuid)

                        case retcode

                            when @@error
                                reply[:status] = @@error
                                return

                            when @@no_operation
                                reply[:status] = @@success
                                return
                        end
                    end

                    run("dd if=/dev/zero of=/dev/#{dev_id} bs=512 count=32", :stdout => :out, :stderr => :err)
                    run("sfdisk -R /dev/#{dev_id}", :stdout => :out, :stderr => :err)

                    output_message = "Finished clearing out device '#{dev_id}' with uuid '#{dev_uuid}'"

                    log_action.log(label, output_message)

                    reply[:status] = @@success
                    reply[:out]    = output_message
                    reply[:err]    = ""

                    return
                end

            end # action clear
        end
    end
end
