All files and directories in the "puppet" directory (except this README.txt)
will be included in the RPM, and will be installed in
/opt/ericsson/nms/litp/etc/puppet/modules.  This behaviour is triggered by
the existence of this README.txt file; deleting or renaming this file will
mean that files in "puppet" are no longer included in the RPM.
