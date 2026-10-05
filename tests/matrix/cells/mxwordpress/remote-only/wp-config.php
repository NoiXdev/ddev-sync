<?php
/**
 * The production host's wp-config.php, as `wp config get` reads it. Credentials
 * are the stand-in production database's (see docker-compose.yaml). The table
 * prefix is the one thing the cell cares about: the engine reads it from here
 * over ssh for `${WP_TABLE_PREFIX}`, and it differs from the local install's
 * only in being a deliberate choice: `mxwp_`, WordPress's default being `wp_`.
 *
 * The last line is not decoration: wp-cli refuses a wp-config.php that does
 * not load wp-settings.php ("Strange wp-config.php file").
 */

define( 'DB_NAME', 'acme_prod' );
define( 'DB_USER', 'acme' );
define( 'DB_PASSWORD', 's3cret' );
define( 'DB_HOST', 'remote-db' );
define( 'DB_CHARSET', 'utf8' );
define( 'DB_COLLATE', '' );

$table_prefix = 'mxwp_';

define( 'WP_DEBUG', false );

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}

require_once ABSPATH . 'wp-settings.php';
