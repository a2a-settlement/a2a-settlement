from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pyasn1.codec.der import decoder, encoder
from pyasn1.type import univ

import rfc3161ng


@dataclass(frozen=True)
class TimestampResponse:
    token: bytes
    tst_info_serial: int
    tst_info_time: datetime
    tsa_url: str


class TimestampAuthority:
    """RFC 3161 Time Stamping Authority client.

    Wraps ``rfc3161ng.RemoteTimestamper`` and exposes a minimal
    ``timestamp`` / ``verify`` interface.  No default TSA URL is
    provided -- callers must supply one explicitly.
    """

    def __init__(
        self,
        tsa_url: str,
        *,
        hash_algorithm: str = "sha256",
        certificate: bytes | None = None,
        timeout: int = 30,
        include_tsa_certificate: bool = True,
    ) -> None:
        if not tsa_url:
            raise ValueError("tsa_url is required")
        self._tsa_url = tsa_url
        self._hash_algorithm = hash_algorithm
        self._certificate = certificate
        self._stamper = rfc3161ng.RemoteTimestamper(
            url=tsa_url,
            hashname=hash_algorithm,
            certificate=certificate,
            include_tsa_certificate=include_tsa_certificate,
            timeout=timeout,
        )

    @property
    def tsa_url(self) -> str:
        return self._tsa_url

    def timestamp(self, digest: bytes) -> TimestampResponse:
        """Send *digest* to the TSA and return a :class:`TimestampResponse`.

        *digest* must be the raw bytes of the hash (not hex-encoded).
        """
        tsr = self._stamper(digest=digest, return_tsr=True)
        token_bytes: bytes = encoder.encode(tsr.time_stamp_token)
        tst_time = rfc3161ng.get_timestamp(tsr.time_stamp_token)
        serial = self._extract_serial(tsr.time_stamp_token)
        return TimestampResponse(
            token=token_bytes,
            tst_info_serial=serial,
            tst_info_time=tst_time,
            tsa_url=self._tsa_url,
        )

    def verify(self, digest: bytes, token: bytes) -> bool:
        """Verify *token* against *digest*.

        Returns ``True`` when verification succeeds.  Raises
        ``rfc3161ng.TimestampingError`` or ``ValueError`` on failure
        when a TSA certificate is available, otherwise returns the
        decoded token (truthy) without cryptographic verification.
        """
        result = rfc3161ng.check_timestamp(
            token,
            digest=digest,
            hashname=self._hash_algorithm,
            certificate=self._certificate,
        )
        return bool(result)

    @staticmethod
    def _extract_serial(tst: rfc3161ng.TimeStampToken) -> int:
        tstinfo_raw = (
            tst.getComponentByName("content")
            .getComponentByPosition(2)
            .getComponentByPosition(1)
        )
        tstinfo_octet, _ = decoder.decode(tstinfo_raw, asn1Spec=univ.OctetString())
        tstinfo, _ = decoder.decode(tstinfo_octet, asn1Spec=rfc3161ng.TSTInfo())
        return int(tstinfo.getComponentByName("serialNumber"))
