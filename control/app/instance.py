"""The ID of this installation (Settings → License): derived from the machine, never stored – e.g. to bind a license to it."""
import hashlib

DMI_UUID = "/sys/class/dmi/id/product_uuid"
# placeholders some boards report instead of a real system UUID
_DUMMY_UUIDS = {"00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "03000200-0400-0500-0006-000700080009", "not settable", "not present", "to be filled by o.e.m."}
_instance = None


def _engine_id():
    try:
        from . import dockerops
        return str(dockerops.client().info().get("ID") or "")
    except Exception:  # noqa: BLE001 – no Docker: no fallback
        return ""


def instance_id():
    """(ID, source) of this installation – TCI-XXXX-XXXX-XXXX-XXXX, a hash of the machine's system UUID from the firmware
    (source "hardware"), or of the Docker engine's ID where the firmware has none ("docker"); ("", "") if neither.
    Worked out on every start and never stored: it can't be set by hand or copied along with the data directory."""
    global _instance
    if _instance is None:
        try:
            with open(DMI_UUID) as f:
                uuid = f.read().strip().lower()
        except OSError:
            uuid = ""
        src, kind = (uuid, "hardware") if uuid and uuid not in _DUMMY_UUIDS else (_engine_id(), "docker")
        if src:
            h = hashlib.sha256(f"tentaclai-instance|{src}".encode()).hexdigest()[:16].upper()
            _instance = ("TCI-" + "-".join(h[i:i + 4] for i in range(0, 16, 4)), kind)
        else:
            _instance = ("", "")
    return _instance
