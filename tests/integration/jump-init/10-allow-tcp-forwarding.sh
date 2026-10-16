#!/usr/bin/with-contenv bash
# The image ships `AllowTcpForwarding no`, which is right for its usual job -
# an sshd you expose to log into - and wrong for the only thing this container
# does. ProxyJump opens the final hop with stdio forwarding (`ssh -W`), and
# without this every `-J` through here fails as "administratively prohibited".
#
# There is no Include directory in the generated config, so this edits it in
# place, before s6 starts sshd.
sed -i 's/^AllowTcpForwarding no/AllowTcpForwarding yes/' /config/sshd/sshd_config
