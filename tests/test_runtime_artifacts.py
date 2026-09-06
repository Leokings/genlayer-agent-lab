"""Artifact TLS tests use local ephemeral CAs; no host certificate stores change."""

import hashlib
import http.server
import ssl
import threading
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from genlayer_agent_lab.runtime import artifacts

BUNDLE = b"small pinned test archive"


def certificate(name, *, issuer=None, hostname="localhost", expired=False):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer_key, issuer_cert = issuer if issuer else (key, None)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder().subject_name(subject)
        .issuer_name(issuer_cert.subject if issuer_cert else subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if expired else 1))
        .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=issuer is None,
            crl_sign=issuer is None, encipher_only=None, decipher_only=None), critical=True)
    )
    if issuer:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    return key, builder.sign(issuer_key, hashes.SHA256())


@contextmanager
def https_bundle(tmp_path, issuer, payload=BUNDLE, *, hostname="localhost", expired=False):
    key, cert = certificate("test endpoint", issuer=issuer, hostname=hostname, expired=expired)
    key_file, cert_file = tmp_path / "key.pem", tmp_path / "server.pem"
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}/bundle"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.fixture
def download_environment(tmp_path, monkeypatch):
    from gltest.direct import sdk_loader

    system_ca = certificate("existing enterprise CA")
    public_ca = certificate("additional public CA")
    public_roots = tmp_path / "public-roots.pem"
    public_roots.write_bytes(public_ca[1].public_bytes(serialization.Encoding.PEM))
    contexts, setups = [], []
    default_flags = ssl.create_default_context().verify_flags

    def system_default_context():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_flags = default_flags
        context.load_verify_locations(cadata=system_ca[1].public_bytes(serialization.Encoding.PEM).decode())
        contexts.append(context)
        return context

    def setup(contract_path, *, version):
        setups.append(version)
        return []

    monkeypatch.setattr(artifacts.ssl, "create_default_context", system_default_context)
    monkeypatch.setattr(artifacts.certifi, "where", lambda: str(public_roots))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "profile"))
    monkeypatch.setattr(artifacts, "BUNDLE_SIZE", len(BUNDLE))
    monkeypatch.setattr(artifacts, "BUNDLE_SHA256", hashlib.sha256(BUNDLE).hexdigest())
    # Both globals can be changed by prepare_sdk; restore them after each test.
    monkeypatch.setattr(sdk_loader, "CACHE_DIR", tmp_path / "unused")
    monkeypatch.setattr(sdk_loader, "setup_sdk_paths", setup)
    cache = tmp_path / "profile/.cache/genlayer-agent-lab/gltest-direct"
    archive = cache / f"genvm-universal-{artifacts.GENVM_VERSION}.tar.xz"
    return {"system_ca": system_ca, "public_ca": public_ca, "contexts": contexts,
            "default_flags": default_flags, "setups": setups, "archive": archive}


@pytest.mark.parametrize("signer", ["system_ca", "public_ca"])
def test_download_trusts_existing_system_and_additional_public_roots(
        tmp_path, monkeypatch, download_environment, signer):
    environment = download_environment
    with https_bundle(tmp_path, environment[signer]) as url:
        monkeypatch.setattr(artifacts, "BUNDLE_URL", url)
        artifacts.prepare_sdk(tmp_path / "contract.py")
    assert environment["archive"].read_bytes() == BUNDLE
    assert environment["setups"] == [artifacts.GENVM_VERSION]
    assert len(environment["contexts"]) == 1
    context = environment["contexts"][0]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.verify_flags == environment["default_flags"]
    assert set(context.get_ca_certs(binary_form=True)) == {
        environment[ca][1].public_bytes(serialization.Encoding.DER)
        for ca in ("system_ca", "public_ca")
    }


@pytest.mark.parametrize("failure", ["unknown_issuer", "wrong_hostname", "expired"])
def test_invalid_tls_never_downloads_or_prepares_sdk(
        tmp_path, monkeypatch, download_environment, failure):
    environment = download_environment
    issuer = certificate("unknown CA") if failure == "unknown_issuer" else environment["public_ca"]
    with https_bundle(tmp_path, issuer, hostname="wrong.example" if failure == "wrong_hostname" else "localhost",
                      expired=failure == "expired") as url:
        monkeypatch.setattr(artifacts, "BUNDLE_URL", url)
        with pytest.raises(urllib.error.URLError) as caught:
            artifacts.prepare_sdk(tmp_path / "contract.py")
    assert isinstance(caught.value.reason, ssl.SSLCertVerificationError)
    assert not environment["archive"].exists()
    assert not environment["archive"].with_suffix(".download").exists()
    assert environment["setups"] == []


@pytest.mark.parametrize("payload", [b"x" * len(BUNDLE), BUNDLE + b"extra"])
def test_verified_tls_does_not_bypass_pinned_digest_or_size(
        tmp_path, monkeypatch, download_environment, payload):
    environment = download_environment
    with https_bundle(tmp_path, environment["public_ca"], payload) as url:
        monkeypatch.setattr(artifacts, "BUNDLE_URL", url)
        with pytest.raises(RuntimeError, match="Pinned GenVM bundle checksum mismatch"):
            artifacts.prepare_sdk(tmp_path / "contract.py")
    assert not environment["archive"].exists()
    assert not environment["archive"].with_suffix(".download").exists()
    assert environment["setups"] == []
