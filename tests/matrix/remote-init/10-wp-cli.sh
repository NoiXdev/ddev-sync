#!/bin/sh
# Runs as root when the stand-in production host starts, after the packages
# the cell asked for (`Cell.remote_packages`) are installed. A cell that did not
# ask for PHP gets no wp-cli, which is the case for every profile except
# WordPress: its `wp_cli` credentials source runs `wp config get` on the
# production host over ssh.
set -eu
command -v php83 > /dev/null || exit 0

# The phar is fetched with PHP itself because that is already here (a package
# the cell asked for) and the sshd image's own tools are not guaranteed to speak
# https. Alpine installs the interpreter as `php83`, not `php`, which the phar's
# `#!/usr/bin/env php` line would need, so `wp` is a two-line wrapper.
php83 -r 'copy("https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar", "/usr/local/lib/wp-cli.phar");'
printf '#!/bin/sh\nexec php83 /usr/local/lib/wp-cli.phar "$@"\n' > /usr/local/bin/wp
chmod 755 /usr/local/bin/wp
