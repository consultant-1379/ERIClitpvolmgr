# Make sure the whole path for the mounting point exists,
# creating, otherwise.
define volmgr::create_mount_path ($mount_point) {

  exec { "Creating ${mount_point}":
    command => "mkdir -m 755 -p '${mount_point}'",
    creates => $mount_point,
    path    => ['/bin', '/usr/bin', '/usr/sbin', '/sbin'],
  }

}
