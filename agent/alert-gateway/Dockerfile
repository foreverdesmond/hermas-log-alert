FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY . .
RUN go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/alert-gateway .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates
COPY --from=build /out/alert-gateway /app/alert-gateway
COPY LICENSES/Apache-2.0.txt /usr/share/licenses/hermas-log-alert/Apache-2.0.txt
USER 65532:65532
ENTRYPOINT ["/app/alert-gateway"]
