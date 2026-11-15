# Optional Command Code sentiment fallback

This potentially paid fallback is disabled by default and requires a primary sentiment
provider, `COMMANDCODE_ENABLED=true`, a nonempty `COMMANDCODE_API_KEY` and an explicitly
chosen, nonempty `COMMANDCODE_MODEL`. A direct probe using `reflection/beam:free` returned
HTTP 200 in 0.98 seconds with a valid JSON score. The model is promotional free, subject
to capacity; the successful probe does not guarantee stable availability, future pricing
or capacity. Verify compatibility and cost before opting in. It is used only after other
providers fail, not for general overseer chat.
The 50-second sentiment chain reserves its final ten seconds for Command Code when
available; earlier providers may be interrupted.

## Future rollout, only after explicit approval

Keep Command Code settings **out of the shared `.env`**. Provision
`/etc/crypto-agent/commandcode.env` outside the repository, owned by the deploy user
(root for the GitHub Actions SSH workflow), mode 0600, containing:

```dotenv
COMMANDCODE_ENABLED=true
COMMANDCODE_API_KEY=<secret>
COMMANDCODE_MODEL=<verified-model-id>
```

Make a separate host-local file `/etc/crypto-agent/commandcode.deploy` containing
exactly one line `enabled`. It is data, not shell, and contains no secrets. The regular
`scripts/rebuild_prod_agents.sh` (including the GitHub Actions SSH workflow) reads this
marker on every deploy and consistently uses `docker-compose.commandcode.yml` after
`docker-compose.prod.yml`. With the marker absent the existing base-only deploy is
unchanged. An invalid marker, missing/unreadable/non-0600 or incorrectly owned env
file, symlink, or missing overlay fails the rebuild before any Docker call. Compose
also validates env-file syntax when enumerating services before the build. The rebuild
compares base and merged service sets and validates the resolved enable flag, key and
model without printing them; missing or invalid values abort before build/up. The marker
and env file paths may be overridden with `COMMANDCODE_DEPLOY_CONFIG` and
`COMMANDCODE_ENV_FILE` for isolated testing; the workflow normally uses defaults.
Do not put these variables into the shared .env. Protect the marker against
unauthorized modification too.

The overlay changes only `agent_sentiment_macro` and introduces no services; the
base's monitoring services remain declared when `--remove-orphans` runs. Other
unlisted third-party overlay services would still be orphans under the existing deploy
behavior. Do not use the overlay with the development compose file.

This is **environment scoping, not filesystem isolation**: only the sentiment service
receives this env file, but Compose/Docker administrators can inspect container
configuration and the host file. Production `node_exporter` mounts host `/` read-only
and may be able to read the file depending on its UID and host permissions; any
privileged container or host-root user can also read it. Container environment and
Docker metadata can retain the key after file deletion. Protect backups and logs, and
never print merged Compose environments. To rotate a key, update the restricted file
and explicitly recreate the sentiment service through an approved deployment; remove
old containers/metadata as appropriate and revoke the old key at the provider. On
normal shutdown, provider transports are closed after background tasks stop; abnormal
startup or forced termination is not guaranteed to run this cleanup.

Rollback requires removing the marker and performing an approved base-only rebuild
which recreates the sentiment service without the overlay; then revoke the key and
remove the env file and any retained copies as appropriate. Removing the file or marker
alone does not erase the environment of an existing container. This document and
uncommitted overlay do not modify a running deployment.
