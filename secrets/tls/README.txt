Place production origin TLS files here:

  origin.crt
  origin.key

Use scripts/install-origin-certificate.sh rather than copying them manually.
The real certificate and key are ignored by Git and Docker build context.
