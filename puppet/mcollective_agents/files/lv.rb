module MCollective
  module Agent
    class Lv<RPC::Agent
      begin
        PluginManager.loadclass("MCollective::Util::LogAction")
        log_action = Util::LogAction
      rescue LoadError => e
        raise "Cannot load logaction util: %s" % [e.to_s]
      end

      action "lvs" do
        log_action = Util::LogAction

        cmd = "lvs --unit b -o lv_path,lv_size,lv_attr --noheadings "
        cmd += "| while read lvs_line; do "
        cmd += 'mount_point=""; '
        cmd += "parts=(${lvs_line}); "
        cmd += "if [[ ! ${parts[2]} =~ ^s ]]; then "
        cmd += "mount_point=$(lsblk ${parts[0]} --noheadings -f -o MOUNTPOINT); "
        cmd += "fi; "
        cmd += 'if [ "${mount_point}" == "" ]; then '
        cmd += "mount_point='[NOT-MOUNTED]'; "
        cmd += "fi; "
        cmd += "echo \"${lvs_line} ${mount_point}\"; "
        cmd += "done"

        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "vg2fact" do
        log_action = Util::LogAction
        preamble = "lv:vg2fact"

        cmd = "facter -p | grep "
        cmd += "$(pvs --options pv_name,vg_name | grep #{request[:vgname]} | awk '{print $1}' | head -1) | "
        cmd += "awk '{print $1'} | egrep '^disk.*_part[23]_dev$' | grep -v '_wmp' | head -1"

        log_action.log(preamble, cmd)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "lsblk" do
        log_action = Util::LogAction

        cmd = "lsblk #{request[:path]} -f -P"

        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "pvresize" do
        log_action = Util::LogAction
        preamble = "lv:pvresize"

        cmd = "/usr/bin/facter -p #{request[:disk_fact_name]}"
        pvname = ""
        run(cmd,
            :stdout => pvname,
            :stderr => :err,
            :chomp => true)

        if pvname.to_s == '' then
          reply[:status] = 1
        else
          cmd = "/sbin/pvresize #{pvname}"

          log_action.log(preamble, cmd)
          reply[:status] = run(cmd,
                               :stdout => :out,
                               :stderr => :err,
                               :chomp => true)
        end
      end

      action "pvs" do
        log_action = Util::LogAction

        cmd = "pvs --noheadings"

        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "vgs" do
        log_action = Util::LogAction

        cmd = "vgs --noheadings"

        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end
    end
  end
end


