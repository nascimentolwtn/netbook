"""Tests for the signature-verification hardening in ../app.py
(docs/plans/0001-signature-verification-root-ca-decision.md, option c).

stdlib unittest only, no network. Fixtures are built at test time with
cryptography's CertificateBuilder, using only APIs available in the apt
package `python3-cryptography` 2.1.4 that the netbook relay venv actually
runs (ADR 0007) -- run this suite on the netbook in that venv before
trusting it, not just in a newer local interpreter.

Run from relay/: `python3 -m unittest tests.test_signature -v`
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import app as relay_app


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
def _key():
    # `backend` is required (not optional) on cryptography 2.1.4 (ADR 0007,
    # the netbook's apt-installed version) even though newer releases infer it.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _epoch(dt):
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())


def _build_cert(
    subject_cn,
    issuer_name,
    issuer_key,
    subject_key,
    is_ca=False,
    san=None,
    not_before=None,
    not_after=None,
):
    now = datetime.datetime.utcnow()
    not_before = not_before or (now - datetime.timedelta(days=1))
    not_after = not_after or (now + datetime.timedelta(days=30))
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject_cn))
        .issuer_name(issuer_name)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False
        )
    return builder.sign(issuer_key, hashes.SHA256(), default_backend())


def _write_pem(path, *certs):
    with open(path, "wb") as f:
        for cert in certs:
            f.write(cert.public_bytes(serialization.Encoding.PEM))


def _make_chain(root_cn="Test Root", not_before=None, not_after=None):
    """A normal root -> intermediate -> leaf chain, leaf SAN'd for
    echo-api.amazon.com. Returns (root_cert, root_key, intermediate_cert, leaf_cert)."""
    root_key = _key()
    root_cert = _build_cert(root_cn, _name(root_cn), root_key, root_key, is_ca=True)

    intermediate_key = _key()
    intermediate_cert = _build_cert(
        "Test Intermediate",
        root_cert.subject,
        root_key,
        intermediate_key,
        is_ca=True,
        not_before=not_before,
        not_after=not_after,
    )

    leaf_key = _key()
    leaf_cert = _build_cert(
        "echo-api.amazon.com",
        intermediate_cert.subject,
        intermediate_key,
        leaf_key,
        is_ca=False,
        san="echo-api.amazon.com",
        not_before=not_before,
        not_after=not_after,
    )
    return root_cert, root_key, intermediate_cert, leaf_cert


class TempCAFile(object):
    """Writes one or more certs to a throwaway CA bundle file."""

    def __init__(self, *certs):
        self._certs = certs
        self._tmpdir = None
        self.path = None

    def __enter__(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmpdir.name, "ca.pem")
        _write_pem(self.path, *self._certs)
        return self.path

    def __exit__(self, *exc):
        self._tmpdir.cleanup()


# ---------------------------------------------------------------------------
# 1-8: _verify_chain_anchor (c2)
# ---------------------------------------------------------------------------
class VerifyChainAnchorTests(unittest.TestCase):
    def test_1_good_chain_passes(self):
        root, _, intermediate, leaf = _make_chain()
        with TempCAFile(root) as ca_file:
            relay_app._verify_chain_anchor([leaf, intermediate], ca_file=ca_file)

    def test_2_forged_chain_rejected(self):
        # Internally consistent, valid dates, correct SAN -- but its root
        # is not the one in ca_file. This is the exact attack the old
        # (pre-anchor) checks accepted.
        trusted_root, _, _, _ = _make_chain(root_cn="Trusted Root")
        _, _, forged_intermediate, forged_leaf = _make_chain(root_cn="Attacker Root")
        with TempCAFile(trusted_root) as ca_file:
            with self.assertRaises(relay_app.SignatureVerificationError):
                relay_app._verify_chain_anchor([forged_leaf, forged_intermediate], ca_file=ca_file)

    def test_3_forged_root_included_in_chain_still_rejected(self):
        trusted_root, _, _, _ = _make_chain(root_cn="Trusted Root")
        forged_root, _, forged_intermediate, forged_leaf = _make_chain(root_cn="Attacker Root")
        with TempCAFile(trusted_root) as ca_file:
            with self.assertRaises(relay_app.SignatureVerificationError):
                relay_app._verify_chain_anchor(
                    [forged_leaf, forged_intermediate, forged_root], ca_file=ca_file
                )

    def test_4_missing_intermediate_rejected(self):
        root, _, _intermediate, leaf = _make_chain()
        with TempCAFile(root) as ca_file:
            with self.assertRaises(relay_app.SignatureVerificationError):
                relay_app._verify_chain_anchor([leaf], ca_file=ca_file)

    def test_5_non_ca_intermediate_rejected(self):
        root_key = _key()
        root_cert = _build_cert("Test Root", _name("Test Root"), root_key, root_key, is_ca=True)
        # Intermediate deliberately issued WITHOUT BasicConstraints(ca=True).
        bad_intermediate_key = _key()
        bad_intermediate_cert = _build_cert(
            "Bad Intermediate", root_cert.subject, root_key, bad_intermediate_key, is_ca=False
        )
        leaf_key = _key()
        leaf_cert = _build_cert(
            "echo-api.amazon.com",
            bad_intermediate_cert.subject,
            bad_intermediate_key,
            leaf_key,
            is_ca=False,
            san="echo-api.amazon.com",
        )
        with TempCAFile(root_cert) as ca_file:
            with self.assertRaises(relay_app.SignatureVerificationError):
                relay_app._verify_chain_anchor([leaf_cert, bad_intermediate_cert], ca_file=ca_file)

    def test_6_expired_intermediate_rejected_via_at_time(self):
        now = datetime.datetime.utcnow()
        not_before = now - datetime.timedelta(days=2)
        not_after = now + datetime.timedelta(days=10)
        root, _, intermediate, leaf = _make_chain(not_before=not_before, not_after=not_after)
        future = _epoch(now + datetime.timedelta(days=20))  # past not_after
        with TempCAFile(root) as ca_file:
            with self.assertRaises(relay_app.SignatureVerificationError):
                relay_app._verify_chain_anchor(
                    [leaf, intermediate], ca_file=ca_file, at_time=future
                )

    def test_7_openssl_missing_fails_closed(self):
        root, _, intermediate, leaf = _make_chain()
        with TempCAFile(root) as ca_file:
            with mock.patch.object(relay_app, "OPENSSL_BIN", "/nonexistent/openssl-binary"):
                with self.assertRaises(relay_app.SignatureVerificationError):
                    relay_app._verify_chain_anchor([leaf, intermediate], ca_file=ca_file)

    def test_8_openssl_exit_code_contract_on_failure(self):
        # Pins the raw CLI contract this box's openssl is assumed to have:
        # non-zero exit and no trailing ": OK" on a failing verification.
        trusted_root, _, _, _ = _make_chain(root_cn="Trusted Root")
        _, _, forged_intermediate, forged_leaf = _make_chain(root_cn="Attacker Root")
        with TempCAFile(trusted_root) as ca_file, tempfile.TemporaryDirectory() as tmpdir:
            leaf_path = os.path.join(tmpdir, "leaf.pem")
            untrusted_path = os.path.join(tmpdir, "untrusted.pem")
            _write_pem(leaf_path, forged_leaf)
            _write_pem(untrusted_path, forged_intermediate)
            import subprocess

            result = subprocess.run(
                [
                    "openssl",
                    "verify",
                    "-no-CApath",
                    "-CAfile",
                    ca_file,
                    "-untrusted",
                    untrusted_path,
                    leaf_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(result.stdout.decode("utf-8", errors="replace").rstrip().endswith(": OK"))


# ---------------------------------------------------------------------------
# URL hardening (c1): _validate_cert_chain_url
# ---------------------------------------------------------------------------
class ValidateCertChainUrlTests(unittest.TestCase):
    REJECTED = [
        "https://s3.amazonaws.com/echo.api/%2e%2e/evilbucket/c.pem",
        "https://s3.amazonaws.com/echo.api/%2E%2E/evilbucket/c.pem",
        "https://s3.amazonaws.com/echo.api/../x",
        "https://s3.amazonaws.com/echo.api/./../x",
        "https://s3.amazonaws.com/echo.api/%2Fx",
        "http://s3.amazonaws.com/echo.api/x",
        "https://s3.amazonaws.com:8443/echo.api/x",
        "https://s3.amazonaws.com/Echo.api/x",
        "https://s3.amazonaws.com@evil.com/echo.api/x",
    ]

    ACCEPTED = [
        "https://s3.amazonaws.com/echo.api/echo-api-cert-4.pem",
        "https://S3.AMAZONAWS.COM:443/echo.api/echo-api-cert-4.pem",
    ]

    def test_rejected_urls(self):
        for url in self.REJECTED:
            with self.subTest(url=url):
                with self.assertRaises(relay_app.SignatureVerificationError):
                    relay_app._validate_cert_chain_url(url)

    def test_accepted_urls(self):
        for url in self.ACCEPTED:
            with self.subTest(url=url):
                relay_app._validate_cert_chain_url(url)  # must not raise


# ---------------------------------------------------------------------------
# _get_cert_chain: redirects are rejected, not followed
# ---------------------------------------------------------------------------
class GetCertChainRedirectTests(unittest.TestCase):
    def setUp(self):
        relay_app._cert_chain_cache.clear()

    def test_redirect_is_rejected(self):
        fake_resp = mock.Mock()
        fake_resp.status_code = 301
        with mock.patch.object(relay_app.requests, "get", return_value=fake_resp) as get:
            with self.assertRaises(relay_app.SignatureVerificationError):
                relay_app._get_cert_chain("https://s3.amazonaws.com/echo.api/echo-api-cert-4.pem")
            _, kwargs = get.call_args
            self.assertFalse(kwargs.get("allow_redirects", True))


if __name__ == "__main__":
    unittest.main()
