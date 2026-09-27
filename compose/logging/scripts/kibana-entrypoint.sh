#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /usr/share/kibana
if [ ! -f config/kibana.keystore ]; then
  bin/kibana-keystore create
fi
bin/kibana-keystore add elasticsearch.password --stdin --force < /run/secrets/kibana_password
bin/kibana-keystore add xpack.security.encryptionKey --stdin --force < /run/secrets/kibana_session_key
bin/kibana-keystore add xpack.encryptedSavedObjects.encryptionKey --stdin --force < /run/secrets/kibana_saved_objects_key
bin/kibana-keystore add xpack.reporting.encryptionKey --stdin --force < /run/secrets/kibana_reporting_key
exec bin/kibana "$@"
