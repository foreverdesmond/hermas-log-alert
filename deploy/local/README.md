# Docker-host deployment

The collector source is in `../../agent`. Build the two local images on the
target Linux, macOS, or Windows Docker host, then deploy `agent/compose.yaml`
through Portainer with the variables from the private `agent/config.env` file.
Windows requires Docker Desktop's Linux-containers mode and a Docker-shared
`LOGS_DIR` path such as `C:/Logs/my-app`.
