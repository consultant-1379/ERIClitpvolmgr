ENV['FACTERLIB'] = "/var/lib/puppet/lib/facter"
require 'facter'
require 'open3'
require 'set'
require 'mcollective/vendor/systemu/lib/systemu'

module MCollective
  module Util
    class VxvmUtils
      begin
        PluginManager.loadclass("MCollective::Util::LogAction")
        @@log_action = Util::LogAction
      rescue LoadError => e
        raise "Cannot load utils: %s" % [e.to_s]
      end

      def self.disk_groups_in_node
        grep = "grep -e \"^vxfs.*\" /proc/modules"
        ret, out, err = systemu(grep)
        if out.nil?
          return []
        end
        cmd = "/opt/VRTS/bin/vxdg list | awk '{print $1}' | sed '1d'"
        ret, out, err = systemu(cmd)
        disk_groups = out.tr("\n",",")
        if !disk_groups.nil?
          disk_groups = disk_groups.split(",")
        else
          disk_groups = []
        end
        disk_groups
      end

       def self.scandisks
         vxscan_cmd = "/sbin/vxdisk scandisks new"
         Open3.popen3(vxscan_cmd)
       end

       def self.import_disk_group disk_group
         # function for plan recovery
         # trying to import disk group
         cmd = "/opt/VRTS/bin/vxdg -f import #{disk_group}"
         begin
           Open3.popen3(cmd)
         rescue
         end
       end

       def self.vx_disk_name(disk_name, scan_disk=false)
         if scan_disk
           VxvmUtils.scandisks
         end
         Facter.reset
         device = Facter.value(disk_name)
         if device.nil?
           raise "VxVM Disk #{disk_name} does not exist"
         end
         device
       end

       def self.snapshot_list
         response = {}
         status = 0
         errors = ""
         disk_groups = VxvmUtils.disk_groups_in_node()
         disk_groups.each do |dg|
           response[dg] = {}
           cmd = "/opt/VRTS/bin/vxsnap -g  #{dg}  list"
           retcode, stdout, stderr = systemu(cmd)

           cmd << " | grep -E '^L_[^ ]+_[^ ]* '"
           cmd << " | awk -F' ' '{print $1}'"
           # We need to use the systemu from the mcollective agent
           # library because this provides more data than the
           # Popen3. We should refactor all calls to popen to this.
           stat, stdout, err = systemu(cmd)
           if retcode.exitstatus != 0
               status = retcode.exitstatus
           end
           if err !=""
               errors << err
           end
           response[dg]['snaps'] = stdout.split("\n")
           cmd = "/opt/VRTS/bin/vxprint -g #{dg} | grep -E '^v  LV[^ ]+_[^ ]* ' | awk -F ' ' '{print $2}'"
           stat, stdout, err = systemu(cmd)
           if stat.exitstatus != 0
               status = stat.exitstatus
           end
           if err !=""
               errors << err
           end
           response[dg]['cv'] = stdout.split("\n")
           cmd = "/opt/VRTS/bin/vxprint -g #{dg} | grep -E '^co LO[^ ]+_[^ ]* ' | awk -F ' ' '{print $2}'"
           stat, stdout, err = systemu(cmd)
           if stat.exitstatus != 0
               status = stat.exitstatus
           end
           if err !=""
               errors << err
           end
           response[dg]['co'] = stdout.split("\n")
         end
         return [response, status, errors]
       end

      def self.delete_disk_file_and_return(status, message, disk_file)
          if File.exist?(disk_file)
              File.delete(disk_file)
          end
          return status, message
      end

      def self.clear_SCSI3_registrations(disk_path, vxvm_disk_name)
        victim_keys = Set.new()
        temp_key = 'LITP_KEY'
        disk_file = '/tmp/disk_path.reg'
        unreg_key = '0,0,0,0,0,0,0,0'
        vxfenadm = '/opt/VRTS/bin/vxfenadm'
        response = {}
        # Save the disk path to temp file we will need it later on for other VxVM commands
        File.open(disk_file, 'w') { |file| file.write(disk_path)}

        # Get all the SCSI-3 registrations victim keys from the disk
        cmd = "#{vxfenadm} -s #{disk_path}"
        @@log_action.log('clear_SCSI3_registrations', "Running command #{cmd}")

        Open3.popen3(cmd){|stdin, stdout, stderr|
          response['out'] = stdout.readlines()
          response['err'] = stderr.readlines()
          if response['err'].any?
            return VxvmUtils.delete_disk_file_and_return(1, "Mcollective call for CMD: '#{cmd}' failed: '#{response['err'].join()}'", disk_file)
          end
        }
        # Add all SCSI-3 victim keys to a set
        response['out'].each do |line|
          if line =~ /Numeric/
            key = line.split(/\s+/)[3]
            victim_keys.add(key)
          end
        end

        if victim_keys.empty?
          return VxvmUtils.delete_disk_file_and_return(0, "No SCSI-3 Registrations on disk: '#{vxvm_disk_name}'", disk_file)
        else

          victim_keys.each do |victim_key|
            # First make sure that we are not the owner of any keys by deleting all
            cmd = "#{vxfenadm} -a -K #{unreg_key} -f #{disk_file}"
            @@log_action.log('clear_SCSI3_registrations', "Running command #{cmd}")

            Open3.popen3(cmd){|stdin, stdout, stderr|
              response['out'] = stdout.readlines()
            }
          end

          # Register a second key "LITP_KEY" temporarily with the disk
          cmd = "#{vxfenadm} -m -k #{temp_key} -f #{disk_file}"
          @@log_action.log('clear_SCSI3_registrations', "Running command #{cmd}")

          Open3.popen3(cmd){|stdin, stdout, stderr|
            response['out'] = stdout.readlines()
            response['err'] = stderr.readlines()
            if response['err'].any?
              return VxvmUtils.delete_disk_file_and_return(2, "Mcollective call for CMD: '#{cmd}' failed: '#{response['err'].join()}'", disk_file)
            end
          }

          victim_keys.each do |victim_key|
            # Remove the victim key from the disk by preempting it with the temporary "LITP_KEY"
            cmd = "#{vxfenadm} -p -k#{temp_key} -f #{disk_file} -V #{victim_key}"
            @@log_action.log('clear_SCSI3_registrations', "Running command #{cmd}")

            Open3.popen3(cmd){|stdin, stdout, stderr|
              response['out'] = stdout.readlines()
            }
          end
          # Remove the temporary key "LITP_KEY" registered earlier
          cmd = "#{vxfenadm} -a -K#{unreg_key} -f #{disk_file}"
          @@log_action.log('clear_SCSI3_registrations', "Running command #{cmd}")

          Open3.popen3(cmd){|stdin, stdout, stderr|
            response['out'] = stdout.readlines()
            response['err'] = stderr.readlines()
            if response['err'].any?
              return VxvmUtils.delete_disk_file_and_return(3, "Mcollective call for CMD: '#{cmd}' failed: '#{response['err'].join()}'", disk_file)
            else
              return VxvmUtils.delete_disk_file_and_return(0, "All SCSI-3 Registrations deleted from disk: '#{vxvm_disk_name}'", disk_file)
            end
          }
        end
      end
      def self.clear_SCSI3_reservations(disk_path, vxvm_disk_name)
        victim_keys = Set.new()
        disk_file = '/tmp/disk_path.res'
        vxfenadm = '/opt/VRTS/bin/vxfenadm'
        response = {}
        # Save the disk path to temp file we will need it later on for other VxVM commands
        File.open(disk_file, 'w') { |file| file.write(disk_path)}

        # Get all the SCSI-3 reservations keys from the disk
        cmd = "#{vxfenadm} -r all -f #{disk_file}"
        @@log_action.log('clear_SCSI3_reservations', "Running command #{cmd}")

        Open3.popen3(cmd){|stdin, stdout, stderr|
          response['out'] = stdout.readlines()
          response['err'] = stderr.readlines()
          if response['err'].any?
            return VxvmUtils.delete_disk_file_and_return(1, "Mcollective call for CMD: '#{cmd}' failed: '#{response['err'].join()}'", disk_file)
          end
        }

        # Add all SCSI-3 victim keys to a set
        response['out'].each do |line|
          if line =~ /Numeric/
            key = line.split(/\s+/)[5]
            victim_keys.add(key)
          end
        end

        if victim_keys.empty?
          return VxvmUtils.delete_disk_file_and_return(0, "No SCSI-3 Reservations on disk: '#{vxvm_disk_name}'", disk_file)
        else
          victim_keys.each do |victim_key|
            # Remove the SCSI-3 reservations keys
            cmd = "#{vxfenadm} -c -K #{victim_key} -f #{disk_file}"
            @@log_action.log('clear_SCSI3_reservations', "Running command #{cmd}")

            Open3.popen3(cmd){|stdin, stdout, stderr|
              response['out'] = stdout.readlines()
            }
          end
          return VxvmUtils.delete_disk_file_and_return(0, "All SCSI-3 Reservations deleted from disk: '#{vxvm_disk_name}'", disk_file)
        end
      end
    end
  end
end
