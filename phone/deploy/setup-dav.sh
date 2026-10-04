#!/bin/bash
# 建托管同步用的目录,并让 nginx(www-data)和后端(当前用户)都能读写。用 sudo 跑一次。
set -e
ME=${SUDO_USER:-$USER}
mkdir -p /srv/udav /srv/udav-auth
chown www-data:www-data /srv/udav && chmod 2775 /srv/udav
setfacl -m u:$ME:rwx,u:www-data:rwx /srv/udav
setfacl -d -m u:$ME:rwx,u:www-data:rwx /srv/udav
chown $ME:www-data /srv/udav-auth && chmod 750 /srv/udav-auth
echo "ok: /srv/udav /srv/udav-auth ready for $ME + www-data"
