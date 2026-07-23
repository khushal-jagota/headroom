# Production deployment

On the single-user Mac, the self-hosted runner and the Panels LaunchAgent run as the signed-in
operator. Releases, the `current` pointer, persistent state, logs, and deployment records live under
`~/Library/Application Support/Panels`. Production uses the operator's existing Hermes home and
provider credentials. Ordinary deployment does not require root access or a separate service user.

An existing live database must have an operator-established baseline release manifest supplied
to the first deployment. The deploy command backs up that database under the baseline SHA before
it changes `current`. An explicitly absent or new database may bootstrap without that backup.
