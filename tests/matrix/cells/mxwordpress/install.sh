# A real WordPress, installed by wp-cli (which the web image provides). The
# table prefix is deliberately not the default `wp_`: the point of
# `${WP_TABLE_PREFIX}` is that a site may choose its own, and a profile that
# quietly assumed `wp_users` would pass on a default install and nowhere else.
wp core download
wp config create \
  --dbname=db --dbuser=db --dbpass=db --dbhost=ddev-mxwordpress-db \
  --dbprefix=mxwp_
wp core install \
  --url=https://mxwordpress.ddev.site \
  --title=matrix \
  --admin_user=installer \
  --admin_password=Password.1234 \
  --admin_email=admin@example.invalid \
  --skip-email

# A persistent object cache, so that `wp cache flush` - the profile's only
# setup command, which it allows to fail - has something to flush. Without
# one WordPress keeps its cache in the memory of the very process that
# flushes it, and the command has no effect anyone could observe. Real sites
# get theirs from a drop-in such as Redis Object Cache; this one keeps each
# entry as a file under wp-content/matrix-cache/, which the test can look at.
# It is a stand-in for the cache backend, not for wp-cli: the command under
# test is still the real one.
cat > wp-content/object-cache.php <<'PHP'
<?php
function matrix_cache_file($key, $group)
{
    return WP_CONTENT_DIR . '/matrix-cache/' . md5(($group ?: 'default') . ':' . $key);
}
function wp_cache_init()
{
    $GLOBALS['wp_object_cache'] = new stdClass();
    is_dir(WP_CONTENT_DIR . '/matrix-cache') || mkdir(WP_CONTENT_DIR . '/matrix-cache', 0777, true);
}
function wp_cache_set($key, $data, $group = '', $expire = 0)
{
    return false !== file_put_contents(matrix_cache_file($key, $group), serialize($data));
}
function wp_cache_add($key, $data, $group = '', $expire = 0)
{
    return !is_file(matrix_cache_file($key, $group)) && wp_cache_set($key, $data, $group, $expire);
}
function wp_cache_replace($key, $data, $group = '', $expire = 0)
{
    return is_file(matrix_cache_file($key, $group)) && wp_cache_set($key, $data, $group, $expire);
}
function wp_cache_get($key, $group = '', $force = false, &$found = null)
{
    $file = matrix_cache_file($key, $group);
    $found = is_file($file);
    return $found ? unserialize(file_get_contents($file)) : false;
}
function wp_cache_delete($key, $group = '')
{
    $file = matrix_cache_file($key, $group);
    return is_file($file) && unlink($file);
}
function wp_cache_incr($key, $offset = 1, $group = '')
{
    $value = wp_cache_get($key, $group, false, $found);
    if (!$found) {
        return false;
    }
    $value = max(0, (int) $value + $offset);
    wp_cache_set($key, $value, $group);
    return $value;
}
function wp_cache_decr($key, $offset = 1, $group = '')
{
    return wp_cache_incr($key, -$offset, $group);
}
function wp_cache_flush()
{
    array_map('unlink', glob(WP_CONTENT_DIR . '/matrix-cache/*') ?: []);
    return true;
}
function wp_cache_get_multiple($keys, $group = '', $force = false)
{
    return array_combine($keys, array_map(fn ($key) => wp_cache_get($key, $group), $keys));
}
function wp_cache_set_multiple($data, $group = '', $expire = 0)
{
    return array_map(fn ($key) => wp_cache_set($key, $data[$key], $group, $expire), array_combine(array_keys($data), array_keys($data)));
}
function wp_cache_add_multiple($data, $group = '', $expire = 0)
{
    return array_map(fn ($key) => wp_cache_add($key, $data[$key], $group, $expire), array_combine(array_keys($data), array_keys($data)));
}
function wp_cache_delete_multiple($keys, $group = '')
{
    return array_combine($keys, array_map(fn ($key) => wp_cache_delete($key, $group), $keys));
}
function wp_cache_flush_group($group) { return false; }
function wp_cache_flush_runtime() { return true; }
function wp_cache_supports($feature) { return false; }
function wp_cache_close() { return true; }
function wp_cache_add_global_groups($groups) {}
function wp_cache_add_non_persistent_groups($groups) {}
function wp_cache_switch_to_blog($blog_id) {}
PHP
# Fails the install, rather than printing, when WordPress did not pick it up.
wp eval 'exit(wp_using_ext_object_cache() ? 0 : 1);'

wp core version
wp --version
php -v | head -1
