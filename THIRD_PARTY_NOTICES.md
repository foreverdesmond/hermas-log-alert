# Third-party notices

This repository is licensed under the Apache License, Version 2.0 in
[`LICENSE`](LICENSE). The Docker collector build also obtains, modifies, and
compiles the following third-party components.

## Logtail

- Project: [vogo/logtail](https://github.com/vogo/logtail)
- Copyright: vogo/logtail contributors
- Source revision: `5df3ecb1ddee1c7b0c26c2d020578a58862e99e0`
- License: Apache License, Version 2.0

The complete Apache-2.0 text is included at
[`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).

Our changes are applied during the Docker build, rather than copying the
upstream source tree into this repository. The patches are retained in
`agent/logtail/patches/` and make these changes:

1. Start tailing at end-of-file and send an event envelope with a source path.
2. Make English matching case-insensitive.
3. Preserve the discovered log-file path when creating dynamic workers.

## Fwatch

- Project: [vogo/fwatch](https://github.com/vogo/fwatch)
- Copyright: vogo/fwatch contributors
- Source revision: `v1.6.1`
- License: Apache License, Version 2.0

The complete Apache-2.0 text is included at
[`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).

The patch in `agent/logtail/fwatch-patches/` adjusts directory discovery for
Docker Desktop bind mounts. It is clearly retained as a separate patch so the
upstream modification is auditable.

## Distribution of images

The Logtail and alert-gateway runtime images include a readable copy of the
Apache-2.0 license at `/usr/share/licenses/hermes-log-alert/Apache-2.0.txt`.
When publishing a derivative image or source archive, retain this notice, the
Apache-2.0 text, and the patch files above.
