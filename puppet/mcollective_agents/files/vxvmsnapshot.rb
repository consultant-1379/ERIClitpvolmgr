def get_cache_name(snap_name, object_key)
  # there needs to be an object for each snapshot due to named snaps
  # co = cache_object, cv = cache_volume
  if snap_name.start_with?("L_") then
    if object_key == "co" then
      snap_name.sub("L_", "LO")
    elsif object_key == "cv" then
      snap_name.sub("L_", "LV")
    else
      raise "unknown object_key: #{object_key}"
    end
   else
     raise "unknown snap_name convention: #{snap_name}"
  end
end

module MCollective
  module Agent
    class Vxvmsnapshot < RPC::Agent
      begin
        PluginManager.loadclass("MCollective::Util::LogAction")
        PluginManager.loadclass("MCollective::Util::VxvmUtils")
        log_action = Util::LogAction
        vx_utils = Util::VxvmUtils
      rescue LoadError => e
        raise "Cannot load utils: %s" % [e.to_s]
      end

      action "check_active_nodes" do
        disk_groups = vx_utils.disk_groups_in_node()
        reply[:out] = disk_groups.to_json
      end

      action "create_snapshot" do
        disk_group = request[:disk_group]
        volume_name = request[:volume_name]
        volume_size = request[:volume_size]
        snap_name = request[:snapshot_name]
        region_size = request[:region_size]

        cache_vol = get_cache_name(snap_name, "cv")
        cache_object = get_cache_name(snap_name, "co")

        # Due to TORF-599335 it was necessary to split the commands up.
        # status code 12: Record already exists in disk group
        cmd = "/opt/VRTS/bin/vxassist -g #{disk_group} make #{cache_vol} #{volume_size} init=active"
        good_rc_a = [0, 12]
        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
        return unless good_rc_a.include?(reply[:status])

        # Update to handle TORF-599335. The cache volume may be temporarily locked and the cache object will then fail.
        # Which can lead to ERROR V-5-1-15315 Cannot allocate space for snapshots due to failure to delete cache volumes when there is a failure to create a cache object from that cache volume.
        # status code 12: VxVM vxmake ERROR V-5-1-6317 Cache LOvol0_ already exists
        vxmake_cmd = "/opt/VRTS/bin/vxmake -g #{disk_group} cache #{cache_object} cachevolname=#{cache_vol} regionsize=#{region_size}"
        vxmake_cmd_good_rc_a = [0, 12]
        log_action.debug(vxmake_cmd, request)
        reply[:status] = run(vxmake_cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
        unless vxmake_cmd_good_rc_a.include?(reply[:status])
          cache_vol_free = false
          counter = 60
          start = Time.now
          while Time.now - start <= counter
            # Check status of the groups devopen
            devopen_cmd = "/usr/sbin/vxprint -g #{disk_group} -m #{cache_vol} -F %devopen"
            devopen_cmd_for_log = "/usr/sbin/vxprint -g #{disk_group} -m #{cache_vol} -F %%devopen"

            log_action.log("#{request.action}", "Running command #{devopen_cmd_for_log}")
            reply[:status] = run(devopen_cmd,
                                 :stdout => :out,
                                 :stderr => :err,
                                 :chomp => true)
            if reply[:out].include?("off")
              cache_vol_free = true
              break
            end
            sleep 5
          end
          if cache_vol_free == true
            # The vxmake command should now work.
            log_action.debug(vxmake_cmd, request)
            reply[:status] = run(vxmake_cmd,
                                 :stdout => :out,
                                 :stderr => :err,
                                 :chomp => true)
            return unless vxmake_cmd_good_rc_a.include?(reply[:status])
          else
            # Fail as the vxmake command will not succeed. Note that there will be a cache_volume remaining which will cause issues if it's not removed.
            log_action.log("#{request.action}", "Failed to create #{disk_group} snapshot #{snap_name} as attribute 'devopen' of #{cache_vol} is not 'off'")
            reply[:status] = 1
            reply[:out] = ""
            reply[:err] = "Failed to create snapshot #{snap_name} of #{disk_group} as #{cache_vol} attribute 'devopen' is not 'off'"
            return reply
          end
        end

        commands = [
          # status code 25: VxVM vxcache INFO V-5-1-6325 Cache LOvol0_ is already started
          ["/opt/VRTS/bin/vxcache -g #{disk_group} start #{cache_object}", [0, 25]],
          ["/opt/VRTS/bin/vxsnap -g #{disk_group} make source=#{volume_name}/newvol=#{snap_name}/cache=#{cache_object}", [0]]
        ]
        commands.each do |cmd, good_rc_a|
          log_action.debug(cmd, request)
          reply[:status] = run(cmd,
                               :stdout => :out,
                               :stderr => :err,
                               :chomp => true)
          break unless good_rc_a.include?(reply[:status])
        end
      end

      action "restore_snapshot" do
        disk_group = request[:disk_group]
        vol_name = request[:volume_name]
        snap_name = request[:snapshot_name]

        cmd = "/opt/VRTS/bin/vxsnap -g #{disk_group} restore #{vol_name} source=#{snap_name}"
        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "check_dcoregionsz" do
        disk_group = request[:disk_group]

        cmd = "vxprint -g #{disk_group} -m | grep dcoregionsz | awk -F'=' '{print $2}'"
        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "delete_snapshot" do
        disk_group = request[:disk_group]
        snap_name = request[:snapshot_name]
        cache_object = get_cache_name(snap_name, "co")

        commands = [
          ["/opt/VRTS/bin/vxsnap -g #{disk_group} dis #{snap_name}", [0, 2, 20]],
          # status code 20: snapshot disassociated
          # status code 2: snapshot already deleted
          ["/opt/VRTS/bin/vxedit -g #{disk_group} -rf rm #{snap_name}", [0, 11]],
          # status code 11: snapshot already deleted
          ["/opt/VRTS/bin/vxcache -g #{disk_group} stop #{cache_object}", [0, 11, 26]],
          # status code 11: VxVM vxcache ERROR V-5-1-6332 Cache test not in configuration
          # status code 26: VxVM vxcache ERROR V-5-1-6326 Cache is already stopped
          ["/opt/VRTS/bin/vxedit -g #{disk_group} -rf rm #{cache_object}", [0, 11]],
          # status code 11: VxVM vxedit ERROR V-5-1-926 Record test not in disk group configuration
        ]
        successful_command_chain = true
        commands.each do |cmd, good_rc_a|
          log_action.debug(cmd, request)
          reply[:status] = run(cmd,
                               :stdout => :out,
                               :stderr => :err,
                               :chomp => true)
          unless good_rc_a.include?(reply[:status])
            successful_command_chain = false
            break
          end
        end
        if successful_command_chain
          reply[:status] = 0
          reply[:out] = ""
          reply[:err] = ""
        end
      end

      action "check_snapshot" do
        STATE_COLUMN = 5
        disk_group = request[:disk_group]
        snapshot_name = request[:snapshot_name]

        result = ""
        return_value = 0

        cmd = "/opt/VRTS/bin/vxprint -g %s -t %s | grep %s" % [disk_group, snapshot_name, snapshot_name]
        log_action.debug(cmd, request)
        run(cmd,
            :stdout => result,
            :stderr => :err)

        cols = result.split(" ")
        #http://www.filibeto.org/unix/hp-ux/lib/cluster/veritas-sf-ha-docs/docsets/vxvm/html/vol_admin_vxvm107_108071.html
        if cols.size >= STATE_COLUMN && "INVALID" == cols[STATE_COLUMN-1]
           return_value = 1
           reply[:err] = "Snapshot '%s', on disk group '%s', is not valid" % [snapshot_name, disk_group]
        end

        if result == ""
           return_value = 1
           reply[:err] = "Snapshot '%s' does not exist on disk group '%s'" % [snapshot_name, disk_group]
        end

        reply[:status] = return_value
        reply[:out] = result

      end

      action "check_restore_completed" do
        disk_group = request[:disk_group]
        vol_name = request[:volume_name]

        cmd = "/opt/VRTS/bin/vxprint -m -g #{disk_group} #{vol_name} | grep 'restore=' | grep -v 'snap' | awk -F '=' '{print $2}' "

        log_action.debug(cmd, request)

        reply[:status] = run(cmd,
          :stdout => :out,
          :stderr => :err,
          :chomp => true)
      end

    end
  end
end
