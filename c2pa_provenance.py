"""Bounded C2PA manifest-store discovery for supported audio containers.

This module locates embedded or adjacent C2PA data. It deliberately does not
claim to validate signatures, trust chains, assertions, or hard bindings.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct


C2PA_SPEC_VERSION = "2.4"
C2PA_POLICY_BLOCK = "block"
C2PA_POLICY_ALLOW_REMOVAL = "allow-removal"
C2PA_POLICIES = frozenset({
    C2PA_POLICY_BLOCK,
    C2PA_POLICY_ALLOW_REMOVAL,
})
C2PA_MEDIA_TYPES = frozenset({
    "application/c2pa",
    "application/x-c2pa-manifest-store",
})
MAX_EMBEDDED_MANIFEST_BYTES = 64 * 1024 * 1024
_OGG_C2PA_IDENTIFIER = b"\x00c2pa"


class C2PAInspectionError(ValueError):
    """The source could not be inspected safely and completely."""


@dataclass(frozen=True, slots=True)
class ManifestStoreEvidence:
    location: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if self.size_bytes <= 0:
            raise ValueError("C2PA manifest store must contain bytes")
        if (
            len(self.sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.sha256)
        ):
            raise ValueError("C2PA manifest store requires a SHA-256 digest")

    def to_dict(self) -> dict:
        return {
            "location": self.location,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class C2PAInspection:
    status: str
    container: str
    manifest_stores: tuple[ManifestStoreEvidence, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        if self.status not in {
            "absent",
            "present_unvalidated",
            "inspection_failed",
        }:
            raise ValueError(f"unknown C2PA inspection status: {self.status}")
        if self.status == "present_unvalidated" and not self.manifest_stores:
            raise ValueError("present C2PA status requires manifest evidence")
        if self.status != "present_unvalidated" and self.manifest_stores:
            raise ValueError("manifest evidence requires present C2PA status")

    @property
    def present(self) -> bool:
        return self.status == "present_unvalidated"

    @property
    def failed(self) -> bool:
        return self.status == "inspection_failed"

    def to_dict(self) -> dict:
        return {
            "spec_version": C2PA_SPEC_VERSION,
            "status": self.status,
            "container": self.container,
            "validation": "not_performed",
            "validation_scope": (
                "embedded and adjacent manifest-store discovery only"
            ),
            "manifest_stores": [
                evidence.to_dict()
                for evidence in self.manifest_stores
            ],
            "message": self.message,
        }


def _sha256_region(handle, offset: int, size: int) -> str:
    if size <= 0 or size > MAX_EMBEDDED_MANIFEST_BYTES:
        raise C2PAInspectionError(
            "embedded C2PA manifest size is outside the 1-byte to "
            f"{MAX_EMBEDDED_MANIFEST_BYTES}-byte inspection limit"
        )
    handle.seek(offset)
    remaining = size
    hasher = hashlib.sha256()
    while remaining:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            raise C2PAInspectionError("truncated C2PA manifest store")
        hasher.update(chunk)
        remaining -= len(chunk)
    return hasher.hexdigest()


def _evidence_for_region(handle, location, offset, size):
    return ManifestStoreEvidence(
        location=location,
        sha256=_sha256_region(handle, offset, size),
        size_bytes=size,
    )


def _scan_riff(path: Path) -> tuple[str, list[ManifestStoreEvidence]]:
    file_size = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(12)
        if (
            len(header) != 12
            or header[:4] not in {b"RIFF", b"RF64"}
            or header[8:12] != b"WAVE"
        ):
            raise C2PAInspectionError("invalid RIFF/WAVE header")
        rf64_data_size = None
        evidence = []
        while handle.tell() < file_size:
            chunk_header = handle.read(8)
            if not chunk_header:
                break
            if len(chunk_header) != 8:
                raise C2PAInspectionError("truncated RIFF chunk header")
            chunk_id, raw_size = struct.unpack("<4sI", chunk_header)
            data_offset = handle.tell()
            chunk_size = raw_size
            if chunk_id == b"ds64" and chunk_size >= 16:
                values = handle.read(16)
                if len(values) != 16:
                    raise C2PAInspectionError("truncated RF64 ds64 chunk")
                _riff_size, rf64_data_size = struct.unpack("<QQ", values)
                handle.seek(data_offset)
            if raw_size == 0xFFFFFFFF:
                if chunk_id == b"data" and rf64_data_size is not None:
                    chunk_size = rf64_data_size
                else:
                    raise C2PAInspectionError(
                        "RF64 chunk has no resolvable 64-bit size"
                    )
            chunk_end = data_offset + chunk_size
            padded_end = chunk_end + (chunk_size & 1)
            if chunk_end > file_size or padded_end > file_size + 1:
                raise C2PAInspectionError("RIFF chunk exceeds source size")
            if chunk_id == b"C2PA":
                evidence.append(_evidence_for_region(
                    handle,
                    "riff:C2PA",
                    data_offset,
                    chunk_size,
                ))
            handle.seek(padded_end)
    return "wav", evidence


def _scan_aiff(path: Path) -> tuple[str, list[ManifestStoreEvidence]]:
    file_size = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(12)
        if (
            len(header) != 12
            or header[:4] != b"FORM"
            or header[8:12] not in {b"AIFF", b"AIFC"}
        ):
            raise C2PAInspectionError("invalid AIFF/AIFC header")
        evidence = []
        while handle.tell() < file_size:
            chunk_header = handle.read(8)
            if not chunk_header:
                break
            if len(chunk_header) != 8:
                raise C2PAInspectionError("truncated AIFF chunk header")
            chunk_id, chunk_size = struct.unpack(">4sI", chunk_header)
            data_offset = handle.tell()
            chunk_end = data_offset + chunk_size
            padded_end = chunk_end + (chunk_size & 1)
            if chunk_end > file_size or padded_end > file_size + 1:
                raise C2PAInspectionError("AIFF chunk exceeds source size")
            if chunk_id == b"C2PA":
                evidence.append(_evidence_for_region(
                    handle,
                    "aiff:C2PA",
                    data_offset,
                    chunk_size,
                ))
            handle.seek(padded_end)
    return "aiff", evidence


def _scan_id3(path: Path, container: str):
    with path.open("rb") as handle:
        header = handle.read(10)
    if len(header) < 10 or header[:3] != b"ID3":
        return container, []
    tag_size = (
        (header[6] << 21)
        | (header[7] << 14)
        | (header[8] << 7)
        | header[9]
    )
    if tag_size > MAX_EMBEDDED_MANIFEST_BYTES:
        raise C2PAInspectionError(
            "ID3 tag exceeds the C2PA inspection limit"
        )
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError

        tags = ID3(path)
    except ID3NoHeaderError:
        return container, []
    except Exception as exc:
        raise C2PAInspectionError(f"ID3 inspection failed: {exc}") from exc

    evidence = []
    for frame in tags.getall("GEOB"):
        media_type = str(getattr(frame, "mime", "")).strip().lower()
        if media_type not in C2PA_MEDIA_TYPES:
            continue
        data = bytes(getattr(frame, "data", b""))
        if not data:
            raise C2PAInspectionError("C2PA ID3 GEOB frame is empty")
        if len(data) > MAX_EMBEDDED_MANIFEST_BYTES:
            raise C2PAInspectionError(
                "C2PA ID3 GEOB frame exceeds the inspection limit"
            )
        evidence.append(ManifestStoreEvidence(
            location=f"id3:GEOB:{media_type}",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        ))
    return container, evidence


def _scan_ogg(path: Path):
    file_size = path.stat().st_size
    evidence = []
    first_packets: dict[int, bytearray | None] = {}
    saw_non_bos_page = False
    with path.open("rb") as handle:
        while handle.tell() < file_size:
            header = handle.read(27)
            if not header:
                break
            if len(header) != 27 or header[:4] != b"OggS":
                raise C2PAInspectionError("invalid Ogg page header")
            if header[4] != 0:
                raise C2PAInspectionError("unsupported Ogg bitstream version")
            header_type = header[5]
            serial = struct.unpack("<I", header[14:18])[0]
            segment_count = header[26]
            lacing = handle.read(segment_count)
            if len(lacing) != segment_count:
                raise C2PAInspectionError("truncated Ogg lacing table")
            payload_size = sum(lacing)
            payload = handle.read(payload_size)
            if len(payload) != payload_size:
                raise C2PAInspectionError("truncated Ogg page payload")

            is_bos = bool(header_type & 0x02)
            if is_bos:
                first_packets.setdefault(serial, bytearray())
            else:
                saw_non_bos_page = True
            packet = first_packets.get(serial)
            payload_offset = 0
            for segment_size in lacing:
                segment = payload[
                    payload_offset:payload_offset + segment_size
                ]
                payload_offset += segment_size
                if packet is None:
                    continue
                packet.extend(segment)
                if len(packet) > MAX_EMBEDDED_MANIFEST_BYTES + len(
                    _OGG_C2PA_IDENTIFIER
                ):
                    raise C2PAInspectionError(
                        "Ogg first packet exceeds the C2PA inspection limit"
                    )
                if segment_size < 255:
                    if packet.startswith(_OGG_C2PA_IDENTIFIER):
                        manifest = bytes(packet[len(_OGG_C2PA_IDENTIFIER):])
                        if not manifest:
                            raise C2PAInspectionError(
                                "Ogg C2PA logical stream is empty"
                            )
                        evidence.append(ManifestStoreEvidence(
                            location=f"ogg:stream-{serial}",
                            sha256=hashlib.sha256(manifest).hexdigest(),
                            size_bytes=len(manifest),
                        ))
                    first_packets[serial] = None
                    packet = None
            if (
                saw_non_bos_page
                and first_packets
                and all(value is None for value in first_packets.values())
            ):
                break
    return "ogg", evidence


def _scan_embedded(path: Path):
    with path.open("rb") as handle:
        magic = handle.read(12)
    suffix = path.suffix.lower()
    if (
        len(magic) >= 12
        and magic[:4] in {b"RIFF", b"RF64"}
        and magic[8:12] == b"WAVE"
    ):
        return _scan_riff(path)
    if (
        len(magic) >= 12
        and magic[:4] == b"FORM"
        and magic[8:12] in {b"AIFF", b"AIFC"}
    ):
        return _scan_aiff(path)
    if magic[:4] == b"OggS":
        return _scan_ogg(path)
    if suffix in {".mp3", ".flac"} or magic[:3] == b"ID3":
        return _scan_id3(path, suffix.lstrip(".") or "id3")
    return suffix.lstrip(".") or "unknown", []


def _adjacent_manifest(path: Path):
    candidate = path.with_suffix(".c2pa")
    if candidate == path or not candidate.is_file():
        return None
    size = candidate.stat().st_size
    if size <= 0:
        raise C2PAInspectionError("adjacent C2PA manifest store is empty")
    if size > MAX_EMBEDDED_MANIFEST_BYTES:
        raise C2PAInspectionError(
            "adjacent C2PA manifest store exceeds the inspection limit"
        )
    hasher = hashlib.sha256()
    with candidate.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return ManifestStoreEvidence(
        location="adjacent:.c2pa",
        sha256=hasher.hexdigest(),
        size_bytes=size,
    )


def inspect_c2pa(path) -> C2PAInspection:
    """Locate C2PA stores without modifying the asset or using the network."""
    source = Path(path)
    container = source.suffix.lower().lstrip(".") or "unknown"
    try:
        if not source.is_file():
            raise C2PAInspectionError("source file is unavailable")
        container, embedded = _scan_embedded(source)
        adjacent = _adjacent_manifest(source)
        evidence = list(embedded)
        if adjacent is not None:
            evidence.append(adjacent)
        if not evidence:
            return C2PAInspection(
                status="absent",
                container=container,
                message=(
                    "No embedded or adjacent C2PA manifest store was found."
                ),
            )
        locations = ", ".join(item.location for item in evidence)
        return C2PAInspection(
            status="present_unvalidated",
            container=container,
            manifest_stores=tuple(evidence),
            message=(
                f"Found {len(evidence)} C2PA manifest store(s) at "
                f"{locations}; cryptographic validation was not performed."
            ),
        )
    except (OSError, C2PAInspectionError, ValueError) as exc:
        return C2PAInspection(
            status="inspection_failed",
            container=container,
            message=str(exc),
        )
