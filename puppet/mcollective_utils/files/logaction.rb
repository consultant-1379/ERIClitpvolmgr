require 'syslog'

module MCollective
    module Util
        class LogAction
            def self.debug(cmd, request)
                Syslog.open(request.action)
                Syslog.log(Syslog::LOG_DEBUG, "Running command #{cmd}")
                Syslog.close()
            end
            def self.log(log_name, msg)
                Syslog.open(log_name)
                Syslog.log(Syslog::LOG_INFO, msg)
                Syslog.close()
            end
        end
    end
end
