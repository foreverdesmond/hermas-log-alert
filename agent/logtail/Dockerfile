FROM golang:1.25-alpine AS build

ARG LOGTAIL_COMMIT=5df3ecb1ddee1c7b0c26c2d020578a58862e99e0
ARG FWATCH_TAG=v1.6.1
RUN apk add --no-cache git patch
WORKDIR /src
RUN git clone https://github.com/vogo/logtail.git . \
    && git checkout "$LOGTAIL_COMMIT"
RUN git clone --branch "$FWATCH_TAG" --depth 1 https://github.com/vogo/fwatch.git /fwatch
COPY patches/ /patches/
COPY fwatch-patches/ /fwatch-patches/
RUN patch -p1 < /patches/0001-tail-from-end-and-gateway-payload.patch \
    && patch -p1 < /patches/0002-case-insensitive-match.patch \
    && patch -p1 < /patches/0003-send-log-file-path.patch \
    && patch -d /fwatch -p1 < /fwatch-patches/0001-reliable-bind-mount-discovery.patch \
    && go mod edit -replace github.com/vogo/fwatch=/fwatch \
    && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/logtail logtail.go

FROM alpine:3.22
RUN apk add --no-cache ca-certificates
COPY --from=build /out/logtail /usr/local/bin/logtail
COPY LICENSES/Apache-2.0.txt /usr/share/licenses/hermes-log-alert/Apache-2.0.txt
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/logtail"]
