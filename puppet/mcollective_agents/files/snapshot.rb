require 'syslog'

module MCollective
  module Agent
    class Snapshot<RPC::Agent
      begin
        PluginManager.loadclass("MCollective::Util::LogAction")
        log_action = Util::LogAction
      rescue LoadError => e
        raise "Cannot load logaction util: %s" % [e.to_s]
      end

      action "create" do

        log_action = Util::LogAction

        cmd = "lvcreate -s -L #{request[:size]} -n #{request[:name]} #{request[:path]}"
        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
        reply[:path] = request[:path]
      end

      def remove_snapshot(vg_name, snapshot_name)

        log_action = Util::LogAction
        preamble = "snapshot:remove_snapshot"

        result = {
            :status => 0,
            :out    => "",
            :err    => ""
        }

        cmd = "lvremove -f #{vg_name}/#{snapshot_name}"

        log_action.log(preamble, cmd)

        result[:status] = run(cmd,
                              :stdout => result[:out],
                              :stderr => result[:err],
                              :chomp => true)
        return result
      end

      action "remove" do

        log_action = Util::LogAction
        preamble = "snapshot:remove"

        log_action.debug(preamble, request)

        vg_name         = request[:vg]
        snapshot_name   = request[:name]

        log_action.log(
            preamble, "checking for '#{snapshot_name}' in '#{vg_name}'")

        if snapshot_is_merging(snapshot_name)
            reply[:out]    = ""
            reply[:err]    = "snapshot '#{snapshot_name}' is in a merging state."
            reply[:status] = 1

            log_action.log(preamble, "#{reply[:err]}")
        else
            # if there is no snapshot, return success and let the user know
            if !File.exist?("/dev/#{vg_name}/#{snapshot_name}")

                reply[:out]    = "no snapshots exist, could not find snapshot '#{snapshot_name}'"
                reply[:err]    = ""
                reply[:status] = 0

                log_action.log(preamble, "#{reply[:out]}")

            else

                # otherwise, remove the snapshot and relay the result
                output = remove_snapshot(vg_name, snapshot_name)

                reply[:out]    = output[:out]
                reply[:err]    = output[:err]
                reply[:status] = output[:status]

                log_action.log(
                    preamble,
                    "#{snapshot_name}, status: '#{reply[:status]}', out: '#{reply[:out]}', err: '#{reply[:err]}'")
            end
        end
      end

      def snapshot_is_merging(snapshot_name)

        log_action = Util::LogAction
        preamble = "snapshot:snapshot_is_merging"

        cmd =  "lvs -a --unit b --noheadings "
        cmd += "| grep -w #{snapshot_name} "
        cmd += "| awk '{print $3}' | cut -c 1 | grep -q S"
        log_action.log(preamble, cmd)
        retcode = run(cmd)
        return retcode == 0
      end

      action "is_merging" do

        log_action = Util::LogAction
        preamble = "snapshot:is_merging"

        vg_name         = request[:vg]
        snapshot_name   = request[:name]

        log_action.log(
            preamble, "check '#{snapshot_name}' is not merging in '#{vg_name}'")

        if !snapshot_is_merging(snapshot_name)

            out_msg = "'#{snapshot_name}' snapshot volumes are not merging."

            reply[:status] = 0
            reply[:out]    = out_msg
            reply[:err]    = ""

            log_action.log(preamble, out_msg)
        else

            error_msg = "'#{snapshot_name}' in '#{vg_name}' is merging, can't proceed."

            reply[:status] = 1
            reply[:out]    = ""
            reply[:err]    = error_msg

            log_action.log(preamble, error_msg)
        end
      end

      action "restore" do

        log_action = Util::LogAction
        preamble = "snapshot:restore"

        snapshot_name = request[:name]
        vg = request[:vg]

        if !File.exist?("/dev/#{vg}/#{snapshot_name}") then
          if not snapshot_is_merging(snapshot_name)
            reply[:status] = 404
            reply[:out] = ""
            reply[:err] = "#{snapshot_name} not found"
          end
        else
          if snapshot_is_merging(snapshot_name)
            msg = "Snapshot #{snapshot_name} is already merging. Skipping."
            log_action.log(preamble, msg)
            reply[:out] = msg
            reply[:status] = 0
          else
            cmd = "lvconvert --merge #{vg}/#{snapshot_name}"
            log_action.debug(cmd, request)
            reply[:status] = run(cmd,
                                 :stdout => :out,
                                 :stderr => :err,
                                 :chomp => true)
          end
        end
      end

      action "save_grub" do
        log_action = Util::LogAction

        cmd = "cp -f /boot/grub2/grub.cfg /boot/grub2/grub.cfg.backup"
        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "remove_grub" do
        log_action = Util::LogAction

        cmd = "rm -f /boot/grub2/grub.cfg.backup"
        log_action.debug(cmd, request)
        reply[:status] = run(cmd,
                             :stdout => :out,
                             :stderr => :err,
                             :chomp => true)
      end

      action "restore_grub" do
        log_action = Util::LogAction

        if File.exist?("/boot/grub2/grub.cfg.backup") then
          cmd = "mv -f /boot/grub2/grub.cfg.backup /boot/grub2/grub.cfg"
          log_action.debug(cmd, request)
          reply[:status] = run(cmd,
                               :stdout => :out,
                               :stderr => :err,
                               :chomp => true)
        else
          reply[:status] = 2
          reply[:out] = ""
          reply[:err] = "/boot/grub2/grub.cfg.backup not found"
        end
      end
    end
  end
end
