from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from compliance.tsa import TimestampAuthority, TimestampResponse


class TestTimestampAuthorityInit:
    def test_empty_url_rejected(self):
        with pytest.raises(ValueError, match="tsa_url is required"):
            TimestampAuthority(tsa_url="")

    def test_url_stored(self):
        with patch("compliance.tsa.rfc3161ng.RemoteTimestamper"):
            tsa = TimestampAuthority(tsa_url="https://tsa.example.com")
            assert tsa.tsa_url == "https://tsa.example.com"


class TestTimestampResponse:
    def test_frozen(self):
        tr = TimestampResponse(
            token=b"\x00",
            tst_info_serial=1,
            tst_info_time=datetime(2024, 1, 1),
            tsa_url="https://tsa.example.com",
        )
        with pytest.raises(AttributeError):
            tr.token = b"\x01"


class TestTimestamp:
    @patch("compliance.tsa.decoder.decode")
    @patch("compliance.tsa.encoder.encode", return_value=b"\x30\x00")
    @patch("compliance.tsa.rfc3161ng")
    def test_timestamp_returns_response(self, mock_rfc, _mock_encode, mock_decode):
        mock_tst = MagicMock()
        mock_tsr = MagicMock()
        mock_tsr.time_stamp_token = mock_tst

        mock_stamper = MagicMock()
        mock_stamper.return_value = mock_tsr
        mock_rfc.RemoteTimestamper.return_value = mock_stamper
        mock_rfc.get_timestamp.return_value = datetime(2024, 6, 15, 12, 0, 0)
        mock_rfc.TSTInfo.return_value = MagicMock()

        mock_tstinfo_obj = MagicMock()
        mock_tstinfo_obj.getComponentByName.return_value = 42
        mock_decode.side_effect = [
            (b"\x30\x00", b""),
            (mock_tstinfo_obj, b""),
        ]

        tsa = TimestampAuthority(tsa_url="https://tsa.example.com")
        digest = hashlib.sha256(b"test data").digest()
        resp = tsa.timestamp(digest)

        assert isinstance(resp, TimestampResponse)
        assert resp.tsa_url == "https://tsa.example.com"
        assert resp.tst_info_serial == 42
        assert resp.tst_info_time == datetime(2024, 6, 15, 12, 0, 0)


class TestVerify:
    @patch("compliance.tsa.rfc3161ng")
    def test_verify_delegates_to_check_timestamp(self, mock_rfc):
        mock_rfc.check_timestamp.return_value = True
        mock_rfc.RemoteTimestamper.return_value = MagicMock()

        tsa = TimestampAuthority(tsa_url="https://tsa.example.com")
        digest = hashlib.sha256(b"hello").digest()
        result = tsa.verify(digest, b"\x30\x00")

        assert result is True
        mock_rfc.check_timestamp.assert_called_once_with(
            b"\x30\x00",
            digest=digest,
            hashname="sha256",
            certificate=None,
        )

    @patch("compliance.tsa.rfc3161ng")
    def test_verify_with_certificate(self, mock_rfc):
        cert = b"-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----"
        mock_rfc.check_timestamp.return_value = True
        mock_rfc.RemoteTimestamper.return_value = MagicMock()

        tsa = TimestampAuthority(
            tsa_url="https://tsa.example.com",
            certificate=cert,
        )
        digest = hashlib.sha256(b"hello").digest()
        tsa.verify(digest, b"\x30\x00")

        mock_rfc.check_timestamp.assert_called_once_with(
            b"\x30\x00",
            digest=digest,
            hashname="sha256",
            certificate=cert,
        )
