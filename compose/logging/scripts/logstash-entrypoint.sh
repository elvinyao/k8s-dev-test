#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /usr/share/logstash
export LOGSTASH_KEYSTORE_PASS
LOGSTASH_KEYSTORE_PASS="$(cat /run/secrets/logstash_keystore_password)"
if [ ! -f config/logstash.keystore ]; then
  bin/logstash-keystore --path.settings config create
else
  # Logstash does not support Kibana's --force flag. Remove only the two
  # derived entries before reloading the same external secret files.
  bin/logstash-keystore --path.settings config remove LOGSTASH_PASSWORD INGEST_PASSWORD
fi
bin/logstash-keystore --path.settings config add LOGSTASH_PASSWORD --stdin < /run/secrets/logstash_password
bin/logstash-keystore --path.settings config add INGEST_PASSWORD --stdin < /run/secrets/ingest_password
exec bin/logstash --path.settings config "$@"
