---
id: rb-auth-proxy-cert
type: runbook
title: internal-auth-proxy rejects requests with an expired certificate
service: internal-auth-proxy
---

# internal-auth-proxy rejects requests with an expired certificate

internal-auth-proxy sits in front of internal tools and validates the mutual TLS client certificate
that every service presents. Use this runbook when callers get
`x509: certificate has expired or is not yet valid`.

## Which certificate expired

There are three certificates in play and the fix differs for each:

1. **The proxy's own server certificate.** Clients report the error when they connect. Check with
   `openssl s_client -connect internal-auth-proxy:8443 | openssl x509 -noout -dates`.
2. **The client certificate of the calling service.** The proxy logs `client certificate expired`
   with the caller's common name.
3. **The internal CA certificate.** Every caller fails at once. This is rare, the CA is valid for
   five years, and it is a SEV1.

## Renewal

Certificates are issued by cert-manager and are valid for 90 days. They renew automatically 30 days
before expiry. When renewal silently fails, force it:

```
cmctl renew internal-auth-proxy-tls -n platform
kubectl rollout restart deployment/internal-auth-proxy -n platform
```

The restart is required because the proxy reads the certificate only at startup.

## Development environments

Developers who run the proxy locally use a self-signed certificate that expires after 30 days. The
fix is `make dev-certs`, which regenerates the certificate and the local trust bundle. Do not copy
production certificates to a laptop.

## Why the renewal failed

The usual reason is that the cert-manager Issuer lost access to the CA secret after a namespace
cleanup. Check `kubectl describe certificaterequest` for `secret "internal-ca" not found`. Restore
the secret from the sealed backup in the platform repository.
