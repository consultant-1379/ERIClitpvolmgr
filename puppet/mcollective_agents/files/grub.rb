module MCollective
  module Agent
    class Grub<RPC::Agent
      begin
        PluginManager.loadclass("MCollective::Util::LogAction")
        log_action = Util::LogAction
      rescue LoadError => e
        raise "Cannot load logaction util: %s" % [e.to_s]
      end

      @@action_label="update_grub"

      action "update_grub" do

        default_grub="/etc/default/grub"
        vxdmp_script="/etc/grub.d/03_vxdmp_config_script"
        rack_grub="/boot/efi/EFI/redhat/grub.cfg"
        grub="/boot/grub2/grub.cfg"
        mk_grub="/usr/sbin/grub2-mkconfig"
        var_tmp="/var/tmp"

        files = {"#{default_grub}" => "#{var_tmp}/grub.bkup",
                 "#{vxdmp_script}" => "#{var_tmp}/03_vxdmp_config_script.bkup",
                 "#{rack_grub}" => "#{var_tmp}/rack_grub.cfg.bkup",
                 "#{grub}" => "#{var_tmp}/grub.cfg.bkup"}

        files.each do |file, backup_file|
          if File.exist?(file)
            result = execute_file_action(file, backup_file, "backup_file")

            if result[:status] != 0
              remove_backups(files)
              reply[:status] = result[:status]
              reply[:err] = result[:err]
              reply[:out] = result[:out]
              return reply
            end
          end
        end

        sed_cmd = "sed -i -E -e \"s:rd\.lvm\.lv=[^ ]*:RD_LVM_ENTRY:g\" -e \"s:(RD_LVM_ENTRY *)+:#{request[:lv_names]} :g\" -e \"s:RD_LVM_ENTRY::g\""

        files.each_key do |file|
          cmd = ""
          if File.exist?(file)
            if file == grub or file == rack_grub
              cmd = "#{mk_grub} -o #{file} 2>&1"
            else
              cmd = "#{sed_cmd} #{file}"
            end
            reply[:status] = run(cmd,
                                 :stdout => :out,
                                 :stderr => :err,
                                 :chomp => true)
            log_action.log("#{@@action_label}", "Ran command: #{cmd}. Result: #{reply[:status]} stdout: #{reply[:out]} stderr: #{reply[:err]}")
            if reply[:status] != 0
              restore_backups(files)
              return reply
            end
          end
        end
        remove_backups(files)
      end

      def restore_backups(files)
        files.each do |file, backup_file|
          if File.exist?(backup_file)
            execute_file_action(file, backup_file, "restore_backup")
          end
        end
      end

      def remove_backups(files)
        files.each do |file, backup_file|
          if File.exist?(backup_file)
            execute_file_action(file, backup_file, "remove_backup")
          end
        end
      end

      def execute_file_action(file, backup_file, action)
        log_action = Util::LogAction
        file_action_result = {:out => "", :err => "", :status => 0}
        cmd = ""
        msg = ""

        case action
        when "backup_file"
          cmd = "/usr/bin/cp -fp #{file} #{backup_file}"
          msg = "Back up file #{file} with command: #{cmd}."
        when "restore_backup"
          cmd = "/usr/bin/mv -f #{backup_file} #{file}"
          msg = "Restore file #{file} with command: #{cmd}."
        when "remove_backup"
          cmd = "/usr/bin/rm -f #{backup_file}"
          msg = "Remove backup file #{backup_file} with command: #{cmd}."
        end

        if !cmd.empty?
          file_action_result[:status] = run(cmd,
                                            :stdout => file_action_result[:out],
                                            :stderr => file_action_result[:err],
                                            :chomp => true)
          log_action.log("#{@@action_label}", "#{msg} Result: #{file_action_result[:status]} stdout: #{file_action_result[:out]} stderr: #{file_action_result[:err]}")

        end
        return file_action_result
      end
    end
  end
end
