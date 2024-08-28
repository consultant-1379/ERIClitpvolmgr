require 'json'
require 'set'
ENV['FACTERLIB'] = "/var/lib/puppet/lib/facter"

module MCollective
  module Agent
    class Vxvm < RPC::Agent
      begin
        PluginManager.loadclass("MCollective::Util::LogAction")
        PluginManager.loadclass("MCollective::Util::VxvmUtils")
        log_action = Util::LogAction
        vx_utils = Util::VxvmUtils
      rescue LoadError => e
        raise "Cannot load utils: %s" % [e.to_s]
      end

      def self.error_match(err_msg, proc_err)
        proc_err.each do |err|
          if err_msg.include?(err)
            return true
          end
        end
        return false
      end

      def get_cache_name(snap_name, object_key)
        if snap_name.start_with?("L_") then
          if object_key == "co" then
            snap_name.sub("L_", "LO")
          elsif object_key == "cv" then
            snap_name.sub("L_", "LV")
          end
        else
           return nil
        end
      end

      def flush_disk_group(disk_group)
        run("/opt/VRTS/bin/vxdg -g %s flush" % disk_group)
      end

      def disk_is_in_group(disk_name, disk_group)
        retcode = run("/opt/VRTS/bin/vxdisk list -e -o alldgs | grep %s | grep %s" % [disk_name, disk_group],
                    :stdout => :out,
                    :stderr => :err)

        return retcode == 0
      end

      def self.look_for_stderr(rpl_hash, proc_err)
        # looking for the processable errors
        # in std error
        error_msg = rpl_hash[:err].to_s
        if rpl_hash[:status] != '0' && Vxvm.error_match(error_msg, proc_err)
          return {:status => 0, :out => "Ignoring idempotent task result: #{error_msg}", :err => ""}
        else
          return rpl_hash
        end
      end

      action "create_volume" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]
        volume_size = request[:volume_size]

        cmd = "/opt/VRTS/bin/vxprint -g #{disk_group} | grep '^v ' | awk '$2==\"#{volume_name}\"'"
        log_action.debug(cmd, request)
        rpl_hash = {:out => "", :err => ""}
        rpl_hash[:status] = run("#{cmd}",
          :stdout => rpl_hash[:out],
          :stderr => rpl_hash[:err],
          :chomp => true)
        if rpl_hash[:out] != ""
            reply[:status] = 0
            reply[:out] = "Ignoring idempotent task result"
            reply[:err] = ""
        else
            cmd = "/opt/VRTS/bin/vxassist -g #{disk_group} make #{volume_name} #{volume_size}"
            log_action.debug(cmd, request)

            rpl_hash = {:out => "", :err => ""}
            rpl_hash[:status] = run("#{cmd}",
              :stdout => rpl_hash[:out],
              :stderr => rpl_hash[:err],
              :chomp => true)

            proc_errors = [
              "Record already exists in disk group"
            ]
            the_hash = Vxvm.look_for_stderr(rpl_hash, proc_errors)
            reply[:status] = the_hash[:status]
            reply[:out] = the_hash[:out]
            reply[:err] = the_hash[:err]
        end
      end

      action "resize_volume" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]
        volume_size = request[:volume_size]

        cmd = "/opt/VRTS/bin/vxresize -g #{disk_group} #{volume_name} #{volume_size}"
        log_action.debug(cmd, request)

        reply[:status] = run("#{cmd}",
            :stdout => :out,
            :stderr => :err,
            :chomp => true)

        log_action.debug(reply, request)
      end

      action "resize_disk" do
        disk_group = request[:disk_group]
        disk_name = request[:disk_name]
        force_flag = ''
        if request[:force_flag] == 'True'
            force_flag = '-f'
        end
        vxvm_disk_name = vx_utils.vx_disk_name(disk_name)

        commands = [
          ["/opt/VRTS/bin/vxdisk resize #{force_flag} -g #{disk_group} #{vxvm_disk_name}", [0]],
          ["/opt/VRTS/bin/vxdg flush", [0]],
          ["/opt/VRTS/bin/vxconfigbackup", [0,2]]
        ]
        commands.each do |cmd, good_rc_a|
          log_action.debug(cmd, request)
          reply[:status] = run(cmd,
                               :stdout => :out,
                               :stderr => :err,
                               :chomp => true)
          break unless good_rc_a.include?(reply[:status])
        end

        if reply[:status] == 2
          reply[:status] = 0
        end
      end

      action "prepare_volume" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]

        cmd = "/opt/VRTS/bin/vxsnap -g #{disk_group} -b prepare #{volume_name} regionsize=1M"
        log_action.debug(cmd, request)

        rpl_hash = {:out => "", :err => ""}
        rpl_hash[:status] = run("#{cmd}",
          :stdout => rpl_hash[:out],
          :stderr => rpl_hash[:err],
          :chomp => true)

        proc_errors = [
          "volume #{volume_name} is already instant ready"
        ]
        the_hash = Vxvm.look_for_stderr(rpl_hash, proc_errors)
        reply[:status] = the_hash[:status]
        reply[:out] = the_hash[:out]
        reply[:err] = the_hash[:err]
      end

      action "add_disk_to_group" do
          disk_group = request[:disk_group]
          disk_name = request[:disk_name]
          vxvm_disk_name = vx_utils.vx_disk_name(disk_name)

          # 10990
          if disk_is_in_group(vxvm_disk_name, disk_group)
              msg = "Disk '%s' is already in group '%s'" % [disk_name, disk_group]
              reply[:status] = 0
              reply[:out] = msg
              log_action.log("vxvm:add_disk_to_group", msg)
          else
              flush_disk_group(disk_group)

              cmd = "/opt/VRTS/bin/vxdg -g #{disk_group} adddisk #{vxvm_disk_name}"
              log_action.debug(cmd, request)

              reply[:status] = run("#{cmd}",
                :stdout => :out,
                :stderr => :err,
                :chomp => true)
          end
      end

      action "del_disk_from_group" do
          disk_group = request[:disk_group]
          disk_name = request[:disk_name]
          vxvm_disk_name = vx_utils.vx_disk_name(disk_name)

          cmd = "/opt/VRTS/bin/vxdg -g #{disk_group} rmdisk #{vxmv_disk_name}"
          log_action.debug(cmd, request)
          reply[:status] = run("#{cmd}",
            :stdout => :out,
            :stderr => :err,
            :chomp => true)
      end

      action "delete_volume" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]
        cmd = "/opt/VRTS/bin/vxassist -g #{disk_group} remove volume #{volume_name}"

        log_action.debug(cmd, request)

        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
      end

      action "disable_volume" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]
        cmd = "/opt/VRTS/bin/vxvol -g  #{disk_group} stop #{volume_name}"
        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
      end

      action "create_filesystem" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]

        cmd = "/sbin/mkfs -t vxfs /dev/vx/dsk/#{disk_group}/#{volume_name}"

        log_action.debug(cmd, request)
        rpl_hash = {:out => "", :err => ""}
        rpl_hash[:status] = run("#{cmd}",
          :stdout => rpl_hash[:out],
          :stderr => rpl_hash[:err],
          :chomp => true)
        proc_errors = [
          "#{volume_name} is mounted, cannot mkfs"
        ]
        the_hash = Vxvm.look_for_stderr(rpl_hash, proc_errors)
        reply[:status] = the_hash[:status]
        reply[:out] = the_hash[:out]
        reply[:err] = the_hash[:err]
      end

      action "clear_keys" do
        disk_name = request[:disk_name]
        vxvm_disk_name = vx_utils.vx_disk_name(disk_name, scan_disk=true)

        # First we need to get a device path to the disk
        cmd = "/opt/VRTSvcs/vxfen/bin/vxfendisk -d #{vxvm_disk_name} -a listpath -p char"
        log_action.debug(cmd, request)

        reply[:status] = run("#{cmd}",
                              :stdout => :out,
                              :stderr => :err,
                              :chomp => true)

        # We need to check STDERR as return code will be always 0
        if reply[:err].include?("ERROR")
          reply[:status] = 1
          reply.fail!("Mcollective action clear_keys failed: #{reply[:err]}")
        end

        status, msg_reg = vx_utils.clear_SCSI3_registrations(reply[:out], vxvm_disk_name)
        if status != 0
           reply.fail!(msg_reg)
        end

        status, msg_res = vx_utils.clear_SCSI3_reservations(reply[:out], vxvm_disk_name)
        if status != 0
          reply.fail!(msg_res)
        else
          reply[:out] = "#{msg_reg}\n#{msg_res}"
        end
      end

      action "setup_disk" do
        disk_name = request[:disk_name]
        disk_group = request[:disk_group]
        vxvm_disk_name = vx_utils.vx_disk_name(disk_name, scan_disk=true)
        vx_disk_setup = "/opt/VRTS/bin/vxdisksetup -if #{vxvm_disk_name} format=sliced"
        vx_disk_unsetup = "/opt/VRTS/bin/vxdiskunsetup -Cf #{vxvm_disk_name}"
        cmd = "if ! #{vx_disk_setup}; then #{vx_disk_unsetup}; #{vx_disk_setup}; fi"
        log_action.debug(cmd, request)

        rpl_hash = {:out => "", :err => ""}
        rpl_hash[:status] = run("#{cmd}",
          :stdout => rpl_hash[:out],
          :stderr => rpl_hash[:err],
          :chomp => true)

        proc_errors = [
          "Disk cannot be reinitialized."
        ]
        the_hash = Vxvm.look_for_stderr(rpl_hash, proc_errors)
        reply[:status] = the_hash[:status]
        reply[:out] = the_hash[:out]
        reply[:err] = the_hash[:err]
      end

      action "unsetup_disk" do
        disk_name = request[:disk_name]
        vxvm_disk_name = vx_utils.vx_disk_name(disk_name)

        cmd = "/opt/VRTS/bin/vxdiskunsetup -Cf #{vxvm_disk_name}"
        log_action.debug(cmd, request)
        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
        # TORF-339263: VxVM unsetup works, except when the snasphot is restored, the plan retry fails
        # because VxVM unsetup was already successful
        # Make task succeed even if VxVM disks and volumes are gone
        # VxVM vxdiskunsetup ERROR V-5-2-3522 #{vxvm_disk_name}: Disk is not a volume manager disk
        if reply[:status] == 1
          reply[:status] = 0
          reply[:out] = reply[:err]
          reply[:err] = ""
        end
      end

      action "create_disk_group" do
        disk_group = request[:disk_group]
        disk_names = request[:disk_names]
        # trying import disk group to recover plan
        vx_utils.import_disk_group(disk_group)

        device_names = disk_names.split(" ").map! { |name| vx_utils.vx_disk_name(name, scan_disk=true) }.join(" ")

        cmd = "/opt/VRTS/bin/vxdg init #{disk_group} #{device_names} cds=off"
        log_action.debug(cmd, request)

        rpl_hash = {:out => "", :err => ""}
        rpl_hash[:status] = run("#{cmd}",
          :stdout => rpl_hash[:out],
          :stderr => rpl_hash[:err],
          :chomp => true)

        proc_errors = [
          "Disk group #{disk_group}: cannot create: Disk group exists and is imported",
          "Disk group import of #{disk_group} failed with error 122 - Disk group exists and is imported"
        ]
        the_hash = Vxvm.look_for_stderr(rpl_hash, proc_errors)
        reply[:status] = the_hash[:status]
        reply[:out] = the_hash[:out]
        reply[:err] = the_hash[:err]
      end

      action "destroy_disk_group" do
        disk_group = request[:disk_group]

        cmd = "/opt/VRTS/bin/vxdg destroy #{disk_group}"
        log_action.debug(cmd, request)

        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
        # see previous comment about TORF-339263, idempotent VxVM unsetup tasks
        # VxVM vxdg ERROR V-5-1-2275 vxdg: Disk group #{disk_group}: No such disk group
        if reply[:status] == 11
          reply[:status] = 0
          reply[:out] = reply[:err]
          reply[:err] = ""
        end
      end

      action "deport_disk_group" do
        disk_group = request[:disk_group]

        cmd = "/opt/VRTS/bin/vxdg deport #{disk_group}"

        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)

      end

      action "stop_volumes" do
        disk_group = request[:disk_group]
        # trying import disk group to recover plan
        vx_utils.import_disk_group(disk_group)
        cmd = "/opt/VRTS/bin/vxvol -g #{disk_group} stopall"
        log_action.debug(cmd, request)

        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
        # see previous comment about TORF-339263, idempotent VxVM unsetup tasks
        # VxVM vxvol ERROR V-5-1-607 Diskgroup #{disk_group} not found
        if reply[:status] == 9
          reply[:status] = 0
          reply[:out] = reply[:err]
          reply[:err] = ""
        end
      end

      action "show_disk_group_properties" do
        disk_group = request[:disk_group]

        cmd = "/opt/VRTS/bin/vxinfo -p -g #{disk_group}"
        log_action.debug(cmd, request)

        reply[:status] = run("#{cmd}",
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
      end

      action "get_dg_hostname" do
        data = vx_utils.snapshot_list()
        reply[:status] =  data[1]
        reply[:out] = data[0].to_json
        reply[:err] = data[2]
      end

      action "is_snapshot_present" do
        snapshot_name = request[:snapshot_name]
        disk_group = request[:disk_group]
        check_for_cache = request[:check_for_cache]

        cmd = "/opt/VRTS/bin/vxprint -g #{disk_group} "
        cmd << "| grep -e '^sp #{snapshot_name}'"

        if check_for_cache == "False"
            reply[:status] = run("#{cmd}",
              :stdout => :out,
              :stderr => :err,
              :chomp => true)
        else
            cache_object = get_cache_name(snapshot_name, "co")
            cache_volume = get_cache_name(snapshot_name, "cv")

            commands = []

            # 3 commands are run, 1 for each of Snapshot, cache-volume & cache-object.
            # All 3 must be missing for overall failure. Presence of any 1 is success.
            commands << cmd

            if !cache_volume.nil?
                cmd2 = "/opt/VRTS/bin/vxprint -g #{disk_group} "
                cmd2 << "| grep -E '^v  %s'" % cache_volume
                commands << cmd2
            end

            if !cache_object.nil?
                cmd3 = "/opt/VRTS/bin/vxprint -g #{disk_group} "
                cmd3 << "| grep -E '^co %s'" % cache_object
                commands << cmd3
            end

            status = 0
            reply[:out] = ''
            reply[:err] = ''
            commands.each do |cmd|
                log_action.debug(cmd, request)
                stat, out, err = systemu(cmd)
                if stat.exitstatus == 0
                    status = 0
                    reply[:out] = ''
                    reply[:err] = ''
                    break
                else
                    status += 1
                    reply[:err] << err
                    reply[:out] << out
                end
            end
            reply[:status] = status
        end
      end

      action "vgdg_set_coordinator_on" do
        disk_group_name = request[:disk_group_name]
        sys = request[:sys]

        cmd = "/opt/VRTS/bin/vxdg -g #{disk_group_name}"
        cmd << " set coordinator=on"
        if !sys.nil? then
          cmd << " -sys #{sys}"
        end
        log_action.debug(cmd, request)
        reply[:retcode] = run("#{cmd}",
               :stdout => :out,
               :stderr => :err,
               :chomp => true)
      end

      action "vgdg_set_coordinator_off" do
        disk_group_name = request[:disk_group_name]
        sys = request[:sys]

        cmd = "/opt/VRTS/bin/vxdg -g #{disk_group_name}"
        cmd << " set coordinator=off"
        if !sys.nil? then
          cmd << " -sys #{sys}"
        end
        log_action.debug(cmd, request)
        reply[:retcode] = run("#{cmd}",
               :stdout => :out,
               :stderr => :err,
               :chomp => true)
      end

      action "import_disk_group_t_flag" do
        disk_group_name = request[:disk_group_name]
        sys = request[:sys]

        cmd = "/opt/VRTS/bin/vxdg -t import #{disk_group_name}"
        if !sys.nil? then
          cmd << " -sys #{sys}"
        end
        log_action.debug(cmd, request)
        reply[:retcode] = run("#{cmd}",
               :stdout => :out,
               :stderr => :err,
               :chomp => true)
      end
      action "import_disk_group" do
        disk_group_name = request[:disk_group_name]
        if request[:force] then
          cmd = "/opt/VRTS/bin/vxdg -f import #{disk_group_name}"
        else
          cmd = "/opt/VRTS/bin/vxdg import #{disk_group_name}"
        end
        log_action.debug(cmd, request)
        reply[:retcode] = run("#{cmd}",
               :stdout => :out,
               :stderr => :err,
               :chomp => true)
      end
      action "vxdisk_list" do
          disk_name = request[:disk_name]
          vxvm_disk_name = vx_utils.vx_disk_name(disk_name, scan_disk=true)
          cmd = "/opt/VRTS/bin/vxdisk list #{vxvm_disk_name}"
          log_action.debug(cmd, request)
          reply[:status] = run("#{cmd}",
                :stdout => :out,
                :stderr => :err,
                :chomp => true)
      end
    end
  end
end
