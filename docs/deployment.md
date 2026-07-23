# Production deployment

The self-hosted production runner runs as `panels-deploy`, never as root. That identity owns
the release directories, `current` pointer, deployment records, and deployment lock. The
`panels-live` service may traverse, read, and execute releases but has no write access to them.

An existing live database must have an operator-established baseline release manifest supplied
to the first deployment. The deploy command backs up that database under the baseline SHA before
it changes `current`. An explicitly absent or new database may bootstrap without that backup.
