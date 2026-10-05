# Executed only with the embedded Ruby and libraries of the pinned GitLab image.
require 'json'
require 'digest'
require 'ripper'
require 'chef'

cookbooks = '/opt/gitlab/embedded/cookbooks'
require "#{cookbooks}/package/libraries/config/gitlab"
require "#{cookbooks}/package/libraries/deprecations"

input = '/validation/omnibus.rb'
source = File.read(input)
raise 'Ruby syntax is invalid' unless Ripper.sexp(source)

# The real DSL permits arbitrary nested keys. Additionally compare every key in
# this small example with the template shipped in this exact package, so a typo
# does not silently pass the loader.
template_path = '/opt/gitlab/etc/gitlab.rb.template'
template = File.read(template_path)
normalize = ->(value) { value.gsub('"', "'") }
keys = source.scan(/^\s*([a-z_]+(?:\[['"][a-z_0-9]+['"]\])+)[ \t]*=/).flatten
raise 'No configuration keys found' if keys.empty?
keys.each do |key|
  pattern = /^\s*#?\s*#{Regexp.escape(normalize.call(key))}\s*=/
  raise "Option is absent from this image's template: #{key}" unless normalize.call(template).match?(pattern)
end

JSON.parse(File.read('/validation/environment.json')).each { |key, value| ENV[key] = value }
Gitlab.from_file(input)
# Loading the actual DSL evaluates the example's Integer, split/map and File.read
# expressions without running Chef recipes, reconfigure, migrations or services.
rails = Gitlab['gitlab_rails']
expected = {
  'gitlab_shell_ssh_port' => 2222,
  'trusted_proxies' => ['192.0.2.10/32'],
  'initial_root_password' => 'public-validation-fixture-never-deploy',
  'backup_path' => '/var/opt/gitlab/backups',
  'backup_keep_time' => 604800
}
expected.each { |key, value| raise "Unexpected resolved #{key}" unless rails[key] == value }
nginx = {
  'listen_port' => 80, 'listen_https' => false, 'redirect_http_to_https' => false,
  'real_ip_trusted_addresses' => ['192.0.2.10/32'],
  'real_ip_header' => 'X-Forwarded-For', 'real_ip_recursive' => 'on'
}
nginx.each { |key, value| raise "Unexpected resolved NGINX #{key}" unless rails['nginx'][key] == value }
raise 'Unexpected external URL' unless Gitlab['external_url'] == 'https://gitlab.example.invalid'
raise 'Bundled LetsEncrypt must be disabled' unless Gitlab['letsencrypt']['enable'] == false
raise 'Packaged healthcheck is missing' unless File.executable?('/opt/gitlab/bin/gitlab-healthcheck')

puts JSON.generate({
  status: 'passed', parser: 'Gitlab.from_file (packaged SettingsDSL)',
  rubyVersion: RUBY_VERSION, checkedOptions: keys.length,
  templateSha256: Digest::SHA256.file(template_path).hexdigest,
  versionManifestSha256: Digest::SHA256.file('/opt/gitlab/version-manifest.txt').hexdigest,
  scope: 'Ruby syntax, supported example keys and evaluated settings; no reconfigure or GitLab service startup'
})
